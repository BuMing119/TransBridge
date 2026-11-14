"""Batch archive selection, equivalent snapshots, and temporary-file ownership."""

import json
from pathlib import Path
from threading import Event
from zipfile import ZipFile, ZipInfo

import pytest

from tests.paratranz.concurrent_file_support import Backend
from tests.paratranz.test_batch_download_snapshot import scope
from transbridge.application.ports.paratranz import ExternalServiceCategory, ExternalServiceError
from transbridge.paratranz.artifact_download import FreshArtifactDownload
from transbridge.paratranz.artifact_file_reads import ArtifactFileReads
from transbridge.paratranz.batch_download_snapshot import BatchDownloadSnapshotReader
from transbridge.paratranz.file_transport import UploadCancellation

from .test_fresh_artifact_download import API, NEW, OLD


class ExportAPI(API):
    def __init__(self, payloads, *, extra=(), fail=None):
        super().__init__([OLD, OLD, NEW, NEW], fail=fail)
        self.payloads, self.extra = payloads, extra
        self.destination = None

    def download_artifacts(self, project, destination, **kwargs):
        self.calls.append("download")
        self.destination = Path(destination)
        with ZipFile(destination, "w") as archive:
            for path, rows in self.payloads.items():
                archive.writestr("raw/" + path + ".json", json.dumps(rows, ensure_ascii=False))
                archive.writestr("utf8/" + path, "ignored formatted output")
            for name, content in self.extra:
                archive.writestr(name, content)


def setup_reader(count, *, extra=None):
    inventory = {f"a.esp/category-{i}.json": i + 1 for i in range(count)}
    inventory.update(extra or {})
    backend = Backend(inventory)
    for path, file_id in inventory.items():
        backend.rows[file_id] = [
            {
                "key": str(file_id),
                "original": f"Original {file_id}",
                "translation": f"译文 {file_id}",
                "context": "NPC_:FULL",
                "stage": (0, 1, 3, 5, 9, -1)[file_id % 6],
            }
        ]
    api = ExportAPI({path: backend.rows[file_id] for path, file_id in inventory.items()})
    artifacts = []

    def factory(config):
        reader = ArtifactFileReads(
            config,
            downloader_factory=lambda config: FreshArtifactDownload(
                config,
                api_factory=lambda **_: api,
                poll_interval=0.001,
            ),
        )
        artifacts.append(reader)
        return reader

    reader = BatchDownloadSnapshotReader(
        object(), gateway_factory=lambda _: backend.gateway(), artifact_factory=factory
    )
    return reader, backend, api, artifacts


@pytest.mark.parametrize("count", [0, 49, 50, 51])
def test_actual_selected_file_count_switches_at_fifty_and_cleans_up(count):
    reader, backend, api, artifacts = setup_reader(count, extra={f"other.esp/{i}.json": 1000 + i for i in range(60)})
    messages = []
    result = reader.fetch(7, (scope("a"),), progress=lambda _n, _total, message: messages.append(message))
    assert len(result[0].entries) == count and not result[0].error
    assert len(result[0].paths) == count
    assert any(f"本轮需下载 {count} 个" in message for message in messages)
    downloads = [call for call in backend.calls if call["kind"] == "download"]
    if count >= 50:
        assert not downloads and len(artifacts) == 1
        assert api.calls == ["latest", "trigger", "latest", "latest", "download", "latest"]
        assert api.closed and not api.destination.parent.exists()
        assert artifacts[0]._temporary is None and not artifacts[0]._files
        assert any("清理" in message for message in messages)
    else:
        assert len(downloads) == count and not artifacts and not api.calls
    assert {id(session) for session in backend.sessions} == backend.closed


def test_archive_and_file_paths_produce_equal_complete_snapshots(monkeypatch):
    reader, _, api, _ = setup_reader(50)
    zipped = reader.fetch(7, (scope("a"),))
    assert not api.destination.parent.exists()
    monkeypatch.setattr("transbridge.paratranz.batch_download_snapshot.ARTIFACT_FILE_THRESHOLD", 1000)
    direct, _, _, _ = setup_reader(50)
    assert direct.fetch(7, (scope("a"),)) == zipped


@pytest.mark.parametrize("path", ["a.esp/书籍/书籍_书名.json", "a.esp/书籍/书籍_书名.json.json"])
@pytest.mark.parametrize("append_json", [True, False])
def test_raw_json_suffix_variants_match_direct_snapshots(monkeypatch, path, append_json):
    extra = {path: 50, path.replace("a.esp/", "other.esp/"): 60}
    reader, _, api, _ = setup_reader(49, extra=extra)
    if not append_json:
        rows = api.payloads.pop(path)
        api.extra = [("raw/" + path, json.dumps(rows, ensure_ascii=False))]

    zipped = reader.fetch(7, (scope("a"),))

    assert not zipped[0].error and len(zipped[0].entries) == 50
    assert path in zipped[0].paths
    assert path.replace("a.esp/", "other.esp/") not in zipped[0].paths
    assert not api.destination.parent.exists()
    monkeypatch.setattr("transbridge.paratranz.batch_download_snapshot.ARTIFACT_FILE_THRESHOLD", 1000)
    direct, _, _, _ = setup_reader(49, extra=extra)
    assert direct.fetch(7, (scope("a"),)) == zipped


