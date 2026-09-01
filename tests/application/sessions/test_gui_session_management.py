from __future__ import annotations

from dataclasses import replace
from itertools import count
from pathlib import Path

import pytest

from transbridge.application.contracts import RequestContext
from transbridge.application.projections import ProjectionStore, SessionProjectionPublisher
from transbridge.application.sessions import GuiSessionCommandFacade, SessionLifecycleService
from transbridge.persistence.session_lifecycle import SessionUnitOfWorkFactory, V2SessionSnapshotRepository
from transbridge.persistence.v2 import OsPersistenceFilesystem, SessionRepository
from transbridge.persistence.v2.lifecycle_transactions import SessionLifecycleTransactionStore
from transbridge.persistence.v2.session_catalog import SessionCatalogRepository


def build_session_services(root: Path):
    filesystem = OsPersistenceFilesystem()
    repository = SessionRepository(str(root), filesystem)
    catalog = SessionCatalogRepository(str(root), filesystem)
    ids = count()

    def next_id():
        return f"session-{next(ids)}"

    projection = ProjectionStore()
    publisher = SessionProjectionPublisher(projection)
    lifecycle = SessionLifecycleService(
        V2SessionSnapshotRepository(repository),
        SessionUnitOfWorkFactory(SessionLifecycleTransactionStore(str(root), filesystem), next_id),
        token_factory=next_id,
        projection=publisher,
    )
    publisher.bind(lifecycle)
    commands = GuiSessionCommandFacade(
        lifecycle, repository, catalog, id_factory=next_id, timestamp_factory=lambda: "2026-08-31T12:00:00Z"
    )
    return commands, lifecycle, repository, projection


@pytest.fixture
def services(tmp_path):
    values = build_session_services(tmp_path)
    yield values
    values[3].close()


def _create(commands, lifecycle, name="Conversation"):
    context = RequestContext("owner")
    assert commands.create_and_activate(name, context).is_success
    ref = lifecycle.active.aggregate.ref
    messages = [{"role": "user", "content": name}]
    assert commands.save_conversation(ref, messages, messages, context).is_success
    return ref


@pytest.mark.parametrize("active", [False, True])
def test_rename_persists_metadata_without_losing_history_or_switching(services, active):
    commands, lifecycle, repository, _projection = services
    ref = _create(commands, lifecycle)
    if not active:
        _create(commands, lifecycle, "Other")
    active_ref = lifecycle.active.aggregate.ref
    before = repository.load(ref).value.envelope

    result = commands.rename(ref, "  Renamed  ", RequestContext("owner"))

    assert result.is_success
    stored = repository.load(ref).value.envelope
    assert stored.data["name"] == "Renamed"
    assert stored.data["history"] == before.data["history"]
    assert stored.revision == before.revision + 1
    assert lifecycle.active.aggregate.ref == active_ref
    assert not lifecycle.active.dirty
    assert next(row for row in commands.list_sessions() if row["session_id"] == ref.identity.value)["name"] == "Renamed"


@pytest.mark.parametrize("active", [False, True])
def test_delete_removes_record_and_catalog_and_detaches_active_identity(services, active):
    commands, lifecycle, repository, projection = services
    ref = _create(commands, lifecycle)
    other = None if active else _create(commands, lifecycle, "Other")

    assert commands.delete(ref, RequestContext("owner")).is_success

    assert not Path(repository.path_for(ref)).exists()
    assert all(item["session_id"] != ref.identity.value for item in commands.list_sessions())
    if active:
        assert lifecycle.active is None
        assert projection.snapshot() is None
    else:
        assert lifecycle.active.aggregate.ref == other


def test_delete_failure_restores_active_conversation_and_catalog(services, monkeypatch):
    commands, lifecycle, repository, projection = services
    ref = _create(commands, lifecycle)

    def fail_delete(_ref):
        raise OSError("record is locked")

    monkeypatch.setattr(repository, "delete", fail_delete)
    result = commands.delete(ref, RequestContext("owner"))

    assert not result.is_success
    assert lifecycle.active.aggregate.ref == ref
    assert lifecycle.active.aggregate.snapshot().visible_messages()[0]["content"] == "Conversation"
    assert projection.snapshot().to_dict()["values"]["session_id"] == ref.identity.value
    assert commands.list_sessions()[0]["session_id"] == ref.identity.value
    assert Path(repository.path_for(ref)).exists()


def test_rename_failure_restores_catalog_without_changing_active_name(services, monkeypatch):
    commands, lifecycle, repository, _projection = services
    ref = _create(commands, lifecycle)

    def fail_save(*_args):
        raise OSError("record is locked")

    monkeypatch.setattr(repository, "save", fail_save)
    result = commands.rename(ref, "Renamed", RequestContext("owner"))

    assert not result.is_success
    assert commands.list_sessions()[0]["name"] == "Conversation"
    assert lifecycle.active.aggregate.snapshot().name == "Conversation"


def test_renaming_inactive_session_preserves_late_runtime_changes(services):
    commands, lifecycle, repository, _projection = services
    ref = _create(commands, lifecycle)
    retained = lifecycle.active.aggregate
    _create(commands, lifecycle, "Other")
    snapshot = retained.snapshot()
    retained.replace_snapshot(
        replace(snapshot, backend_summary="late background update"), expected_revision=snapshot.revision
    )

    result = commands.rename(ref, "Renamed", RequestContext("owner"))

    assert result.is_success
    persisted = repository.load(ref).value.envelope
    assert persisted.data["name"] == "Renamed"
    assert persisted.data["backend_summary"] == "late background update"
    assert persisted.revision == snapshot.revision + 2


@pytest.mark.parametrize("operation", ["rename", "delete"])
def test_management_rejects_other_owner_without_modifying_record_or_catalog(services, operation):
    commands, lifecycle, repository, _projection = services
    ref = _create(commands, lifecycle)
    before = Path(repository.path_for(ref)).read_bytes()
    context = RequestContext("another-owner")
    result = commands.rename(ref, "Intruder", context) if operation == "rename" else commands.delete(ref, context)

    assert not result.is_success
    assert result.diagnostics[0].code == "SESSION_OWNER_MISMATCH"
    assert Path(repository.path_for(ref)).read_bytes() == before
    assert commands.list_sessions()[0]["name"] == "Conversation"
