"""History browsing reads each file once and leaves full validation to restoration."""

from collections import Counter
import json

import pytest

from transbridge.persistence import project_snapshots as module
from transbridge.persistence.project_snapshots import ProjectSnapshotRepository
from transbridge.persistence.v2.filesystem import OsPersistenceFilesystem
from transbridge.persistence.v2.ids import ProjectId, VariantId, VariantRef
from transbridge.persistence.v2.models import SchemaValidationError
from transbridge.persistence.v2.variant import VariantSnapshot


class CountingFilesystem(OsPersistenceFilesystem):
    def __init__(self):
        self.reads = Counter()

    def read_bytes(self, path):
        self.reads[path] += 1
        return super().read_bytes(path)


def _write(root, identity, ref, *, revision=3):
    directory = root / "snapshots"
    directory.mkdir(exist_ok=True)
    document = {
        "schema_version": 1,
        "project_id": ref.project_id.value,
        "name": "Saved history",
        "variant": VariantSnapshot(ref, (), (), revision).to_dto().envelope.to_dict(),
    }
    path = directory / f"{identity}.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path, document


def test_history_list_reads_once_without_materializing_or_validating_full_versions(tmp_path, monkeypatch):
    ref = VariantRef(VariantId("variant"), ProjectId("project"))
    _write(tmp_path, "a" * 64, ref)
    _write(tmp_path, "b" * 64, ref, revision=8)
    _write(tmp_path, "c" * 64, VariantRef(VariantId("other"), ProjectId("other-project")))
    fs = CountingFilesystem()
    repository = ProjectSnapshotRepository(str(tmp_path), fs)

    def unexpected(*_args, **_kwargs):
        raise AssertionError("Browsing history must not load complete variants")

    with monkeypatch.context() as patch:
        patch.setattr(module, "validate_v2", unexpected)
        patch.setattr(VariantSnapshot, "from_dto", unexpected)
        listed = repository.list(ref)

    assert [item.revision for item in listed] == [8, 3]
    assert len(fs.reads) == 3
    assert set(fs.reads.values()) == {1}
    assert repository.load("a" * 64, ref).revision == 3


def test_history_display_does_not_certify_corrupt_payload_for_restoration(tmp_path):
    ref = VariantRef(VariantId("variant"), ProjectId("project"))
    path, document = _write(tmp_path, "a" * 64, ref)
    document["variant"]["data"]["entries"] = "corrupt-entry-content"
    path.write_text(json.dumps(document), encoding="utf-8")
    repository = ProjectSnapshotRepository(str(tmp_path), CountingFilesystem())
    assert len(repository.list(ref)) == 1
    with pytest.raises(SchemaValidationError):
        repository.load("a" * 64, ref)
    assert path.exists()


@pytest.mark.parametrize("invalid", [True, -1, "3"])
def test_invalid_display_metadata_cannot_hide_other_history(tmp_path, invalid, caplog):
    ref = VariantRef(VariantId("variant"), ProjectId("project"))
    good, _ = _write(tmp_path, "a" * 64, ref)
    bad, document = _write(tmp_path, "b" * 64, ref)
    document["variant"]["revision"] = invalid
    bad.write_text(json.dumps(document), encoding="utf-8")
    repository = ProjectSnapshotRepository(str(tmp_path), CountingFilesystem())
    assert [item.identity for item in repository.list(ref)] == [good.stem]
    assert bad.exists()
    assert "保留原文件" in caplog.text