def test_appended_json_member_keeps_precedence_over_unchanged_name(monkeypatch):
    reader, _, api, _ = setup_reader(50)
    api.extra = [("raw/a.esp/category-0.json", "[]")]

    zipped = reader.fetch(7, (scope("a"),))

    monkeypatch.setattr("transbridge.paratranz.batch_download_snapshot.ARTIFACT_FILE_THRESHOLD", 1000)
    direct, _, _, _ = setup_reader(50)
    assert direct.fetch(7, (scope("a"),)) == zipped
    assert not api.destination.parent.exists()


@pytest.mark.parametrize("select_other", [True, False])
def test_unchanged_member_cannot_belong_to_another_inventory_file(select_other):
    path = "a.esp/书籍_书名.json"
    reader, backend, api, _ = setup_reader(0, extra={path: 1, path + ".json": 2})
    del api.payloads[path + ".json"]
    artifact = reader.artifact_factory(object())
    try:
        with pytest.raises(ValueError, match="归属不明确"):
            artifact.prepare(7, backend.inventory, {1, 2} if select_other else {2})
        assert not artifact._files
    finally:
        artifact.close()
    assert not api.destination.parent.exists()


@pytest.mark.parametrize(
    "name",
    ["raw/other.esp/category-49.json", "raw/category-49.json"],
)
def test_missing_raw_member_never_uses_unrelated_json(name):
    reader, _, api, _ = setup_reader(50)
    rows = api.payloads.pop("a.esp/category-49.json")
    api.extra = [(name, json.dumps(rows))]
    received = []

    with pytest.raises(ValueError, match="缺少所选文件") as caught:
        reader.fetch(7, (scope("a"),), on_source=lambda *args: received.append(args))

    assert "raw/a.esp/category-49.json.json" in str(caught.value)
    assert "raw/a.esp/category-49.json" in str(caught.value)
    assert not received and not api.destination.parent.exists()


def test_legacy_numeric_partitions_and_nested_paths_match_full_paths():
    reader, backend, api, _ = setup_reader(0)
    paths = {f"a_{i}.json": i for i in range(1, 50)}
    paths["a/Quest/Nested.json"] = 50
    paths["other/Quest/Nested.json"] = 51
    backend.inventory = paths
    api.payloads = {
        path: [{"key": str(file_id), "original": path, "translation": "译文"}] for path, file_id in paths.items()
    }
    result = reader.fetch(7, (scope("a"),))
    assert len(result[0].entries) == 50 and not result[0].error
    assert "other/Quest/Nested.json" not in result[0].paths


def test_missing_member_fails_before_any_source_callback_and_cleans_up():
    reader, _, api, _ = setup_reader(50)
    del api.payloads["a.esp/category-49.json"]
    received = []
    with pytest.raises(ValueError, match="缺少所选文件"):
        reader.fetch(7, (scope("a"),), on_source=lambda *args: received.append(args))
    assert not received and not api.destination.parent.exists()


@pytest.mark.parametrize("name", ["../outside.json", "raw/../outside.json", "C:/outside", "raw\\x.json"])
def test_unsafe_member_rejected_and_cleaned(name):
    reader, _, api, _ = setup_reader(50)
    api.extra = [(name, "[]")]
    if "\\" in name:
        member = ZipInfo("placeholder")
        member.filename = name
        api.extra = [(member, "[]")]
    with pytest.raises(ValueError, match="不安全"):
        reader.fetch(7, (scope("a"),))
    assert not api.destination.parent.exists()


def test_symlink_member_rejected():
    reader, _, api, _ = setup_reader(50)
    link = ZipInfo("raw/link.json")
    link.external_attr = 0o120777 << 16
    api.extra = [(link, "outside")]
    with pytest.raises(ValueError, match="不安全"):
        reader.fetch(7, (scope("a"),))
    assert not api.destination.parent.exists()


def test_duplicate_member_rejected():
    reader, _, api, _ = setup_reader(50)
    api.extra = [("raw/a.esp/category-0.json.json", "[]")]
    with pytest.warns(UserWarning, match="Duplicate name"), pytest.raises(ValueError, match="重复路径"):
        reader.fetch(7, (scope("a"),))
    assert not api.destination.parent.exists()


