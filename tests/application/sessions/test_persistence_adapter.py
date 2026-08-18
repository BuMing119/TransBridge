from __future__ import annotations

from dataclasses import replace

import pytest

from transbridge.application.contracts import DomainError, RequestContext
from transbridge.application.sessions import ControllerSnapshot, SessionSnapshot
from transbridge.application.tasks.models import OwnerRef
from transbridge.persistence.session_lifecycle import (
    SessionUnitOfWorkFactory,
    V2SessionSnapshotRepository,
)
from transbridge.persistence.v2 import LoadedRecord, SessionId, SessionRef


def _snapshot(*, revision: int = 2) -> SessionSnapshot:
    ref = SessionRef(SessionId("session-a"))
    return SessionSnapshot(
        ref=ref,
        name="session",
        owner=OwnerRef("owner", "gui", session_id=ref.identity.value),
        messages=({"role": "user", "content": "visible"},),
        backend_history=({"role": "user", "content": "backend"},),
        backend_summary=None,
        controller=ControllerSnapshot(),
        project_id=None,
        variant_id=None,
        approvals=(),
        jobs=(),
        revision=revision,
        created_at="2026-08-18T00:00:00Z",
        last_active_at="2026-08-18T00:01:00Z",
    )


class _Repository:
    def __init__(self, snapshot: SessionSnapshot) -> None:
        self.dto = snapshot.to_dto()
        self.save_calls = []

    def load(self, ref):
        return LoadedRecord(ref, self.dto, "hash")

    def save(self, ref, dto):
        self.save_calls.append((ref, dto))
        self.dto = dto


def test_v2_repository_roundtrip_and_compare_and_swap_revision() -> None:
    repository = _Repository(_snapshot())
    adapter = V2SessionSnapshotRepository(repository)
    context = RequestContext("owner", session_id="session-a")
    loaded = adapter.load(SessionRef(SessionId("session-a")), context)
    updated = replace(loaded, backend_summary="persisted", revision=3)

    saved = adapter.save(updated, expected_revision=2, context=context)

    assert saved == updated
    assert repository.save_calls[0][1].envelope.revision == 3
    with pytest.raises(DomainError) as error:
        adapter.save(replace(updated, revision=4), expected_revision=2, context=context)
    assert error.value.code == "SESSION_REVISION_CONFLICT"


def test_legacy_owner_scope_is_rebound_to_request_but_never_claimed_full_recovery() -> None:
    repository = _Repository(_snapshot())
    data = repository.dto.envelope.data
    data.pop("owner")
    data.pop("history")
    adapter = V2SessionSnapshotRepository(repository)

    loaded = adapter.load(
        SessionRef(SessionId("session-a")),
        RequestContext("owner", session_id="session-a", permissions=frozenset({"session:read"})),
    )

    assert loaded.owner.owner_id == "owner"
    assert loaded.owner.permissions == frozenset({"session:read"})
    assert loaded.recovery.value == "degraded"
    assert "owner_scope_inferred_from_request" in loaded.degradation_reasons
    assert "backend_history_missing" in loaded.degradation_reasons


class _TransactionStore:
    def __init__(self) -> None:
        self.events = []
        self.fail_commit = False

    def begin(self, transaction_id):
        self.events.append(("begin", transaction_id))

    def stage_activate(self, transaction_id, old, candidate):
        self.events.append(("stage", transaction_id, old, candidate))

    def commit(self, transaction_id):
        self.events.append(("commit", transaction_id))
        if self.fail_commit:
            raise OSError("commit failed")

    def rollback(self, transaction_id):
        self.events.append(("rollback", transaction_id))


def test_session_unit_of_work_stages_pointer_then_commits_or_rolls_back() -> None:
    store = _TransactionStore()
    factory = SessionUnitOfWorkFactory(store, lambda: "transaction")
    unit = factory.begin()
    unit.stage_activate(None, _snapshot())
    unit.commit()

    assert [event[0] for event in store.events] == ["begin", "stage", "commit"]
    with pytest.raises(RuntimeError):
        unit.commit()

    failing = _TransactionStore()
    failing.fail_commit = True
    unit = SessionUnitOfWorkFactory(failing, lambda: "failing").begin()
    unit.stage_activate(None, _snapshot())
    with pytest.raises(OSError):
        unit.commit()
    unit.rollback()
    assert [event[0] for event in failing.events] == ["begin", "stage", "commit", "rollback"]
