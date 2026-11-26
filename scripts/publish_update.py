"""Sign the latest complete BIAP release for the desktop update feed."""
import argparse
import base64
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from eegapp.core.update_key import PUBLIC_KEY_B64
from eegapp.core.updater import ASSET_NAMES, version_key

REPO = "ngreat222/eeg-analyzer"


def release_payload(release):
    tag = release["tag_name"]
    if release["draft"] or release["prerelease"] or not tag.startswith("v"):
        raise ValueError("Expected a published stable release.")
    version = tag[1:]
    version_key(version)
    available = {asset["name"]: asset for asset in release["assets"]}
    assets = {}
    for platform, template in ASSET_NAMES.items():
        name = template.format(version)
        asset = available.get(name)
        if not asset or asset["state"] != "uploaded" or not asset.get("digest"):
            print(f"Waiting for {name}.")
            return None
        official = f"https://github.com/{REPO}/releases/download/{tag}/{name}"
        api = f"https://api.github.com/repos/{REPO}/releases/assets/{int(asset['id'])}"
        digest = re.fullmatch(r"sha256:([0-9a-f]{64})", asset["digest"])
        if not digest or asset["size"] <= 0 or asset["browser_download_url"] != official or asset["url"] != api:
            raise ValueError(f"Unexpected metadata for {name}.")
        assets[platform] = {"size": asset["size"], "sha256": digest[1],
                            "urls": [official, api, "https://gh-proxy.com/" + official]}
    return {"schema": 1, "version": version, "assets": assets}


def write_feed(release, target):
    payload = release_payload(release)
    if payload is None:
        return
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(os.environ["BIAP_UPDATE_SIGNING_KEY"], validate=True))
    if key.public_key().public_bytes_raw() != base64.b64decode(PUBLIC_KEY_B64, validate=True):
        raise ValueError("Signing key does not match the application's verification key.")
    if target.exists():
        previous = json.loads(target.read_bytes())
        data = base64.b64decode(previous["payload"], validate=True)
        key.public_key().verify(base64.b64decode(previous["signature"], validate=True), data)
        old = json.loads(data)
        if version_key(old["version"]) > version_key(payload["version"]):
            raise ValueError("Refusing to roll back the current release.")
        if old == payload:
            print(f"Feed already contains {payload['version']}.")
            return
    data = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    envelope = {"payload": base64.b64encode(data).decode(), "signature": base64.b64encode(key.sign(data)).decode()}
    target.write_text(json.dumps(envelope) + "\n", encoding="utf-8")
    print(f"Signed BIAP {payload['version']} for {len(payload['assets'])} platforms.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    release = subprocess.check_output(["gh", "api", f"repos/{REPO}/releases/latest"])
    write_feed(json.loads(release), args.target)