def test_bad_json_fails_its_source_and_preserves_other_source():
    reader, _, api, _ = setup_reader(50, extra={"b.esp.json": 60})
    api.payloads["a.esp/category-20.json"] = {"wrong": "not rows"}
    result = reader.fetch(7, (scope("a"), scope("b")))
    assert result[0].error and not result[0].entries
    assert not result[1].error and len(result[1].entries) == 1
    assert not api.destination.parent.exists()


def test_entry_limit_preserves_complete_source_boundary():
    reader, _, api, _ = setup_reader(50)
    result = reader.fetch(7, (scope("a"),), limit=49)
    assert result[0].error and not result[0].entries
    assert not api.destination.parent.exists()


@pytest.mark.parametrize("phase", ["正在下载", "正在检索", "索引完成"])
def test_cancellation_cleans_up_at_every_archive_phase(phase):
    reader, backend, api, artifacts = setup_reader(50)
    cancellation = UploadCancellation(Event())
    roots = []

    def progress(_current, _total, message):
        if phase in message and artifacts:
            roots.append(Path(artifacts[0]._temporary.name))
            cancellation.event.set()

    with pytest.raises(ExternalServiceError) as caught:
        reader.fetch(7, (scope("a"),), cancellation=cancellation, progress=progress)
    assert caught.value.category is ExternalServiceCategory.CANCELLED
    assert roots and all(not root.exists() for root in roots)
    assert api.closed and {id(session) for session in backend.sessions} == backend.closed


def test_source_callback_failure_still_removes_archive():
    reader, _, api, _ = setup_reader(50)

    def fail(*args):
        raise RuntimeError("callback failed")

    with pytest.raises(RuntimeError, match="callback failed"):
        reader.fetch(7, (scope("a"),), on_source=fail)
    assert not api.destination.parent.exists()


def test_failed_export_removes_its_empty_temporary_directory():
    reader, backend, api, artifacts = setup_reader(50)
    api.fail = ExternalServiceError(ExternalServiceCategory.AUTHORIZATION, "admin required", status=403)
    roots = []
    with pytest.raises(ExternalServiceError):
        reader.fetch(
            7,
            (scope("a"),),
            progress=lambda *_: roots.extend(
                [Path(artifacts[0]._temporary.name)] if artifacts and artifacts[0]._temporary else []
            ),
        )
    assert roots and all(not root.exists() for root in roots)
    assert api.closed and not any(call["kind"] == "download" for call in backend.calls)


@pytest.mark.parametrize("failure", ["corrupt", "partial", "timeout"])
def test_failed_archive_transfer_cleans_up_without_source_results(failure):
    reader, _, api, artifacts = setup_reader(50)
    roots, received = [], []

    def download(project, destination, **kwargs):
        api.destination = Path(destination)
        api.destination.write_bytes(b"incomplete ZIP")
        if failure == "partial":
            raise OSError("connection lost")

    api.download_artifacts = download
    if failure == "timeout":
        api.values = iter([OLD] * 101)
    with pytest.raises((ValueError, OSError, TimeoutError)):
        reader.fetch(
            7,
            (scope("a"),),
            on_source=lambda *args: received.append(args),
            progress=lambda *_: roots.extend(
                [Path(artifacts[0]._temporary.name)] if artifacts and artifacts[0]._temporary else []
            ),
        )
    assert roots and all(not root.exists() for root in roots)
    assert api.closed and not received


def test_cleanup_runs_even_when_progress_callback_fails():
    reader, backend, api, _ = setup_reader(50)

    def progress(_current, _total, message):
        if "清理" in message:
            raise RuntimeError("progress failed")

    with pytest.raises(RuntimeError, match="progress failed"):
        reader.fetch(7, (scope("a"),), progress=progress)
    assert not api.destination.parent.exists()
    assert {id(session) for session in backend.sessions} == backend.closed


@pytest.mark.parametrize("budget", ["_MAX_FILE_BYTES", "_MAX_SELECTED_BYTES"])
def test_extraction_size_limits_fail_before_matching(monkeypatch, budget):
    reader, _, api, _ = setup_reader(50)
    monkeypatch.setattr("transbridge.paratranz.artifact_file_reads." + budget, 10)
    with pytest.raises(ValueError, match="上限"):
        reader.fetch(7, (scope("a"),))
    assert not api.destination.parent.exists()


def test_new_layout_suppresses_legacy_files_before_counting():
    reader, backend, api, _ = setup_reader(1, extra={f"a/old-{i}.json": 1000 + i for i in range(50)})
    result = reader.fetch(7, (scope("a"),))
    assert len(result[0].entries) == 1 and not api.calls
    assert len([call for call in backend.calls if call["kind"] == "download"]) == 1
