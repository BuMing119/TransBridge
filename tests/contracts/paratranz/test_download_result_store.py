"""Download discovery isolates content damage while preserving real I/O failures."""

import json
from pathlib import Path

import pytest

from transbridge.application.sync.download_results import DownloadResultStore

TARGET = ("https://paratranz.cn", 42, 7, "project", "variant")


@pytest.mark.parametrize(
    "damage",
    [
        "invalid_json",
        "invalid_encoding",
        "not_object",
        "missing_created_at",
        "null_created_at",
        "invalid_created_at",
        "invalid_sources",
        "invalid_source_path",
        "invalid_policy",
        "missing_id",
        "invalid_target",
        "invalid_decisions",
        "invalid_merges",
    ],
)
def test_mixed_records_keep_healthy_results_and_report_preserved_damage(tmp_path, caplog, damage):
    store = DownloadResultStore(tmp_path)
    healthy = store.create(TARGET, "keep_current")
    broken = store.create(TARGET, "keep_current")
    path = store.directory(broken["result_id"]) / "result.json"
    if damage == "invalid_json":
        raw = b"{broken"
    elif damage == "invalid_encoding":
        raw = b"\xff"
    elif damage == "not_object":
        raw = b"[]"
    else:
        if damage == "missing_created_at":
            broken.pop("created_at")
        elif damage == "missing_id":
            broken.pop("result_id")
        else:
            key, value = {
                "null_created_at": ("created_at", None),
                "invalid_created_at": ("created_at", "yesterday"),
                "invalid_sources": ("sources", []),
                "invalid_source_path": ("sources", {"one": "../result.json"}),
                "invalid_policy": ("policy", {}),
                "invalid_target": ("target", {}),
                "invalid_decisions": ("decisions", [["row"]]),
                "invalid_merges": ("merges", {}),
            }[damage]
            broken[key] = value
        raw = json.dumps(broken).encode()
    path.write_bytes(raw)

    records = store.list_records(TARGET)

    assert records == (healthy,)
    assert len(records.diagnostics) == 1 and str(path) in records.diagnostics[0]
    assert str(path) in caplog.text
    assert path.read_bytes() == raw


@pytest.mark.parametrize("failure", ["enumeration", "record_read"])
def test_discovery_propagates_permission_errors(tmp_path, monkeypatch, failure):
    store = DownloadResultStore(tmp_path)
    record = store.create(TARGET, "keep_current")
    method = "iterdir" if failure == "enumeration" else "read_text"
    blocked = store.root if failure == "enumeration" else store.directory(record["result_id"]) / "result.json"
    original = getattr(Path, method)

    def deny(path, *args, **kwargs):
        if path == blocked:
            raise PermissionError("access denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, method, deny)
    with pytest.raises(PermissionError, match="access denied"):
        store.list_records(TARGET)


def test_missing_store_is_empty_but_a_file_at_store_path_is_an_error(tmp_path):
    store = DownloadResultStore(tmp_path)
    assert store.list_records(TARGET) == ()
    store.root.write_text("unexpected file", encoding="utf-8")
    with pytest.raises(NotADirectoryError):
        store.list_records(TARGET)
