"""Fresh exports must become visible before any ZIP is downloaded."""

from pathlib import Path
from threading import Event

import pytest

from transbridge.application.ports.paratranz import ExternalServiceCategory, ExternalServiceError
from transbridge.paratranz.artifact_download import FreshArtifactDownload
from transbridge.paratranz.file_transport import UploadCancellation

OLD = {"id": 1, "createdAt": "2026-11-03T00:00:00Z"}
NEW = {"id": 2, "createdAt": "2026-11-03T00:00:01Z"}


class API:
    def __init__(self, values, *, fail=None, job=None):
        self.values = iter(values)
        self.fail, self.job = fail, job
        self.calls = []
        self.closed = False

    def get_latest_artifact(self, *args, **kwargs):
        self.calls.append("latest")
        return next(self.values)

    def trigger_export(self, *args, **kwargs):
        self.calls.append("trigger")
        if self.fail:
            raise self.fail
        return {"id": 12, "status": 0} if self.job is None else self.job

    def download_artifacts(self, project, destination, **kwargs):
        self.calls.append("download")
        Path(destination).write_bytes(b"synthetic")

    def close(self):
        self.closed = True


def downloader(api, max_polls=3):
    return FreshArtifactDownload(object(), api_factory=lambda **_: api, poll_interval=0.001, max_polls=max_polls)


def test_waits_for_new_export_and_checks_identity_after_download(tmp_path):
    api = API([OLD, OLD, NEW, NEW])
    destination = tmp_path / "export.zip"
    messages = []
    downloader(api).download(7, destination, progress=messages.append)
    assert destination.read_bytes() == b"synthetic"
    assert api.calls == ["latest", "trigger", "latest", "latest", "download", "latest"]
    assert api.closed and any("等待" in message for message in messages)


@pytest.mark.parametrize("stale", [OLD, {**OLD, "id": 3}, {**NEW, "id": 1}])
def test_stale_or_rewritten_baseline_never_downloaded(tmp_path, stale):
    api = API([OLD, stale, stale])
    with pytest.raises(TimeoutError, match="未下载旧包"):
        downloader(api, 2).download(7, tmp_path / "export.zip")
    assert api.closed and "download" not in api.calls
    assert api.calls.count("trigger") == 1


def test_first_export_without_baseline(tmp_path):
    api = API([None, None, NEW, NEW])
    downloader(api).download(7, tmp_path / "export.zip")
    assert "download" in api.calls and api.closed


def test_changed_export_during_download_is_rejected(tmp_path):
    api = API([OLD, NEW, {**NEW, "id": 3}])
    with pytest.raises(ValueError, match="下载期间"):
        downloader(api).download(7, tmp_path / "export.zip")
    assert api.closed


def test_permission_error_keeps_original_cause_and_closes_client(tmp_path):
    error = ExternalServiceError(ExternalServiceCategory.AUTHORIZATION, "admin required", status=403)
    api = API([OLD], fail=error)
    with pytest.raises(ExternalServiceError) as caught:
        downloader(api).download(7, tmp_path / "export.zip")
    assert caught.value is error and api.closed and "download" not in api.calls


def test_poll_cancel_never_downloads_or_retriggers(tmp_path):
    api = API([OLD, OLD])
    cancellation = UploadCancellation(Event())
    cancellation.wait = lambda _: cancellation.event.set()
    with pytest.raises(ExternalServiceError) as caught:
        downloader(api).download(7, tmp_path / "export.zip", cancellation=cancellation)
    assert caught.value.category is ExternalServiceCategory.CANCELLED
    assert api.closed and api.calls.count("trigger") == 1 and "download" not in api.calls


@pytest.mark.parametrize("value", [{}, {"id": True}, {"id": 1, "createdAt": "2026-11-03"}])
def test_invalid_export_metadata_rejected_before_trigger(tmp_path, value):
    api = API([value])
    with pytest.raises(ValueError):
        downloader(api).download(7, tmp_path / "export.zip")
    assert api.closed and "trigger" not in api.calls


def test_explicit_failed_job_is_not_polled(tmp_path):
    api = API([OLD], job={"id": 12, "status": -1})
    with pytest.raises(ValueError, match="未能启动"):
        downloader(api).download(7, tmp_path / "export.zip")
    assert api.calls == ["latest", "trigger"] and api.closed
