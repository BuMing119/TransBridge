from __future__ import annotations

from dataclasses import replace

import pytest

from transbridge.ai_translator.translation_entry_outcomes import build_translation_entry_outcomes
from transbridge.application.contracts import (
    Diagnostic,
    DiagnosticSeverity,
    ErrorCategory,
    OperationOutcome,
    OperationResult,
)
from transbridge.application.io import EntryKey, SourceNamespace
from transbridge.application.translation import PostProcessCandidate, PostProcessStageOutcome, ReportSnapshot
from transbridge.application.translation.workload_models import TranslationInput
from transbridge.converter.translation_entry import TranslationEntry


def _entry(namespace="source:a", text="原译文"):
    return TranslationEntry(
        "same-id",
        "same-key",
        "Source",
        text,
        1,
        "BOOK:DESC",
        entry_key=EntryKey(SourceNamespace(namespace), "same-key"),
    )


def _input(entry):
    return TranslationInput(
        entry.identity, entry.revision, entry.original, entry.translation, entry.stage, entry.context
    )


def _commit(*entries):
    return OperationResult.completed({"applied_keys": [entry.identity.to_dict() for entry in entries]})


def _postprocess(entry, *, phases=("proofread",), accepted=True, diagnostics=(), changed=False):
    candidate = PostProcessCandidate(
        "run",
        entry.identity,
        entry.revision,
        entry.original,
        entry.translation,
        "校对译文" if changed else entry.translation,
        2,
        phases=phases,
        accepted=accepted,
    )
    return ReportSnapshot(
        "transbridge.postprocess-report.v1",
        "run",
        OperationOutcome.PARTIAL if diagnostics else OperationOutcome.COMPLETED,
        1,
        int(accepted),
        (candidate,),
        tuple(PostProcessStageOutcome(phase, (candidate,)) for phase in phases),
        diagnostics,
    )


@pytest.mark.parametrize("fault", ["missing_stage", "rejected", "call_failure", "write_failure"])
def test_postprocess_must_complete_all_checks_and_guarded_writes(fault):
    entry = _entry()
    snapshot = _postprocess(
        entry,
        phases=() if fault == "missing_stage" else ("proofread",),
        accepted=fault != "rejected",
        diagnostics=(Diagnostic("CALL_FAILED", "provider failed", category=ErrorCategory.EXTERNAL),)
        if fault == "call_failure"
        else (),
        changed=fault == "write_failure",
    )
    result = build_translation_entry_outcomes(
        [_input(entry)],
        [entry],
        started={entry.identity},
        finished={entry.identity},
        accepted={entry.identity},
        commit_result=_commit(entry),
        cancelled=False,
        post_process_enabled=True,
        required_phases=("proofread",),
        post_process_snapshot=snapshot,
    )[entry.identity]
    assert result.status == "failed"
    assert result.text == "原译文"
    assert result.reason


def test_no_change_postprocess_is_success_without_an_additional_write():
    entry = _entry()
    result = build_translation_entry_outcomes(
        [_input(entry)],
        [entry],
        started={entry.identity},
        finished={entry.identity},
        accepted={entry.identity},
        commit_result=_commit(entry),
        cancelled=False,
        post_process_enabled=True,
        required_phases=("proofread",),
        post_process_snapshot=_postprocess(entry),
    )[entry.identity]
    assert result.status == "succeeded"
    assert result.reason == ""


@pytest.mark.parametrize("committed", [True, False])
def test_translation_retains_questionable_reason_only_after_successful_postprocess_commit(committed):
    entry = _entry()
    snapshot = _postprocess(entry)
    candidate = snapshot.candidates[0].with_report_details({"questionable": True})
    snapshot = replace(
        snapshot,
        candidates=(candidate,),
        stage_outcomes=(PostProcessStageOutcome("proofread", (candidate,)),),
        diagnostics=(
            Diagnostic(
                "PROOFREAD_SYNTAX_REVIEW_REQUIRED",
                "标记存在差异，已保留原译文并标为有疑问",
                severity=DiagnosticSeverity.WARNING,
                category=ErrorCategory.INPUT,
                details=(("entry_key", entry.identity.to_dict()),),
            ),
        ),
    )
    result = build_translation_entry_outcomes(
        [_input(entry)],
        [entry],
        started={entry.identity},
        finished={entry.identity},
        accepted={entry.identity},
        commit_result=_commit(entry),
        cancelled=False,
        post_process_enabled=True,
        required_phases=("proofread",),
        post_process_snapshot=snapshot,
        post_process_commit=_commit(entry) if committed else None,
    )[entry.identity]
    assert result.status == ("succeeded" if committed else "failed")
    assert ("PROOFREAD_SYNTAX_REVIEW_REQUIRED" in result.reason) is committed


def test_namespace_separates_identical_ids_and_scoped_postprocess_failures():
    first, second = _entry(), _entry("source:b")
    first_snapshot = _postprocess(first)
    second_snapshot = _postprocess(second)
    candidates = first_snapshot.candidates + second_snapshot.candidates
    snapshot = replace(
        first_snapshot,
        candidates=candidates,
        input_count=2,
        accepted_count=2,
        stage_outcomes=(PostProcessStageOutcome("proofread", candidates),),
        diagnostics=(
            Diagnostic(
                "CALL_FAILED",
                "provider failed",
                category=ErrorCategory.EXTERNAL,
                details=(("entry_keys", [second.identity.serialize()]),),
            ),
        ),
    )
    keys = {first.identity, second.identity}
    results = build_translation_entry_outcomes(
        [_input(first), _input(second)],
        [first, second],
        started=keys,
        finished=keys,
        accepted=keys,
        commit_result=_commit(first, second),
        cancelled=False,
        post_process_enabled=True,
        required_phases=("proofread",),
        post_process_snapshot=snapshot,
    )
    assert results[first.identity].status == "succeeded"
    assert results[second.identity].status == "failed"


def test_cancel_preserves_failed_and_not_started_distinction():
    failed, active, waiting, candidate = (
        _entry(f"source:{name}") for name in ("failed", "active", "waiting", "candidate")
    )
    entries = [failed, active, waiting, candidate]
    results = build_translation_entry_outcomes(
        [_input(entry) for entry in entries],
        entries,
        started={failed.identity, active.identity, candidate.identity},
        finished={failed.identity, candidate.identity},
        accepted={candidate.identity},
        commit_result=None,
        cancelled=True,
    )
    assert results[failed.identity].status == "failed"
    assert results[active.identity].status == "cancelled"
    assert results[waiting.identity].status == "not_started"
    assert results[candidate.identity].status == "cancelled"
