from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.application.sessions.test_gui_session_management import build_session_services
from tests.persistence.v2.fakes import MemoryFilesystem
from transbridge.application.assistant_requests.archival import compact_request_state
from transbridge.application.assistant_requests.models import ItemStatus, RequestItem, RequestStatus, UserRequest
from transbridge.application.assistant_requests.transcript import TranscriptManifest
from transbridge.application.contracts import RequestContext
from transbridge.application.projections import session_projection
from transbridge.application.sessions import ControllerSnapshot, ControllerState
from transbridge.persistence.assistant_transcript_store import AssistantTranscriptStore
from transbridge.persistence.v2.session_catalog import SessionCatalogEntry, SessionCatalogRepository


@pytest.fixture
def scope_services():
    root = Path("session-scope-tests").absolute()
    filesystem = MemoryFilesystem()
    commands, lifecycle, repository, projection = build_session_services(root, filesystem)
    yield root, filesystem, commands, lifecycle, repository, projection
    projection.close()


def _create(commands, lifecycle, *, project_id="project-b", variant_id="variant-b"):
    context = RequestContext("owner", project_id=project_id, variant_id=variant_id)
    assert commands.create_and_activate("Conversation", context).is_success
    ref = lifecycle.active.aggregate.ref
    messages = [{"role": "user", "content": "Translate this project"}]
    assert commands.save_conversation(ref, messages, messages, context).is_success
    return ref, context


def _assert_no_writes(filesystem):
    assert not {"write", "replace", "replace-durable", "remove", "mkdir"} & {
        operation for operation, _path in filesystem.calls
    }


def test_new_session_persists_project_and_variant_in_record_and_catalog(scope_services):
    root, filesystem, commands, lifecycle, repository, _projection = scope_services
    ref, _context = _create(commands, lifecycle)

    record = repository.read_snapshot(ref).envelope.data
    entry = SessionCatalogRepository(str(root), filesystem).list()[0]
    assert record["project_id"] == record["owner"]["project_id"] == entry.project_id == "project-b"
    assert record["variant_id"] == record["owner"]["variant_id"] == entry.variant_id == "variant-b"
    assert commands.list_sessions()[0]["project_id"] == "project-b"
    assert commands.list_sessions()[0]["variant_id"] == "variant-b"


@pytest.mark.parametrize("cached_scope", [None, {"project_id": "wrong-project", "variant_id": "wrong-variant"}])
def test_restart_uses_authoritative_scope_even_when_catalog_is_old_or_stale(scope_services, cached_scope):
    root, filesystem, commands, lifecycle, _repository, _projection = scope_services
    ref, _context = _create(commands, lifecycle)
    catalog_path = str(root / "session-catalog.json")
    document = json.loads(filesystem.read_bytes(catalog_path))
    entry = document["sessions"][0]
    entry.pop("project_id")
    entry.pop("variant_id")
    entry.update(cached_scope or {})
    filesystem.seed(catalog_path, json.dumps(document).encode())
    restarted, restarted_lifecycle, _restarted_repository, restarted_projection = build_session_services(
        root, filesystem
    )
    persisted = dict(filesystem.files)
    filesystem.calls.clear()
    try:
        row = restarted.list_sessions()[0]
        assert row["session_id"] == ref.identity.value
        assert (row["project_id"], row["variant_id"]) == ("project-b", "variant-b")
        assert restarted_lifecycle.active is None
        assert filesystem.files == persisted
        _assert_no_writes(filesystem)
    finally:
        restarted_projection.close()


def test_unbound_session_stays_unbound_when_browsed_from_a_project(scope_services):
    _root, filesystem, commands, lifecycle, _repository, _projection = scope_services
    ref, _context = _create(commands, lifecycle, project_id=None, variant_id=None)
    context = RequestContext("owner", project_id="project-a", variant_id="variant-a")
    filesystem.calls.clear()

    result = commands.read_conversation(ref, context)

    assert result.is_success
    assert result.value["project_id"] is result.value["variant_id"] is None
    row = commands.list_sessions()[0]
    assert row["project_id"] is row["variant_id"] is None
    _assert_no_writes(filesystem)


