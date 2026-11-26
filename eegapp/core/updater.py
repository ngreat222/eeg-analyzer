"""Read signed BIAP release feeds and download the verified installer for this machine."""
import base64
import binascii
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import http.client
import json
import os
from pathlib import Path
import platform
import re
import tempfile
from urllib import request
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .update_key import PUBLIC_KEY_B64


DEFAULT_SOURCES = (
    "https://cdn.jsdelivr.net/gh/ngreat222/eeg-analyzer@update-feed/latest.json",
    "https://raw.githubusercontent.com/ngreat222/eeg-analyzer/update-feed/latest.json",
    "https://api.github.com/repos/ngreat222/eeg-analyzer/contents/latest.json?ref=update-feed",
    "https://gh-proxy.com/https://raw.githubusercontent.com/ngreat222/eeg-analyzer/update-feed/latest.json",
)
ASSET_NAMES = {
    "windows-x64": "BIAP-{}-Windows-x64.exe",
    "macos-arm64": "eeg-analyzer-{}-macos-arm64.dmg",
    "macos-x86_64": "eeg-analyzer-{}-macos-x86_64.dmg",
}
_MAX_FEED = 256 * 1024
_MAX_ASSET = 4 * 1024**3
_BLOCK = 256 * 1024


class UpdateError(Exception):
    """A release could not be checked or downloaded."""


class UpdateCancelled(UpdateError):
    """The user stopped the operation."""


@dataclass(frozen=True)
class Asset:
    name: str
    size: int
    sha256: str
    urls: tuple[str, ...]


@dataclass(frozen=True)
class Update:
    version: str
    asset: Asset
    source: str


def version_key(version):
    """Numeric ordering of stable BIAP versions, with an optional Git tag prefix."""
    if not isinstance(version, str) or not re.fullmatch(r"v?(?:0|[1-9][0-9]{0,8})(?:\.(?:0|[1-9][0-9]{0,8})){2}", version):
        raise UpdateError("版本号无效，需要正式版版本号，例如 2.5.8。")
    return tuple(int(part) for part in version.removeprefix("v").split("."))


def platform_key(system=None, machine=None):
    system = (system or platform.system()).lower()
    machine = (machine or platform.machine()).lower()
    if system == "windows" and machine in ("amd64", "x86_64", "x64"):
        return "windows-x64"
    if system == "darwin":
        if machine in ("arm64", "aarch64"):
            return "macos-arm64"
        if machine in ("amd64", "x86_64"):
            return "macos-x86_64"
    raise UpdateError(f"此系统还没有自动更新安装包（{system} / {machine}）。")


def _https_url(value):
    if not isinstance(value, str) or len(value) > 4096 or any(c.isspace() or ord(c) < 32 or c == "\\" for c in value):
        raise UpdateError("更新地址无效。")
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.hostname or parts.username is not None or parts.password is not None or parts.fragment:
        raise UpdateError("更新地址必须使用 HTTPS。")
    return value


class _HttpsRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, _https_url(newurl))


def _open(url, timeout, accept):
    req = request.Request(_https_url(url), headers={"User-Agent": "BIAP-Update/1", "Accept": accept})
    return request.build_opener(_HttpsRedirect()).open(req, timeout=timeout)


def _cancel_if_requested(cancelled):
    if cancelled and cancelled():
        raise UpdateCancelled("已取消更新。")


def _read_manifest(source, key, current, timeout, cancelled):
    _cancel_if_requested(cancelled)
    accept = "application/vnd.github.raw+json" if urlsplit(source).hostname == "api.github.com" else "application/json"
    with _open(source, timeout, accept) as response:
        data = response.read(_MAX_FEED + 1)
    if len(data) > _MAX_FEED:
        raise UpdateError("更新清单过大。")
    try:
        envelope = json.loads(data)
        payload = base64.b64decode(envelope["payload"], validate=True)
        signature = base64.b64decode(envelope["signature"], validate=True)
        Ed25519PublicKey.from_public_bytes(base64.b64decode(PUBLIC_KEY_B64, validate=True)).verify(signature, payload)
    except (ValueError, TypeError, KeyError, InvalidSignature, binascii.Error) as exc:
        raise UpdateError("更新清单未通过发布签名校验。") from exc
    try:
        manifest = json.loads(payload)
        if type(manifest["schema"]) is not int or manifest["schema"] != 1:
            raise UpdateError("更新清单格式暂不支持。")
        version = manifest["version"]
        if version_key(version) <= current:
            return None
        version = version.removeprefix("v")
        fields = manifest["assets"][key]
        size, digest, urls = fields["size"], fields["sha256"], fields["urls"]
        if type(size) is not int or not 0 < size <= _MAX_ASSET:
            raise UpdateError("安装包大小无效。")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            raise UpdateError("安装包校验值无效。")
        if not isinstance(urls, list) or not 1 <= len(urls) <= 16:
            raise UpdateError("缺少安装包下载地址。")
        urls = tuple(dict.fromkeys(_https_url(url) for url in urls))
        return Update(version, Asset(ASSET_NAMES[key].format(version), size, digest.lower(), urls), source)
    except (ValueError, KeyError, TypeError) as exc:
        raise UpdateError("更新清单缺少适合本机的安装包或包含无效字段。") from exc


