import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.request import Request

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from eegapp.core import updater


class Response(io.BytesIO):
    def __init__(self, data, length=None):
        super().__init__(data)
        self.headers = {} if length is None else {"Content-Length": str(length)}


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.signer = Ed25519PrivateKey.generate()
        key = self.signer.public_key().public_bytes_raw()
        setting = patch.object(updater, "PUBLIC_KEY_B64", base64.b64encode(key).decode("ascii"))
        setting.start()
        self.addCleanup(setting.stop)
        self.data = b"Example installer" * 20_000
        self.urls = ["https://example.org/package", "https://mirror.example.org/package"]
        self.asset = dict(urls=self.urls, size=len(self.data), sha256=hashlib.sha256(self.data).hexdigest())
        self.feed = "https://publisher.example.org/latest.json"

    def envelope(self, version="2.5.8", asset=None, signer=None):
        payload = json.dumps({"schema": 1, "version": version, "assets": {
            "windows-x64": self.asset if asset is None else asset}}).encode("utf-8")
        signature = (signer or self.signer).sign(payload)
        return json.dumps({"payload": base64.b64encode(payload).decode("ascii"),
                           "signature": base64.b64encode(signature).decode("ascii")}).encode("utf-8")

    def check(self, feeds, current="2.5.7"):
        def route(url, timeout, accept):
            content = feeds[url]
            if isinstance(content, Exception):
                raise content
            return Response(content)
        with patch.object(updater, "_open", side_effect=route):
            return updater.check_for_update(current, sources=list(feeds), platform_name="windows-x64")

    def test_stable_versions_and_platform(self):
        self.assertGreater(updater.version_key("v2.10.0"), updater.version_key("2.9.10"))
        for text in ["2.5.8-beta.1", "2.5", "", "02.5.8", "2.5.8/../x", 42]:
            with self.subTest(text=text), self.assertRaises(updater.UpdateError):
                updater.version_key(text)
        for system, machine, expected in [("Windows", "AMD64", "windows-x64"), ("Darwin", "arm64", "macos-arm64"),
                                           ("Darwin", "x86_64", "macos-x86_64")]:
            self.assertEqual(updater.platform_key(system, machine), expected)
        with self.assertRaises(updater.UpdateError):
            updater.platform_key("Windows", "i386")

    def test_signed_feeds_include_offline_fresh_and_cached_routes(self):
        update = self.check({"https://offline.example.org/": TimeoutError(), self.feed: self.envelope("2.5.7"),
                             "https://fresh.example.org/": self.envelope("2.10.0")})
        self.assertEqual((update.version, update.source, update.asset.name), (
            "2.10.0", "https://fresh.example.org/", "BIAP-2.10.0-Windows-x64.exe"))
        self.assertEqual(update.asset.urls, tuple(self.urls))
        self.assertIsNone(self.check({self.feed: self.envelope("2.5.7")}))
        self.assertIsNone(self.check({self.feed: self.envelope("2.5.1")}))

    def test_invalid_or_untrusted_manifest_is_not_an_update(self):
        invalid = json.loads(self.envelope())
        invalid["payload"] = base64.b64encode(b'{"version":"99.9.9"}').decode("ascii")
        for blob in [json.dumps(invalid).encode(), self.envelope(signer=Ed25519PrivateKey.generate()),
                     b'{"schema":1,"version":"7.0.0"}', b"x"*300_000,
                     self.envelope(asset=dict(self.asset, size=-1)), self.envelope(asset=dict(self.asset, sha256="x")),
                     self.envelope(asset=dict(self.asset, urls=["http://mirror.example.org/a"]))]:
            with self.subTest(blob=blob[:60]), self.assertRaises(updater.UpdateError):
                self.check({self.feed: blob})
        update = self.check({"https://broken.example.org/": b"offline", self.feed: self.envelope()})
        self.assertEqual(update.source, self.feed)

    def test_https_only_including_redirects(self):
        for destination in ["file:///asset", "http://mirror.example.org/asset", "https://me:pwd@example.org/a",
                            "https://example.org\\@other.example.org/"]:
            with self.subTest(destination=destination), self.assertRaises(updater.UpdateError):
                updater._HttpsRedirect().redirect_request(Request(self.feed), None, 302, "", {}, destination)

    def test_download_retries_wrong_hash_then_reuses_valid_cache(self):
        update = self.check({self.feed: self.envelope()})
        calls, progress = [], []
        def route(url, timeout, accept):
            calls.append((url, accept))
            data = self.data if url == self.urls[-1] else b"#" * len(self.data)
            return Response(data, len(data))
        with tempfile.TemporaryDirectory() as temp, patch.object(updater, "_open", side_effect=route):
            result = updater.download_update(update, temp, progress=lambda done, size: progress.append((done, size)))
            self.assertEqual(result.read_bytes(), self.data)
            self.assertTrue(updater.verify_file(result, update))
            self.assertEqual(result, updater.download_update(update, temp))
            self.assertEqual(calls, [(url, "application/octet-stream") for url in self.urls])
            self.assertEqual(progress[-1], (len(self.data), len(self.data)))
            self.assertEqual(list(Path(temp).iterdir()), [result])

    def test_interrupted_or_corrupt_transfers_never_replace_existing_file(self):
        update = self.check({self.feed: self.envelope()})
        for wrong, header in [(b"short", None), (self.data+b"extra", None), (self.data, len(self.data)+50)]:
            with self.subTest(size=len(wrong), header=header), tempfile.TemporaryDirectory() as temp:
                destination = Path(temp)/update.asset.name
                destination.write_bytes(b"existing")
                with patch.object(updater, "_open", side_effect=lambda *_: Response(wrong, header)), self.assertRaises(updater.UpdateError):
                    updater.download_update(update, temp)
                self.assertEqual(destination.read_bytes(), b"existing")
                self.assertEqual(list(destination.parent.iterdir()), [destination])

    def test_cancellation_discards_partial_download(self):
        update = self.check({self.feed: self.envelope()})
        progress = []
        with tempfile.TemporaryDirectory() as temp, patch.object(updater, "_open", side_effect=lambda *_: Response(self.data)):
            with self.assertRaises(updater.UpdateCancelled):
                updater.download_update(update, temp, progress=lambda done, total: progress.append(done),
                                        cancelled=lambda: any(progress))
            self.assertEqual(list(Path(temp).iterdir()), [])
        with self.assertRaises(updater.UpdateCancelled):
            updater.check_for_update("2.5.7", platform_name="windows-x64", cancelled=lambda: True)

    def test_simultaneous_downloads_share_only_verified_result(self):
        update = self.check({self.feed: self.envelope()})
        with tempfile.TemporaryDirectory() as temp, patch.object(updater, "_open", side_effect=lambda *_: Response(self.data)):
            with ThreadPoolExecutor(2) as pool:
                results = list(pool.map(lambda _: updater.download_update(update, temp), [1, 2]))
            self.assertEqual(results[0], results[1])
            self.assertTrue(updater.verify_file(results[0], update))
            self.assertEqual(list(Path(temp).iterdir()), [results[0]])


if __name__ == "__main__":
    unittest.main()