@pytest.mark.parametrize("legacy_format", ["ownerless-current", "v3", "v1"])
def test_ownerless_legacy_history_is_previewed_without_migration_writes(scope_services, monkeypatch, legacy_format):
    _root, filesystem, commands, lifecycle, repository, projection = scope_services
    target, _context = _create(commands, lifecycle, project_id=None, variant_id=None)
    path = repository.path_for(target)
    document = json.loads(filesystem.read_bytes(path))
    expected_messages = document["data"]["messages"]
    document["data"].pop("owner")
    if legacy_format == "v3":
        document["schema_version"] = 3
        document["data"].pop("assistant_state")
        document["data"].pop("transcript_manifest")
    elif legacy_format == "v1":
        document = {
            "session_id": target.identity.value,
            "name": "Old local history",
            "messages": expected_messages,
            "project_name": "A display name must not become a binding",
            "created_at": "2026-01-01T00:00:00Z",
            "last_active_at": "2026-01-01T00:00:00Z",
        }
    filesystem.seed(path, json.dumps(document).encode())
    _create(commands, lifecycle, project_id="project-a", variant_id="variant-a")
    active, generation = lifecycle.active, lifecycle.generation
    published = projection.snapshot().to_dict()
    persisted = dict(filesystem.files), set(filesystem.directories)
    filesystem.calls.clear()

    def unexpected(*_args, **_kwargs):
        pytest.fail("preview must not invoke a record migration or lifecycle activation")

    monkeypatch.setattr(repository, "load", unexpected)
    monkeypatch.setattr(lifecycle, "prepare_switch", unexpected)
    monkeypatch.setattr(lifecycle, "read_session", unexpected)
    result = commands.read_conversation(target, RequestContext("owner", project_id="project-a", variant_id="variant-a"))

    assert result.is_success, result.diagnostics
    assert result.value["messages"] == expected_messages
    assert result.value["project_id"] is result.value["variant_id"] is None
    assert result.value["recovery"] == "degraded"
    assert "owner_scope_inferred_from_request" in result.value["degradation_reasons"]
    assert lifecycle.active is active and lifecycle.generation == generation
    assert projection.snapshot().to_dict() == published
    assert (filesystem.files, filesystem.directories) == persisted
    _assert_no_writes(filesystem)


def test_ownerless_preview_preserves_saved_binding_instead_of_inheriting_workbench(scope_services):
    _root, filesystem, commands, lifecycle, repository, _projection = scope_services
    target, _context = _create(commands, lifecycle)
    path = repository.path_for(target)
    document = json.loads(filesystem.read_bytes(path))
    document["data"].pop("owner")
    filesystem.seed(path, json.dumps(document).encode())
    persisted = dict(filesystem.files)
    filesystem.calls.clear()

    result = commands.read_conversation(target, RequestContext("owner", project_id="project-a", variant_id="variant-a"))

    assert result.is_success, result.diagnostics
    assert (result.value["project_id"], result.value["variant_id"]) == ("project-b", "variant-b")
    assert filesystem.files == persisted
    _assert_no_writes(filesystem)


def test_preview_does_not_replace_explicit_owner_despite_stale_legacy_diagnostic(scope_services):
    _root, filesystem, commands, lifecycle, repository, _projection = scope_services
    target, _context = _create(commands, lifecycle)
    path = repository.path_for(target)
    document = json.loads(filesystem.read_bytes(path))
    document["data"]["degradation_reasons"] = ["owner_scope_missing"]
    filesystem.seed(path, json.dumps(document).encode())
    filesystem.calls.clear()

    result = commands.read_conversation(target, RequestContext("another-owner"))

    assert not result.is_success
    assert result.diagnostics[0].code == "SESSION_OWNER_MISMATCH"
    _assert_no_writes(filesystem)