def check_for_update(current_version, *, sources=DEFAULT_SOURCES, platform_name=None, timeout=7, cancelled=None):
    """Check independent routes concurrently, so a stale or unreachable CDN cannot mask a release."""
    current = version_key(current_version)
    key = platform_name or platform_key()
    if key not in ASSET_NAMES:
        raise UpdateError("此平台没有自动更新安装包。")
    _cancel_if_requested(cancelled)
    sources = tuple(dict.fromkeys(sources))
    if not sources:
        raise UpdateError("没有可用的更新源。")
    latest = None
    reached = False
    errors = []
    with ThreadPoolExecutor(max_workers=min(4, len(sources))) as pool:
        tasks = [pool.submit(_read_manifest, url, key, current, timeout, cancelled) for url in sources]
        for url, task in zip(sources, tasks):
            try:
                candidate = task.result()
            except UpdateCancelled:
                raise
            except (UpdateError, OSError, ValueError, http.client.HTTPException) as exc:
                errors.append(f"{urlsplit(url).hostname or '更新源'}：{exc}")
                continue
            reached = True
            if candidate and (latest is None or version_key(candidate.version) > version_key(latest.version)):
                latest = candidate
    _cancel_if_requested(cancelled)
    if not reached:
        raise UpdateError("暂时无法获取已验证的更新清单。\n" + "\n".join(errors))
    return latest


def verify_file(path, update, *, cancelled=None):
    path = Path(path)
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size != update.asset.size:
            return False
        digest = hashlib.sha256()
        with path.open("rb") as file:
            while block := file.read(_BLOCK):
                _cancel_if_requested(cancelled)
                digest.update(block)
        return digest.hexdigest() == update.asset.sha256
    except OSError:
        return False


def download_update(update, directory, *, progress=None, cancelled=None, timeout=15):
    """Download into a unique temporary file and publish only the complete, matching installer."""
    asset = update.asset
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / asset.name
    _cancel_if_requested(cancelled)
    if verify_file(destination, update, cancelled=cancelled):
        if progress:
            progress(asset.size, asset.size)
        return destination
    errors = []
    for url in asset.urls:
        _cancel_if_requested(cancelled)
        temporary = None
        try:
            with _open(url, timeout, "application/octet-stream") as response:
                length = response.headers.get("Content-Length")
                if length is not None and int(length) != asset.size:
                    raise UpdateError("下载大小与发布清单不一致。")
                with tempfile.NamedTemporaryFile(dir=directory, prefix=".biap-", suffix=".part", delete=False) as file:
                    temporary = Path(file.name)
                    received = 0
                    digest = hashlib.sha256()
                    if progress:
                        progress(0, asset.size)
                    while block := response.read(_BLOCK):
                        _cancel_if_requested(cancelled)
                        received += len(block)
                        if received > asset.size:
                            raise UpdateError("下载内容超过清单指定的大小。")
                        file.write(block)
                        digest.update(block)
                        if progress:
                            progress(received, asset.size)
            if received != asset.size or digest.hexdigest() != asset.sha256:
                raise UpdateError("安装包不完整或 SHA-256 校验失败。")
            _cancel_if_requested(cancelled)
            os.replace(temporary, destination)
            return destination
        except UpdateCancelled:
            raise
        except (UpdateError, OSError, ValueError, http.client.HTTPException) as exc:
            errors.append(f"{urlsplit(url).hostname}：{exc}")
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
    raise UpdateError("未能下载并校验安装包。\n" + "\n".join(errors))
