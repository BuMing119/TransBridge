from __future__ import annotations

import os

import pytest

from transbridge.persistence.v2.atomic_documents import AtomicDocumentStore

from .fakes import MemoryFilesystem


class _StageReadFaultFilesystem(MemoryFilesystem):
    def read_bytes(self, path: str) -> bytes:
        if path.endswith(".tmp"):
            raise OSError("injected staging read fault")
        return super().read_bytes(path)


class _StageWriteFaultFilesystem(MemoryFilesystem):
    def write_bytes(self, path: str, data: bytes) -> None:
        if path.endswith(".tmp"):
            raise OSError("injected staging write fault")
        super().write_bytes(path, data)


class _StageWriteAndCleanupFaultFilesystem(_StageWriteFaultFilesystem):
    def __init__(self) -> None:
        super().__init__()
        self._stage_remove_count = 0

    def remove(self, path: str, *, missing_ok: bool = False) -> None:
        if path.endswith(".tmp"):
            self._stage_remove_count += 1
            if self._stage_remove_count > 1:
                raise OSError("injected cleanup fault")
        super().remove(path, missing_ok=missing_ok)


@pytest.mark.parametrize("filesystem_type", [_StageWriteFaultFilesystem, _StageReadFaultFilesystem])
def test_staging_fault_does_not_publish_or_leave_a_temporary_document(
    filesystem_type: type[MemoryFilesystem],
) -> None:
    filesystem = filesystem_type()
    root = os.path.abspath(f"atomic-document-{type(filesystem).__name__}")
    store = AtomicDocumentStore(root, filesystem)
    destination = store.path("project-catalog.json")

    with pytest.raises(OSError, match="injected staging"):
        store.write_json("project-catalog.json", {"schema_version": 1, "projects": {}}, "repair")

    assert destination not in filesystem.files
    assert not any(path.endswith(".tmp") for path in filesystem.files)


def test_replace_fault_preserves_existing_document_and_cleans_stage() -> None:
    root = os.path.abspath("atomic-document-replace-fault")
    filesystem = MemoryFilesystem()
    store = AtomicDocumentStore(root, filesystem)
    destination = store.path("project-catalog.json")
    original = b'{"schema_version":1,"projects":{}}'
    filesystem.seed(destination, original)
    filesystem.fail_replace_destinations.add(destination)

    with pytest.raises(OSError, match="injected replace fault"):
        store.write_json("project-catalog.json", {"schema_version": 1, "projects": {"new": {}}}, "repair")

    assert filesystem.files[destination] == original
    assert not any(path.endswith(".tmp") for path in filesystem.files)


def test_cleanup_fault_does_not_mask_the_original_publication_failure() -> None:
    root = os.path.abspath("atomic-document-cleanup-fault")
    filesystem = _StageWriteAndCleanupFaultFilesystem()
    store = AtomicDocumentStore(root, filesystem)

    with pytest.raises(OSError, match="injected staging write fault"):
        store.write_json("project-catalog.json", {"schema_version": 1, "projects": {}}, "repair")


def test_durable_document_uses_write_through_replace_and_durable_removal() -> None:
    root = os.path.abspath("atomic-document-durable")
    filesystem = MemoryFilesystem()
    store = AtomicDocumentStore(root, filesystem)
    destination = store.path("project-save-journal/tx.json")

    store.write_json(
        "project-save-journal/tx.json",
        {"schema_version": 1},
        "tx-prepare",
        durable=True,
    )

    assert ("replace-durable", destination) in filesystem.calls
    assert filesystem.read_bytes(destination) == b'{"schema_version":1}'

    store.remove_durable(destination, "tx-cleanup")

    assert destination not in filesystem.files
    assert sum(operation == "replace-durable" for operation, _path in filesystem.calls) == 2
