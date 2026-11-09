# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Fetch and verify public TransBridge Windows releases without UI dependencies."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import unquote, urlsplit

import requests

_REPOSITORY = "BuMing119/TransBridge"
_LATEST_URL = f"https://api.github.com/repos/{_REPOSITORY}/releases/latest"
_VERSION = re.compile(r"(?:v)?(\d+)\.(\d+)\.(\d+)\.(\d+)\Z")
_DIGEST = re.compile(r"sha256:([0-9a-fA-F]{64})\Z")
_SIDE_CAR = re.compile(r"([0-9a-fA-F]{64})  (TransBridge_v\d+\.\d+\.\d+\.\d+_Setup\.exe)\s*\Z")
_MAX_INSTALLER_SIZE = 2 * 1024 * 1024 * 1024 - 1  # ApiWorker.progress carries signed Qt ints.
_HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "TransBridge-Updater"}


class UpdateError(Exception):
    """A release cannot be trusted, fetched or downloaded."""


class DownloadCancelled(UpdateError):
    """The caller cancelled a download before installation."""


@dataclass(frozen=True, slots=True)
class ReleaseInfo:
    version: str
    notes: str
    html_url: str
    asset_url: str
    sha256: str
    size: int

    @property
    def installer_name(self) -> str:
        return f"TransBridge_v{self.version}_Setup.exe"


def _version_parts(value: str) -> tuple[int, int, int, int]:
    match = _VERSION.fullmatch(value)
    if match is None:
        raise UpdateError(f"无法识别软件版本：{value!r}")
    return tuple(int(part) for part in match.groups())


def _asset_url(url: object, version: str, name: str) -> str:
    if not isinstance(url, str):
        raise UpdateError("更新包缺少下载地址")
    parsed = urlsplit(url)
    expected = f"/{_REPOSITORY}/releases/download/v{version}/{name}"
    if (
        parsed.scheme != "https"
        or parsed.netloc != "github.com"
        or unquote(parsed.path) != expected
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise UpdateError("更新包下载地址与官方发布仓库不匹配")
    return url


def _release_page_url(url: object, version: str) -> str:
    if not isinstance(url, str):
        raise UpdateError("发布信息缺少页面地址")
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "github.com"
        or parsed.path != f"/{_REPOSITORY}/releases/tag/v{version}"
        or parsed.query
        or parsed.fragment
    ):
        raise UpdateError("发布页面地址与官方仓库不匹配")
    return url


def _fetch_sidecar(session, url: str, installer_name: str) -> str:
    try:
        response = session.get(url, timeout=(5, 30), stream=True)
        try:
            response.raise_for_status()
            content = bytearray()
            for chunk in response.iter_content(chunk_size=256):
                content.extend(chunk)
                if len(content) > 256:
                    raise UpdateError("更新包校验文件过大")
        finally:
            response.close()
    except requests.RequestException as exc:
        raise UpdateError("无法下载更新包校验文件") from exc
    try:
        text = content.decode("ascii")
    except UnicodeDecodeError as exc:
        raise UpdateError("更新包校验文件格式无效") from exc
    match = _SIDE_CAR.fullmatch(text)
    if match is None or match.group(2) != installer_name:
        raise UpdateError("更新包校验文件格式或文件名无效")
    return match.group(1).lower()


