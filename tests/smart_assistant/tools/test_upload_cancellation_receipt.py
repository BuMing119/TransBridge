from dataclasses import replace
from types import SimpleNamespace

import pytest

from transbridge.application.io.identity import ExternalEntryRef
from transbridge.application.ports.paratranz import ParaTranzEntry
from transbridge.application.tasks import CancellationToken, TaskCancelled
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.smart_assistant.tools._entry_upload import upload_entries
from transbridge.smart_assistant.tools.types import ExecutionContext


def _context():
    collection = TranslationEntryCollection(
        TranslationEntry(
            id=key,
            key=key,
            original=f"Original {key}",
            translation="",
            stage=0,
            context=None,
        )
        for key in ("first", "second", "third")
    )
    app = SimpleNamespace(collection=collection, active_slot=SimpleNamespace(collection=collection))
    return ExecutionContext(app_context=app), collection


class _RemoteService:
    def __init__(self):
        self.received = []

    def upsert_entry(self, project_id, entry, *, force_overwrite=False, cancellation=None):
        assert isinstance(entry, ParaTranzEntry)
        self.received.append((project_id, entry, force_overwrite))
        cancellation._cancel("Cancelled after the remote service accepted the first entry")
        return replace(entry, remote_id=701)


def test_cancel_after_upload_preserves_remote_identity_and_returns_partial_receipt():
    context, collection = _context()
    remote = _RemoteService()
    token = CancellationToken()

    result = upload_entries({}, context, collection, remote, 7, token)

    assert not result.success
    assert result.partial
    assert result.error_code == "UPLOAD_CANCELLED"
    assert result.data["cancelled"] is True
    assert result.data["uploaded"] == 1
    assert result.data["total"] == 3
    assert result.data["not_attempted"] == 1
    assert result.failed_items == result.data["failed_items"]
    assert result.failed_items[0]["key"] == "second"
    assert result.failed_items[0]["cancelled"] is True
    assert result.recovery_action
    assert [entry.key for _, entry, _ in remote.received] == ["first"]
    assert collection.get("first").external_refs == (ExternalEntryRef("paratranz", "project:7", 701),)
    assert not collection.get("second").external_refs
    assert not collection.get("third").external_refs


def test_cancel_before_upload_raises_without_remote_or_local_changes():
    context, collection = _context()
    before = tuple(entry.snapshot() for entry in collection)
    remote = _RemoteService()
    token = CancellationToken()
    token._cancel("Cancelled before upload")

    with pytest.raises(TaskCancelled):
        upload_entries({}, context, collection, remote, 7, token)

    assert not remote.received
    assert tuple(entry.snapshot() for entry in collection) == before
