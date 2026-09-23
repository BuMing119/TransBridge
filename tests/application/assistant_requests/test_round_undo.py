"""Round undo evidence uses real Session transactions and explicit failure states."""

from dataclasses import replace

import pytest

from tests.application.assistant_context.test_history_queries import _request
from tests.application.assistant_requests.test_request_repository import _snapshot
from tests.application.projects.test_assistant_undo import _case, _service
from transbridge.application.assistant_requests.models import (
    AssistantExecutionRef,
    EffectIntent,
    EffectStatus,
    RequestError,
    RequestStatus,
)
from transbridge.application.assistant_requests.round_undo import RoundUndoService
from transbridge.application.assistant_requests.transcript import TranscriptManifest
from transbridge.application.contracts import DomainError, ErrorCategory, OperationResult
from transbridge.persistence.v2.variant import VariantChangeSet

pytest_plugins = ["tests.application.assistant_requests.test_request_repository"]


def _setup(composed):
    services, requests, base_context = composed
    _, before, after = _case()
    harness = _service(before, persisted_revision=before.revision)
    context = replace(
        base_context,
        session_id=None,
        project_id=before.ref.project_id.value,
        variant_id=before.ref.identity.value,
    )
    assert services.gui_session_commands.create_and_activate("Scoped undo", context).is_success
    context = replace(context, session_id=services.session_lifecycle.active.aggregate.ref.identity.value)
    identity = _request(requests, context)
    request = next(r for r in requests.requests(requests.state(context)) if r.request_id == identity)
    execution = AssistantExecutionRef(
        identity,
        request.revision,
        (request.items[0].item_id,),
        "attempt",
        "dispatch",
        "turn",
        session_id=context.session_id,
        work_round_id=request.work_round_id,
    )
    effect = EffectIntent("effect", "operation", execution, 0, status=EffectStatus.SUCCEEDED)
    requests.update_request(context, identity, lambda r: replace(r, effects=(effect,), status=RequestStatus.CANCELLED))
    undo = RoundUndoService(requests, harness.service)
    undo.register(context, execution, effect.effect_id, "edit_translation")
    return services, requests, context, harness, before, after, execution, undo


def _commit(case):
    _, _, context, harness, before, after, execution, undo = case

    def mutation():
        change = VariantChangeSet(
            before.ref,
            before.revision,
            after.source_fingerprints,
            after.entries,
            after.label_library,
            "commit",
        )
        return harness.service.commit_active_variant(
            change,
            replace(context, run_id="commit"),
            expected_project_revision=harness.service.active.project.envelope.revision,
        )

    result = undo.capture_variant_commit(context, execution, "effect", mutation)
    assert result.is_success


def _record(case):
    _, requests, context, _, _, _, execution, _ = case
    return requests.state(context)["undo_rounds"][execution.work_round_id]


def test_recorded_receipts_are_retained_in_real_session_manifest(composed):
    case = _setup(composed)
    services, requests, context, _, _, _, execution, undo = case
    _commit(case)
    commit = _record(case)["commits"][0]
    assert commit["status"] == "recorded"
    manifest = TranscriptManifest.from_dict(_snapshot(services, context).transcript_data())
    refs = [ref.to_dict() for ref in manifest.artifacts]
    assert commit["before"] in refs and commit["receipt"] in refs
    assert undo.preview(context, execution.work_round_id)["available"]
    assert requests.state(context)["ingress"][0]["message_id"] == execution.work_round_id


def test_missing_and_foreign_real_ingress_cannot_claim_effect(composed):
    case = _setup(composed)
    _, requests, context, _, _, _, execution, undo = case
    with pytest.raises(RequestError):
        undo.register(context, replace(execution, work_round_id="invented"), "forged", "edit_translation")
    other = _request(requests, context, "其他请求", "other-input")
    other_request = next(r for r in requests.requests(requests.state(context)) if r.request_id == other)
    with pytest.raises(RequestError):
        undo.register(
            context, replace(execution, work_round_id=other_request.work_round_id), "effect", "edit_translation"
        )