def test_cross_project_preview_returns_projection_values_without_changing_runtime_or_disk(scope_services, monkeypatch):
    _root, filesystem, commands, lifecycle, _repository, projection = scope_services
    target, target_context = _create(commands, lifecycle)
    updated = lifecycle.transact(
        target,
        target_context,
        lambda snapshot: replace(
            snapshot,
            controller=ControllerSnapshot(state=ControllerState.AWAITING_CONFIRM, auto_mode=True),
            backend_summary="Saved summary",
            assistant_state={"requests": [{"request_id": "request-b", "status": "awaiting_approval"}]},
        ),
    )
    assert updated.is_success
    expected = session_projection(updated.value).to_dict()["values"]
    active_ref, context = _create(commands, lifecycle, project_id="project-a", variant_id="variant-a")
    active = lifecycle.active
    generation = lifecycle.generation
    published = projection.snapshot().to_dict()
    persisted = dict(filesystem.files), set(filesystem.directories)
    filesystem.calls.clear()

    def unexpected(*_args, **_kwargs):
        pytest.fail("preview must not reconcile, activate, save or publish")

    monkeypatch.setattr(lifecycle, "prepare_switch", unexpected)
    monkeypatch.setattr(lifecycle, "commit_switch", unexpected)
    monkeypatch.setattr(lifecycle, "save_active", unexpected)
    monkeypatch.setattr(lifecycle, "_projection", unexpected)
    monkeypatch.setattr(lifecycle._reconciler, "reconcile", unexpected)

    result = commands.read_conversation(target, replace(context, session_id=active_ref.identity.value, run_id="browse"))

    assert result.is_success
    assert result.run_id == "browse"
    assert result.value == expected
    assert lifecycle.active is active
    assert lifecycle.generation == generation
    assert projection.snapshot().to_dict() == published
    assert (filesystem.files, filesystem.directories) == persisted
    _assert_no_writes(filesystem)
    result.value["messages"][0]["content"] = "modified by viewer"
    assert commands.read_conversation(target, context).value == expected


def test_preview_rejects_different_owner_without_exposing_history_or_writing(scope_services):
    _root, filesystem, commands, lifecycle, _repository, _projection = scope_services
    target, _context = _create(commands, lifecycle)
    filesystem.calls.clear()

    result = commands.read_conversation(target, RequestContext("another-owner"))

    assert not result.is_success
    assert result.value is None
    assert result.diagnostics[0].code == "SESSION_OWNER_MISMATCH"
    _assert_no_writes(filesystem)


@pytest.mark.parametrize("damage", ["missing", "invalid", "future"])
def test_unreadable_preview_fails_without_quarantine_and_catalog_remains_discoverable(scope_services, damage):
    _root, filesystem, commands, lifecycle, repository, _projection = scope_services
    target, context = _create(commands, lifecycle)
    path = repository.path_for(target)
    if damage == "missing":
        filesystem.files.pop(path)
    elif damage == "invalid":
        filesystem.seed(path, b"{")
    else:
        document = json.loads(filesystem.read_bytes(path))
        document["schema_version"] += 1
        filesystem.seed(path, json.dumps(document).encode())
    persisted = dict(filesystem.files)
    filesystem.calls.clear()

    result = commands.read_conversation(target, context)

    assert not result.is_success
    assert result.diagnostics[0].code == "SESSION_RECORD_UNAVAILABLE"
    assert result.value is None
    row = commands.list_sessions()[0]
    assert row["available"] is False
    assert row["read_error"]
    assert row["project_id"] is row["variant_id"] is None
    assert filesystem.files == persisted
    _assert_no_writes(filesystem)


def test_catalog_accepts_legacy_rows_and_persists_optional_scope(scope_services):
    root, filesystem, _commands, _lifecycle, _repository, _projection = scope_services
    catalog = SessionCatalogRepository(str(root), filesystem)
    filesystem.seed(
        str(root / "session-catalog.json"),
        json.dumps({
            "schema_version": 1,
            "sessions": [
                {
                    "session_id": "old-session",
                    "name": "Old conversation",
                    "last_active_at": "2026-10-19T00:00:00Z",
                    "message_count": 0,
                    "recovery": "complete",
                }
            ],
        }).encode(),
    )
    assert catalog.list()[0].project_id is catalog.list()[0].variant_id is None
    scoped = SessionCatalogEntry("new-session", "New", "2026-10-19T00:01:00Z", 0, "complete", "project", "variant")
    catalog.upsert(scoped)
    assert SessionCatalogRepository(str(root), filesystem).list()[0] == scoped


