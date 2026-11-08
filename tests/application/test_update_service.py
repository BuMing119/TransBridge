"""Offline contracts for checking and downloading official Windows releases."""

from hashlib import sha256
from pathlib import Path

import pytest
import requests

from transbridge.application.update_service import DownloadCancelled, UpdateError, check_latest, download_release

VERSION = "0.2.1.10"
NAME = f"TransBridge_v{VERSION}_Setup.exe"
ASSET_URL = f"https://github.com/BuMing119/TransBridge/releases/download/v{VERSION}/{NAME}"
SIDECAR_URL = ASSET_URL + ".sha256"
CONTENT = b"verified installer content"
DIGEST = sha256(CONTENT).hexdigest()


class FakeResponse:
    def __init__(self, *, payload=None, content=b"", status_code=200):
        self.payload = payload
        self.content = content
        self.status_code = status_code
        self.closed = False

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} response")

    def iter_content(self, chunk_size):
        yield from (self.content[index : index + chunk_size] for index in range(0, len(self.content), chunk_size))

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def _payload(*, version=VERSION, digest=f"sha256:{DIGEST}", asset_url=ASSET_URL, size=len(CONTENT)):
    installer_name = f"TransBridge_v{version}_Setup.exe"
    assets = [{"name": installer_name, "browser_download_url": asset_url, "size": size, "digest": digest}]
    if digest is None:
        assets.append({"name": installer_name + ".sha256", "browser_download_url": SIDECAR_URL})
    return {
        "tag_name": f"v{version}",
        "body": "Fixed translation imports",
        "html_url": f"https://github.com/BuMing119/TransBridge/releases/tag/v{version}",
        "draft": False,
        "prerelease": False,
        "assets": assets,
    }


def test_no_release_and_older_version_are_not_offered() -> None:
    assert check_latest("0.2.1.9", session=FakeSession(FakeResponse(status_code=404))) is None
    assert check_latest("0.2.1.10", session=FakeSession(FakeResponse(payload=_payload()))) is None
    assert check_latest("0.2.1.11", session=FakeSession(FakeResponse(payload=_payload()))) is None


def test_offers_verified_newer_release() -> None:
    session = FakeSession(FakeResponse(payload=_payload()))

    release = check_latest("0.2.1.9", session=session)

    assert release is not None
    assert release.version == VERSION
    assert release.sha256 == DIGEST
    assert release.size == len(CONTENT)
    assert release.notes == "Fixed translation imports"
    assert release.asset_url == ASSET_URL
    assert session.calls[0][1]["timeout"] == (5, 15)


def test_uses_exact_sidecar_when_api_has_no_digest() -> None:
    session = FakeSession(
        FakeResponse(payload=_payload(digest=None)),
        FakeResponse(content=f"{DIGEST}  {NAME}\n".encode("ascii")),
    )

    release = check_latest("0.2.1.9", session=session)

    assert release is not None and release.sha256 == DIGEST
    assert session.calls[1][0] == SIDECAR_URL


@pytest.mark.parametrize(
    "payload",
    [
        _payload(asset_url="https://evil.example/TransBridge.exe"),
        _payload(asset_url=ASSET_URL.replace("BuMing119", "other-user")),
        _payload(digest="sha256:missing"),
        _payload(size=0),
        _payload(version="0.2.1-beta"),
    ],
)
def test_rejects_invalid_release_metadata(payload) -> None:
    with pytest.raises(UpdateError):
        check_latest("0.2.1.9", session=FakeSession(FakeResponse(payload=payload)))


def test_download_verifies_size_and_digest_before_returning(tmp_path: Path) -> None:
    release = check_latest("0.2.1.9", session=FakeSession(FakeResponse(payload=_payload())))
    assert release is not None
    progress = []

    target = download_release(
        release,
        tmp_path,
        session=FakeSession(FakeResponse(content=CONTENT)),
        progress=lambda done, total: progress.append((done, total)),
    )

    assert target.read_bytes() == CONTENT
    assert target.name == NAME
    assert list(tmp_path.glob("*.part")) == []
    assert progress == [(len(CONTENT), len(CONTENT))]
    assert download_release(release, tmp_path, session=FakeSession()) == target


@pytest.mark.parametrize("content", [b"tampered installer content", CONTENT[:-1]])
def test_invalid_download_is_removed(tmp_path: Path, content: bytes) -> None:
    release = check_latest("0.2.1.9", session=FakeSession(FakeResponse(payload=_payload())))
    assert release is not None

    with pytest.raises(UpdateError, match="校验|大于"):
        download_release(release, tmp_path, session=FakeSession(FakeResponse(content=content)))

    assert list(tmp_path.iterdir()) == []


def test_cancelled_download_is_removed(tmp_path: Path) -> None:
    release = check_latest("0.2.1.9", session=FakeSession(FakeResponse(payload=_payload())))
    assert release is not None

    with pytest.raises(DownloadCancelled):
        download_release(release, tmp_path, session=FakeSession(FakeResponse(content=CONTENT)), cancelled=lambda: True)

    assert list(tmp_path.iterdir()) == []


def test_corrupt_cached_installer_can_be_downloaded_again(tmp_path: Path) -> None:
    release = check_latest("0.2.1.9", session=FakeSession(FakeResponse(payload=_payload())))
    assert release is not None
    target = tmp_path / NAME
    target.write_bytes(b"broken cache")

    result = download_release(release, tmp_path, session=FakeSession(FakeResponse(content=CONTENT)))

    assert result == target
    assert target.read_bytes() == CONTENT
