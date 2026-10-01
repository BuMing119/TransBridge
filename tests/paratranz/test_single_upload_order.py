from __future__ import annotations

import json
from pathlib import Path

import pytest

from transbridge.application.io.identity import ExternalEntryRef
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.paratranz.workflow.uploader import ParaTranzUploader


class RecordingFiles:
    def __init__(self, *, existing=False, max_entries=None):
        self.existing = existing
        self.max_entries = max_entries
        self.calls = []
        self.list_count = 0

    def list_files(self, project_id):
        self.list_count += 1
        return [{"id": 41, "name": "all.json"}] if self.existing else []

    def _record(self, operation, path, force=False):
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        self.calls.append((operation, Path(path).name, payload, force))
        if self.max_entries is not None and len(payload) > self.max_entries:
            raise RuntimeError("413 too large")
        return {"id": 42}

    def upload_file(self, project_id, path):
        return self._record("create", path)

    def reupload_file(self, project_id, file_id, path):
        return self._record("original", path)

    def update_file_translation(self, project_id, file_id, path, *, force=False):
        return self._record("translation", path, force)


def _uploader(api):
    uploader = ParaTranzUploader.__new__(ParaTranzUploader)
    uploader._api = api
    return uploader


def _collection(*, orders=(0, 428)):
    return TranslationEntryCollection(
        TranslationEntry(
            f"legacy-{index}",
            f"key-{index}",
            f"original-{index}",
            f"translation-{index}",
            1,
            "BOOK:FULL",
            metadata=() if order is None else (("plugin.source_order", order),),
            external_refs=(ExternalEntryRef("paratranz", "project:7", 17),) if index == 0 else (),
        )
        for index, order in enumerate(orders)
    )


@pytest.mark.parametrize(
    ("mode", "operations", "force"),
    [
        ("orig_only", ["original"], False),
        ("both", ["original", "translation"], False),
        ("trans_safe", ["translation"], False),
        ("trans_force", ["translation"], True),
    ],
)
def test_single_upload_uses_wire_mapping_for_each_existing_file_mode(mode, operations, force):
    collection = _collection()
    before = collection.to_dict()
    api = RecordingFiles(existing=True)

    result = _uploader(api).upload_collection_as_single(collection, 7, "all.json", translation_mode=mode)

    assert result.files == ["all.json"]
    assert [call[0] for call in api.calls] == operations
    for operation, _, payload, actual_force in api.calls:
        assert [record["context"] for record in payload] == ["00000000|BOOK:FULL", "00000428|BOOK:FULL"]
        assert payload[0]["id"] == 17
        assert "id" not in payload[1]
        assert [record["key"] for record in payload] == ["key-0", "key-1"]
        assert all(set(record) <= {"key", "original", "translation", "stage", "context", "id"} for record in payload)
        if operation == "translation":
            assert actual_force is force
    assert collection.to_dict() == before


@pytest.mark.parametrize("orders", [(None, None, None, None), (0, 7, 428, 901)])
def test_repeated_413_splits_keep_complete_collection_prefixes(orders):
    collection = _collection(orders=orders)
    before = collection.to_dict()
    api = RecordingFiles(max_entries=1)

    result = _uploader(api).upload_collection_as_single(collection, 7, "all.json", translation_mode="both")

    assert result.created == result.translation_updated == 4
    assert result.files == [f"all_{index}.json" for index in range(1, 5)]
    uploaded = [payload[0] for operation, _, payload, _ in api.calls if operation == "create" and len(payload) == 1]
    expected = range(4) if orders[0] is None else orders
    assert [record["context"] for record in uploaded] == [f"{order:08d}|BOOK:FULL" for order in expected]
    translated = [payload[0] for operation, _, payload, _ in api.calls if operation == "translation"]
    assert translated == uploaded
    assert collection.to_dict() == before


@pytest.mark.parametrize("mode", ["trans_safe", "trans_force"])
def test_translation_only_still_skips_missing_remote_file(mode):
    api = RecordingFiles()
    result = _uploader(api).upload_collection_as_single(_collection(), 7, "all.json", translation_mode=mode)
    assert result.files == []
    assert api.calls == []


def test_invalid_order_fails_before_any_remote_call():
    api = RecordingFiles()
    with pytest.raises(ValueError, match="99999999"):
        _uploader(api).upload_collection_as_single(_collection(orders=(100_000_000,)), 7, "all.json")
    assert api.list_count == 0
    assert api.calls == []
