from copy import deepcopy
from dataclasses import replace

import pytest

from transbridge.application.assistant_requests.archival import compact_request_state, hydrate_request_state
from transbridge.application.assistant_requests.models import (
    AssistantExecutionRef,
    EffectIntent,
    Evidence,
    ItemStatus,
    RequestError,
    RequestItem,
    RequestStatus,
    UserRequest,
)
from transbridge.application.assistant_requests.transcript import TranscriptManifest
from transbridge.persistence.assistant_transcript_store import AssistantTranscriptStore
from transbridge.persistence.v2.models import BackupVerificationError, PathBoundaryError


def _request(identity, *, terminal=True):
    return UserRequest(
        identity,
        "s",
        f"Goal {identity}",
        (
            RequestItem(
                "i", "Preserve original acceptance", status=ItemStatus.SATISFIED if terminal else ItemStatus.PENDING
            ),
        ),
        status=RequestStatus.COMPLETED if terminal else RequestStatus.OPEN,
        source_message_ids=(f"source-{identity}",),
        evidence=(Evidence(f"e-{identity}", "answer", 1, ("i",), f"message-{identity}", "original-proof"),)
        if terminal
        else (),
    )


def test_default_keeps_latest_hundred_terminal_requests_and_all_active_details(tmp_path):
    requests = [_request(str(index)) for index in range(103)] + [_request("active", terminal=False)]
    state = {"requests": [request.to_dict() for request in requests]}
    original = deepcopy(state)
    store = AssistantTranscriptStore(str(tmp_path))
    compact, manifest = compact_request_state(state, TranscriptManifest(), "s", store)
    assert len(compact["requests"]) == 101
    assert [entry["request_id"] for entry in compact["request_archives"]] == ["0", "1", "2"]
    assert len(manifest.artifacts) == 3
    assert hydrate_request_state(compact, "s", store)["requests"] == original["requests"]
    assert state == original


def test_hydration_and_repeated_compaction_preserve_interleaved_order(tmp_path):
    identities = ["active-a", "old-a", "active-b", "old-b", "recent", "active-c"]
    original = {"requests": [_request(i, terminal=not i.startswith("active")).to_dict() for i in identities]}
    store = AssistantTranscriptStore(str(tmp_path))
    compact, manifest = compact_request_state(original, TranscriptManifest(), "s", store, keep_recent_terminal=1)
    assert [entry["position"] for entry in compact["request_archives"]] == [1, 3]
    hydrated = hydrate_request_state(compact, "s", store)
    assert hydrated["requests"] == original["requests"]
    assert hydrate_request_state(hydrated, "s", store) == hydrated
    repeated, repeated_manifest = compact_request_state(hydrated, manifest, "s", store, keep_recent_terminal=1)
    assert repeated == compact
    assert repeated_manifest == manifest


def test_archive_replacement_retains_full_late_evidence_and_previous_backup(tmp_path):
    store = AssistantTranscriptStore(str(tmp_path))
    state = {"requests": [_request("old").to_dict()]}
    compact, manifest = compact_request_state(state, TranscriptManifest(), "s", store, keep_recent_terminal=0)
    backup = deepcopy(compact)
    hydrated = hydrate_request_state(compact, "s", store)
    request = UserRequest.from_dict(hydrated["requests"][0])
    late = Evidence("late-proof", "answer", 1, ("i",), "late-message", "late-hash")
    hydrated["requests"][0] = replace(request, evidence=(*request.evidence, late)).to_dict()
    updated, next_manifest = compact_request_state(hydrated, manifest, "s", store, keep_recent_terminal=0)
    assert next_manifest.artifacts != manifest.artifacts
    assert len(next_manifest.artifacts) == 1
    assert hydrate_request_state(updated, "s", store)["requests"] == hydrated["requests"]
    assert hydrate_request_state(backup, "s", store)["requests"] == state["requests"]
    store.validate("s", manifest)


def test_terminal_with_unsettled_effect_remains_inline_and_summary_cache_is_disposable(tmp_path):
    old = _request("old")
    unsettled = replace(
        _request("unknown"),
        effects=(
            EffectIntent(
                "effect",
                "operation",
                AssistantExecutionRef("unknown", 1, ("i",), "a", "d", "t", "s"),
                0,
            ),
        ),
    )
    state = {
        "requests": [old.to_dict(), unsettled.to_dict()],
        "request_summaries": {"old": {"derived": True}, "unknown": {"derived": True}},
    }
    compact, manifest = compact_request_state(
        state, TranscriptManifest(), "s", AssistantTranscriptStore(str(tmp_path)), keep_recent_terminal=0
    )
    assert [r["request_id"] for r in compact["requests"]] == ["unknown"]
    assert list(compact["request_summaries"]) == ["unknown"]
    assert len(manifest.artifacts) == 1