def test_mutation_exception_keeps_durable_pending_and_blocks_undo(composed):
    case = _setup(composed)
    _, _, context, _, _, _, execution, undo = case

    def fail():
        raise OSError("result delivery failed after potentially committing")

    with pytest.raises(OSError, match="potentially"):
        undo.capture_variant_commit(context, execution, "effect", fail)
    assert _record(case)["commits"][0]["status"] == "pending"
    assert not undo.preview(context, execution.work_round_id)["available"]
    with pytest.raises(RequestError, match="UNDO_NOT_AVAILABLE"):
        undo.undo(context, execution.work_round_id)


def test_receipt_storage_failure_does_not_replay_committed_mutation(composed, monkeypatch):
    case = _setup(composed)
    _, _, context, harness, _, after, execution, undo = case
    original = undo._store
    calls = []

    def store(context, payload):
        calls.append(payload)
        if len(calls) == 2:
            raise OSError("receipt disk full")
        return original(context, payload)

    monkeypatch.setattr(undo, "_store", store)
    with pytest.raises(OSError, match="disk full"):
        _commit(case)
    assert harness.service.active.variant.snapshot() == after
    assert _record(case)["commits"][0]["status"] == "pending"
    assert not undo.preview(context, execution.work_round_id)["available"]


@pytest.mark.parametrize("status", [EffectStatus.RUNNING, EffectStatus.OUTCOME_UNKNOWN])
def test_unsettled_effects_block_undo_even_after_user_stop(composed, status):
    case = _setup(composed)
    _, requests, context, _, _, _, execution, undo = case
    _commit(case)
    requests.update_request(
        context,
        execution.request_id,
        lambda r: replace(r, effects=(replace(r.effects[0], status=status),)),
    )
    assert not undo.preview(context, execution.work_round_id)["available"]


def test_active_unpaused_request_must_stop_before_undo(composed):
    case = _setup(composed)
    _, requests, context, _, _, _, execution, undo = case
    _commit(case)
    requests.update_request(context, execution.request_id, lambda r: replace(r, status=RequestStatus.OPEN))
    assert not undo.preview(context, execution.work_round_id)["available"]


def test_unsupported_effect_requires_explicit_partial_choice(composed):
    case = _setup(composed)
    _, requests, context, harness, before, _, execution, undo = case
    remote = EffectIntent("remote", "remote-operation", execution, 0, status=EffectStatus.SUCCEEDED)
    requests.update_request(context, execution.request_id, lambda r: replace(r, effects=(*r.effects, remote)))
    undo.register(context, execution, "remote", "upload_entries")
    _commit(case)
    assert undo.preview(context, execution.work_round_id)["limitations"]
    with pytest.raises(RequestError, match="UNDO_PARTIAL_CONFIRMATION_REQUIRED"):
        undo.undo(context, execution.work_round_id)
    result = undo.undo(context, execution.work_round_id, allow_partial=True)
    assert result["partial"]
    assert _record(case)["status"] == "partially_undone"
    assert harness.service.active.variant.snapshot().entries[0].translation == before.entries[0].translation


def test_unsupported_commit_on_covered_effect_is_not_hidden(composed):
    case = _setup(composed)
    _, requests, context, _, _, _, execution, undo = case
    _commit(case)

    def add_unsupported(state):
        state["undo_rounds"][execution.work_round_id]["commits"].append({
            "id": "structural",
            "effect_id": "effect",
            "status": "unsupported",
            "detail": "source changed",
        })

    requests.transact(context, add_unsupported)
    assert undo.preview(context, execution.work_round_id)["limitations"]
    with pytest.raises(RequestError, match="UNDO_PARTIAL_CONFIRMATION_REQUIRED"):
        undo.undo(context, execution.work_round_id)


def test_inverse_save_failure_leaves_undoing_and_never_replays(composed, monkeypatch):
    case = _setup(composed)
    _, _, context, harness, before, _, execution, undo = case
    _commit(case)
    monkeypatch.setattr(
        harness.service,
        "save_active",
        lambda context: OperationResult.failed(DomainError(ErrorCategory.INTERNAL, "SAVE_FAILED", "disk failure")),
    )
    with pytest.raises(RequestError, match="UNDO_SAVE_FAILED"):
        undo.undo(context, execution.work_round_id)
    snapshot = harness.service.active.variant.snapshot()
    assert snapshot.entries[0].translation == before.entries[0].translation
    assert _record(case)["status"] == "undoing"
    with pytest.raises(RequestError, match="UNDO_NOT_AVAILABLE"):
        undo.undo(context, execution.work_round_id)
    assert harness.service.active.variant.snapshot() == snapshot


