"""Legacy single-file/batch uploads cannot reuse equal basenames in folders."""

import json
from pathlib import Path

import pytest

from tests.paratranz.test_single_upload_order import _collection, _uploader


class Files:
    def __init__(self, files, *, split=False):
        self.files = dict(files)
        self.calls = []
        self.split = split

    def list_files(self, project_id):
        return [{"id": key, "name": path} for key, path in self.files.items()]

    def _payload(self, path):
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if self.split and len(payload) > 1:
            raise RuntimeError("413 too large")
        return payload

    def get_file_info(self, project_id, file_id):
        return {"id": file_id, "name": self.files[file_id]}

    def upload_file(self, project_id, path):
        payload = self._payload(path)
        file_id = max(self.files, default=0) + 1
        self.files[file_id] = Path(path).name
        self.calls.append(("create", file_id, Path(path).name, payload))
        return {"id": file_id}

    def reupload_file(self, project_id, file_id, path):
        self.calls.append(("update", file_id, Path(path).name, self._payload(path)))


def test_plain_existing_root_is_selected_without_following_nested_basename():
    files = Files({11: "A.esp.json", 12: "folder/A.esp.json"})
    result = _uploader(files).upload_collection_as_single(_collection(), 7, "A.esp.json")
    assert result.updated == 1
    assert [call[1] for call in files.calls] == [11]
    assert all("id" not in entry for entry in files.calls[0][3])


def test_plain_missing_root_is_created_without_overwriting_nested_basename():
    files = Files({12: "folder/A.esp.json"})
    result = _uploader(files).upload_collection_as_single(_collection(), 7, "A.esp.json")
    assert result.created == 1
    assert files.files[12] == "folder/A.esp.json"
    assert files.calls[0][:3] == ("create", 13, "A.esp.json")


def test_413_split_reuses_only_deterministic_root_parts():
    files = Files({12: "folder/A.esp_1.json", 13: "folder/A.esp_2.json"}, split=True)
    uploader = _uploader(files)
    first = uploader.upload_collection_as_single(_collection(), 7, "A.esp.json")
    assert first.files == ["A.esp_1.json", "A.esp_2.json"]
    assert first.created == 2
    second = uploader.upload_collection_as_single(_collection(), 7, "A.esp.json")
    assert second.files == first.files and second.updated == 2 and second.created == 0
    assert all(call[1] not in {12, 13} for call in files.calls)


def test_plain_rejects_non_root_filename():
    files = Files({})
    with pytest.raises(ValueError, match="根目录"):
        _uploader(files).upload_collection_as_single(_collection(), 7, "folder/A.esp.json")
    assert files.calls == []


def test_plain_does_not_follow_root_file_moved_after_listing():
    class MovedFiles(Files):
        def get_file_info(self, project_id, file_id):
            return {"id": file_id, "name": "moved/A.esp.json"}

    files = MovedFiles({11: "A.esp.json"})
    with pytest.raises(RuntimeError, match="移动"):
        _uploader(files).upload_collection_as_single(_collection(), 7, "A.esp.json")
    assert not files.calls