def test_catalog_rejects_variant_without_project():
    with pytest.raises(ValueError, match="requires a Project"):
        SessionCatalogEntry("session", "New", "2026-10-19", 0, "complete", variant_id="variant")


def _archive_completed_request(scope_services):
    root, filesystem, commands, lifecycle, repository, _projection = scope_services
    ref, context = _create(commands, lifecycle)
    request = UserRequest(
        "completed-request",
        ref.identity.value,
        "Completed translation review",
        (RequestItem("item", "Review original requirements", status=ItemStatus.SATISFIED),),
        status=RequestStatus.COMPLETED,
    )
    store = AssistantTranscriptStore(str(root), filesystem)
    compact, manifest = compact_request_state(
        {"requests": [request.to_dict()]}, TranscriptManifest(), ref.identity.value, store, keep_recent_terminal=0
    )
    assert compact["requests"] == []
    saved = lifecycle.transact(
        ref,
        context,
        lambda snapshot: replace(snapshot, assistant_state=compact, transcript_manifest=manifest.to_dict()),
    )
    assert saved.is_success
    _create(commands, lifecycle, project_id="project-a", variant_id="variant-a")
    commands.assistant_requests = SimpleNamespace(transcript_store=store)
    assert repository.read_snapshot(ref).envelope.data["assistant_state"] == compact
    return ref, request, store


def test_cross_project_preview_restores_archived_completed_request_without_writes_or_activation(
    scope_services, monkeypatch
):
    _root, filesystem, commands, lifecycle, repository, projection = scope_services
    target, request, _store = _archive_completed_request(scope_services)
    active, generation = lifecycle.active, lifecycle.generation
    published = projection.snapshot().to_dict()
    persisted = dict(filesystem.files), set(filesystem.directories)
    filesystem.calls.clear()

    def unexpected(*_args, **_kwargs):
        pytest.fail("archive preview must only read the saved record and its authorized artifact")

    monkeypatch.setattr(repository, "load", unexpected)
    monkeypatch.setattr(lifecycle, "read_session", unexpected)
    monkeypatch.setattr(lifecycle, "prepare_switch", unexpected)
    monkeypatch.setattr(lifecycle, "commit_switch", unexpected)
    monkeypatch.setattr(commands.assistant_requests, "state", unexpected, raising=False)
    result = commands.read_conversation(target, RequestContext("owner", project_id="project-a", variant_id="variant-a"))

    assert result.is_success
    assert result.value["assistant_state"]["requests"] == [request.to_dict()]
    assert result.value["assistant_state"]["request_archives"][0]["request_id"] == request.request_id
    assert lifecycle.active is active
    assert lifecycle.generation == generation
    assert projection.snapshot().to_dict() == published
    assert (filesystem.files, filesystem.directories) == persisted
    _assert_no_writes(filesystem)


def test_preview_checks_owner_before_hydrating_archived_request(scope_services, monkeypatch):
    _root, filesystem, commands, _lifecycle, _repository, _projection = scope_services
    target, _request, _store = _archive_completed_request(scope_services)
    filesystem.calls.clear()

    def unexpected(*_args, **_kwargs):
        pytest.fail("unauthorized preview must not hydrate archived request details")

    monkeypatch.setattr("transbridge.application.assistant_requests.archival.hydrate_request_state", unexpected)
    result = commands.read_conversation(target, RequestContext("another-owner"))

    assert not result.is_success
    assert result.value is None
    assert result.diagnostics[0].code == "SESSION_OWNER_MISMATCH"
    _assert_no_writes(filesystem)