def test_pending_intent_save_failure_prevents_business_mutation(composed, monkeypatch):
    case = _setup(composed)
    _, requests, context, harness, before, _, execution, undo = case

    def fail(*args, **kwargs):
        raise OSError("intent could not be saved")

    monkeypatch.setattr(requests, "transact", fail)
    with pytest.raises(OSError, match="intent"):
        undo.capture_variant_commit(context, execution, "effect", lambda: pytest.fail("mutation ran without intent"))
    assert harness.service.active.variant.snapshot() == before


def test_structural_commit_is_recorded_as_unsupported_not_silently_covered(composed):
    case = _setup(composed)
    _, _, context, harness, before, _, execution, undo = case

    def mutation():
        entry = replace(before.entries[0], entry_key=replace(before.entries[0].entry_key, local_key="new"))
        change = VariantChangeSet(
            before.ref,
            before.revision,
            before.source_fingerprints,
            (*before.entries, entry),
            before.label_library,
            "structural",
        )
        return harness.service.commit_active_variant(
            change,
            replace(context, run_id="structural"),
            expected_project_revision=harness.service.active.project.envelope.revision,
        )

    assert undo.capture_variant_commit(context, execution, "effect", mutation).is_success
    assert _record(case)["commits"][0]["status"] == "unsupported"
    preview = undo.preview(context, execution.work_round_id)
    assert not preview["available"] and preview["limitations"]


def test_final_session_save_failure_after_inverse_persist_blocks_replay(composed, monkeypatch):
    case = _setup(composed)
    _, requests, context, harness, before, _, execution, undo = case
    _commit(case)
    original = requests.transact

    def transact(*args, **kwargs):
        cause = kwargs.get("cause")
        if cause is not None and cause.operation == "round.undone":
            raise OSError("final receipt persistence failed")
        return original(*args, **kwargs)

    monkeypatch.setattr(requests, "transact", transact)
    with pytest.raises(OSError, match="final receipt"):
        undo.undo(context, execution.work_round_id)
    assert _record(case)["status"] == "undoing"
    assert harness.service.active.variant.snapshot().entries[0].translation == before.entries[0].translation
    assert not undo.preview(context, execution.work_round_id)["available"]


def test_capture_design_error_is_not_reclassified_as_unsupported(composed, monkeypatch):
    case = _setup(composed)
    _, _, context, _, _, _, execution, undo = case

    def broken(*args):
        raise RuntimeError("broken capture contract")

    monkeypatch.setattr("transbridge.application.assistant_requests.round_undo.capture_variant_undo", broken)
    with pytest.raises(RuntimeError, match="capture contract"):
        _commit(case)
    assert _record(case)["commits"][0]["status"] == "pending"
    assert not undo.preview(context, execution.work_round_id)["available"]


def test_intervening_commit_cannot_be_attributed_to_assistant_round(composed):
    case = _setup(composed)
    _, _, context, harness, before, after, execution, undo = case

    def mutation():
        for revision, text in ((before.revision, "user change"), (before.revision + 1, "assistant change")):
            current = harness.service.active.variant.snapshot()
            changed = replace(current.entries[0], translation=text, revision=current.entries[0].revision.next())
            change = VariantChangeSet(
                before.ref,
                revision,
                after.source_fingerprints,
                (changed, *current.entries[1:]),
                current.label_library,
                "commit",
            )
            result = harness.service.commit_active_variant(
                change,
                replace(context, run_id="commit"),
                expected_project_revision=harness.service.active.project.envelope.revision,
            )
            assert result.is_success
        return result

    assert undo.capture_variant_commit(context, execution, "effect", mutation).is_success
    assert _record(case)["commits"][0]["status"] == "unresolved"
    assert not undo.preview(context, execution.work_round_id)["available"]