def check_latest(current_version: str, *, session=None) -> ReleaseInfo | None:
    """Return a newer stable release, or ``None`` when none has been published."""

    current_parts = _version_parts(current_version)
    owns_session = session is None
    session = session or requests.Session()
    try:
        try:
            response = session.get(_LATEST_URL, headers=_HEADERS, timeout=(5, 15))
            try:
                if response.status_code == 404:
                    return None
                response.raise_for_status()
                payload = response.json()
            finally:
                response.close()
        except (requests.RequestException, ValueError) as exc:
            raise UpdateError("检查更新失败，请检查网络连接或稍后重试") from exc
        if not isinstance(payload, dict) or payload.get("draft") or payload.get("prerelease"):
            raise UpdateError("GitHub 返回了无效的正式版本信息")
        tag = payload.get("tag_name")
        if not isinstance(tag, str) or not tag.startswith("v"):
            raise UpdateError("发布版本标签无效")
        version = tag[1:]
        if _version_parts(version) <= current_parts:
            return None
        installer_name = f"TransBridge_v{version}_Setup.exe"
        assets = payload.get("assets")
        if not isinstance(assets, list):
            raise UpdateError("发布信息缺少安装包列表")
        installer_assets = [
            asset for asset in assets if isinstance(asset, dict) and asset.get("name") == installer_name
        ]
        if len(installer_assets) != 1:
            raise UpdateError("新版本缺少唯一的 Windows 安装包")
        installer = installer_assets[0]
        asset_url = _asset_url(installer.get("browser_download_url"), version, installer_name)
        size = installer.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= _MAX_INSTALLER_SIZE:
            raise UpdateError("更新包大小无效")
        digest = installer.get("digest")
        if digest is not None:
            if not isinstance(digest, str) or (match := _DIGEST.fullmatch(digest)) is None:
                raise UpdateError("更新包 SHA-256 摘要无效")
            checksum = match.group(1).lower()
        else:
            sidecar_name = installer_name + ".sha256"
            sidecars = [asset for asset in assets if isinstance(asset, dict) and asset.get("name") == sidecar_name]
            if len(sidecars) != 1:
                raise UpdateError("新版本缺少 SHA-256 校验信息")
            sidecar_url = _asset_url(sidecars[0].get("browser_download_url"), version, sidecar_name)
            checksum = _fetch_sidecar(session, sidecar_url, installer_name)
        notes = payload.get("body")
        return ReleaseInfo(
            version=version,
            notes=notes if isinstance(notes, str) else "",
            html_url=_release_page_url(payload.get("html_url"), version),
            asset_url=asset_url,
            sha256=checksum,
            size=size,
        )
    finally:
        if owns_session:
            session.close()


def download_release(
    release: ReleaseInfo,
    destination_dir: Path,
    *,
    session=None,
    progress: Callable[[int, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> Path:
    """Stream and verify the installer before making it available to the caller."""

    _version_parts(release.version)
    _asset_url(release.asset_url, release.version, release.installer_name)
    if not _DIGEST.fullmatch(f"sha256:{release.sha256}") or not 0 < release.size <= _MAX_INSTALLER_SIZE:
        raise UpdateError("更新包校验信息无效")
    destination_dir = Path(destination_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)
    target = destination_dir / release.installer_name
    if target.exists():
        if (
            target.is_file()
            and target.stat().st_size == release.size
            and _file_sha256(target) == release.sha256.lower()
        ):
            return target
        if not target.is_file():
            raise UpdateError("下载目录已有同名路径，无法保存安装包")
        target.unlink()
    owns_session = session is None
    session = session or requests.Session()
    temporary: Path | None = None
    try:
        if cancelled is not None and cancelled():
            raise DownloadCancelled("已取消更新包下载")
        try:
            response = session.get(release.asset_url, timeout=(5, 30), stream=True)
            try:
                response.raise_for_status()
                digest = sha256()
                received = 0
                with tempfile.NamedTemporaryFile(
                    dir=destination_dir, prefix=".transbridge-update-", suffix=".part", delete=False
                ) as output:
                    temporary = Path(output.name)
                    for chunk in response.iter_content(chunk_size=256 * 1024):
                        if cancelled is not None and cancelled():
                            raise DownloadCancelled("已取消更新包下载")
                        if not chunk:
                            continue
                        received += len(chunk)
                        if received > release.size:
                            raise UpdateError("下载的更新包大于发布信息声明的大小")
                        output.write(chunk)
                        digest.update(chunk)
                        if progress is not None:
                            progress(received, release.size)
            finally:
                response.close()
        except requests.RequestException as exc:
            raise UpdateError("下载更新包失败，请检查网络连接后重试") from exc
        if received != release.size or digest.hexdigest() != release.sha256.lower():
            raise UpdateError("更新包大小或 SHA-256 校验失败")
        if target.exists():
            raise UpdateError("下载目录出现同名安装包，请重新检查")
        os.replace(temporary, target)
        temporary = None
        return target
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        if owns_session:
            session.close()


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(256 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
