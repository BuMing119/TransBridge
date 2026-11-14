"""Only unresolved files are read from alternate export directories."""

import json
from threading import Event
from zipfile import ZipFile

import pytest

from tests.paratranz.test_artifact_file_downloads import setup_reader
from tests.paratranz.test_artifact_member_discovery import use_utf8
from tests.paratranz.test_batch_download_snapshot import scope
from transbridge.application.ports.paratranz import ExternalServiceCategory, ExternalServiceError
from transbridge.paratranz.file_transport import UploadCancellation


def record_reads(monkeypatch):
    opened = []
    original = ZipFile.open

    def open_member(self, name, mode="r", *args, **kwargs):
        if mode == "r":
            opened.append(name if isinstance(name, str) else name.filename)
        return original(self, name, mode, *args, **kwargs)

    monkeypatch.setattr(ZipFile, "open", open_member)
    return opened


def test_complete_raw_directory_never_opens_other_copies(monkeypatch):
    reader, _, api, _ = setup_reader(50)
    api.extra = []
    for path, rows in api.payloads.items():
        api.extra.append(("utf-16/" + path, json.dumps(rows).encode("utf-16")))
    opened = record_reads(monkeypatch)

    result = reader.fetch(7, (scope("a"),))

    assert not result[0].error and len(result[0].entries) == 50
    assert len(opened) == 50 and all(name.startswith("raw/") for name in opened)
    assert not api.destination.parent.exists()


def test_only_missing_files_are_read_from_other_directories(monkeypatch):
    reader, _, api, _ = setup_reader(50)
    api.extra = []
    missing = [f"a.esp/category-{index}.json" for index in (3, 27)]
    for path in missing:
        rows = api.payloads.pop(path)
        api.extra.append(("utf-8/" + path, json.dumps(rows)))
    opened = record_reads(monkeypatch)

    result = reader.fetch(7, (scope("a"),))

    assert not result[0].error and len(result[0].entries) == 50
    assert len(opened) == 50
    assert sorted(name for name in opened if not name.startswith("raw/")) == sorted("utf-8/" + p for p in missing)


@pytest.mark.parametrize("payload", [b"", b"invalid", b"\xff\xff", b"{}", b"null"])
def test_unreadable_raw_uses_next_valid_copy_and_stops(monkeypatch, payload):
    reader, _, api, _ = setup_reader(50)
    path = "a.esp/category-0.json"
    rows = api.payloads.pop(path)
    api.extra = [
        ("raw/" + path + ".json", payload),
        ("utf8/" + path, json.dumps(rows)),
        ("backup/" + path, b"must not read"),
    ]
    opened = record_reads(monkeypatch)

    result = reader.fetch(7, (scope("a"),))

    assert not result[0].error and len(result[0].entries) == 50
    assert len(opened) == 51 and "backup/" + path not in opened
    assert not api.destination.parent.exists()


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "utf-32", "utf-32-be"])
def test_json_encodings_are_decoded_without_reading_other_copies(monkeypatch, encoding):
    reader, _, api, _ = setup_reader(50)
    path = "a.esp/category-0.json"
    rows = api.payloads.pop(path)
    api.extra = [
        ("raw/" + path + ".json", json.dumps(rows, ensure_ascii=False).encode(encoding)),
        ("utf8/" + path, json.dumps(rows)),
    ]
    opened = record_reads(monkeypatch)

    result = reader.fetch(7, (scope("a"),))

    assert not result[0].error and len(result[0].entries) == 50
    assert len(opened) == 50 and "utf8/" + path not in opened
    assert any(entry.translation == rows[0]["translation"] for entry in result[0].entries)


def test_empty_directory_is_skipped_and_valid_empty_file_is_not_replaced(monkeypatch):
    reader, _, api, _ = setup_reader(50)
    api.payloads["a.esp/category-0.json"] = []
    use_utf8(api)
    api.extra.extend([("raw/", b""), ("backup/a.esp/category-0.json", b"invalid")])
    opened = record_reads(monkeypatch)

    result = reader.fetch(7, (scope("a"),))

    assert not result[0].error and len(result[0].entries) == 49
    assert len(opened) == 50 and all(name.startswith("utf8/") for name in opened)


def test_unknown_directories_use_coverage_before_name(monkeypatch):
    reader, _, api, _ = setup_reader(50)
    use_utf8(api, prefix="z-complete/")
    api.extra.append(("a-partial/a.esp/category-0.json", b"must not read"))
    opened = record_reads(monkeypatch)

    result = reader.fetch(7, (scope("a"),))

    assert not result[0].error and len(result[0].entries) == 50
    assert len(opened) == 50 and all(name.startswith("z-complete/") for name in opened)


def test_unused_copies_do_not_consume_decompression_budget(monkeypatch):
    reader, _, api, _ = setup_reader(50)
    expected_bytes = sum(len(json.dumps(rows, ensure_ascii=False).encode()) for rows in api.payloads.values())
    api.extra = [("backup/" + path, "x" * 4096) for path in api.payloads]
    monkeypatch.setattr("transbridge.paratranz.artifact_file_reads._MAX_SELECTED_BYTES", expected_bytes)
    opened = record_reads(monkeypatch)

    result = reader.fetch(7, (scope("a"),))

    assert not result[0].error and len(result[0].entries) == 50
    assert len(opened) == 50


def test_failed_attempts_count_toward_actual_read_budget(monkeypatch):
    reader, backend, api, _ = setup_reader(0, extra={"a.esp/file.json": 1})
    path = "a.esp/file.json"
    valid = json.dumps(api.payloads.pop(path)).encode()
    api.extra = [("raw/" + path + ".json", b"x" * len(valid)), ("utf8/" + path, valid)]
    monkeypatch.setattr("transbridge.paratranz.artifact_file_reads._MAX_SELECTED_BYTES", len(valid))
    artifact = reader.artifact_factory(object())
    try:
        artifact.prepare(7, backend.inventory, {1})
        with pytest.raises(ValueError, match="累计读取内容"):
            artifact.get_file_translation(7, 1)
    finally:
        artifact.close()
    assert not api.destination.parent.exists()


def test_cancellation_while_reading_closes_archive_and_removes_temporary_files(monkeypatch):
    reader, _, api, artifacts = setup_reader(50)
    cancellation = UploadCancellation(Event())
    original = ZipFile.open

    def open_member(self, name, mode="r", *args, **kwargs):
        if mode == "r":
            cancellation.event.set()
        return original(self, name, mode, *args, **kwargs)

    monkeypatch.setattr(ZipFile, "open", open_member)
    with pytest.raises(ExternalServiceError) as caught:
        reader.fetch(7, (scope("a"),), cancellation=cancellation)

    assert caught.value.category is ExternalServiceCategory.CANCELLED
    assert artifacts[0]._archive is None and not api.destination.parent.exists()


def test_all_candidates_fail_with_file_and_candidate_context():
    reader, _, api, _ = setup_reader(50)
    path = "a.esp/category-0.json"
    api.payloads.pop(path)
    api.extra = [("raw/" + path + ".json", b""), ("utf8/" + path, b"invalid")]

    result = reader.fetch(7, (scope("a"),))

    assert not result[0].entries
    assert "raw/" + path + ".json" in result[0].error and "utf8/" + path in result[0].error
    assert not api.destination.parent.exists()