def test_recent_terminal_order_uses_completion_event_not_request_creation_order(tmp_path):
    state = {
        "requests": [
            _request("first-created-last-completed").to_dict(),
            _request("later-created-earlier-completed").to_dict(),
        ],
        "lifecycle_events": [
            {
                "sequence": 5,
                "request_ids": ["later-created-earlier-completed"],
                "before": {"status": "open"},
                "after": {"status": "completed"},
            },
            {
                "sequence": 10,
                "request_ids": ["first-created-last-completed"],
                "before": {"status": "open"},
                "after": {"status": "completed"},
            },
        ],
    }
    compact, _ = compact_request_state(
        state, TranscriptManifest(), "s", AssistantTranscriptStore(str(tmp_path)), keep_recent_terminal=1
    )
    assert compact["requests"][0]["request_id"] == "first-created-last-completed"


@pytest.mark.parametrize("damage", ["scope", "version", "position", "digest", "bytes"])
def test_damaged_archive_fails_closed_instead_of_dropping_old_request(tmp_path, damage):
    store = AssistantTranscriptStore(str(tmp_path))
    compact, manifest = compact_request_state(
        {"requests": [_request("old").to_dict()]}, TranscriptManifest(), "s", store, keep_recent_terminal=0
    )
    entry = compact["request_archives"][0]
    if damage == "scope":
        entry["session_id"] = "other"
    elif damage == "version":
        entry["version"] = 99
    elif damage == "position":
        entry["position"] = 100
    elif damage == "digest":
        entry["request_digest"] = "incorrect"
    else:
        (tmp_path / manifest.artifacts[0].path).write_bytes(b"corruption")
    with pytest.raises((RequestError, BackupVerificationError, PathBoundaryError)):
        hydrate_request_state(compact, "s", store)


def test_without_store_no_archival_loss_or_spurious_empty_request_key():
    assert hydrate_request_state({}, "s", None) == {}
    assert compact_request_state({}, TranscriptManifest(), "s", None) == ({}, TranscriptManifest())
    original = {"requests": [_request("old").to_dict()]}
    with pytest.raises(RequestError, match="附件存储"):
        compact_request_state(original, TranscriptManifest(), "s", None, keep_recent_terminal=0)
    assert len(original["requests"]) == 1


def test_lost_session_cas_keeps_previous_archive_index_and_full_details(tmp_path):
    from transbridge.application.contracts import DomainError, RequestContext
    from transbridge.application.sessions import ControllerSnapshot, SessionSnapshot
    from transbridge.application.tasks.models import OwnerRef
    from transbridge.persistence.session_lifecycle import V2SessionSnapshotRepository
    from transbridge.persistence.v2 import SessionId, SessionRef, SessionRepository
    from transbridge.persistence.v2.filesystem import OsPersistenceFilesystem

    store = AssistantTranscriptStore(str(tmp_path))
    original_state = {"requests": [_request("old").to_dict()]}
    state, manifest = compact_request_state(original_state, TranscriptManifest(), "s", store, keep_recent_terminal=0)
    snapshot = SessionSnapshot(
        ref=SessionRef(SessionId("s")),
        name="Session",
        owner=OwnerRef("owner", "gui", session_id="s"),
        messages=(),
        backend_history=(),
        backend_summary=None,
        controller=ControllerSnapshot(),
        project_id=None,
        variant_id=None,
        approvals=(),
        jobs=(),
        revision=0,
        created_at="2026-09-12T00:00:00Z",
        last_active_at="2026-09-12T00:00:00Z",
        assistant_state=state,
        transcript_manifest=manifest.to_dict(),
    )
    repository = SessionRepository(str(tmp_path), OsPersistenceFilesystem())
    repository.save(snapshot.ref, snapshot.to_dto())
    changed = hydrate_request_state(state, "s", store)
    changed["requests"][0]["stop_reason"] = "late audit detail"
    next_state, next_manifest = compact_request_state(changed, manifest, "s", store, keep_recent_terminal=0)
    candidate = replace(snapshot, revision=1, assistant_state=next_state, transcript_manifest=next_manifest.to_dict())
    winner = replace(snapshot, revision=1, name="Concurrent metadata change")
    repository.save(snapshot.ref, winner.to_dto())
    with pytest.raises(DomainError, match="Session changed"):
        V2SessionSnapshotRepository(repository).save(
            candidate, expected_revision=0, context=RequestContext("owner", session_id="s")
        )
    persisted = SessionSnapshot.from_dto(repository.load(snapshot.ref).value)
    assert persisted == winner
    assert hydrate_request_state(persisted.assistant_data(), "s", store)["requests"] == original_state["requests"]
    assert all((tmp_path / ref.path).exists() for ref in (*manifest.artifacts, *next_manifest.artifacts))
