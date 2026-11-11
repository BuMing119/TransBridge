from dataclasses import replace

import pytest

from tests.contracts.translation.test_postprocess_execution import _collection, _input
from transbridge.application.contracts import Diagnostic, ErrorCategory, OperationOutcome, RequestContext
from transbridge.application.io import StagePolicy
from transbridge.application.io.publish import ImmediateCommitGuard
from transbridge.application.translation import (
    InMemoryPostProcessCheckpointPort,
    InMemoryTranslationCheckpointPort,
    PostProcessExecutionService,
    PostProcessStageOutcome,
    PostProcessWorkload,
)


class FailingStorage(InMemoryPostProcessCheckpointPort):
    def save(self, checkpoint):
        raise OSError("checkpoint unwritable; api_key=private-test-secret")


@pytest.mark.parametrize(
    "phase_result", ["success", "exception", "failed", "incomplete", "cancelled", "cancel_before", "empty"]
)
def test_storage_failure_returns_terminal_report_and_keeps_original_diagnostic(phase_result):
    later = []

    def stage(candidates):
        if phase_result == "exception":
            raise RuntimeError("original proofread failure")
        if phase_result in {"failed", "incomplete", "cancelled"}:
            diagnostic = Diagnostic(
                "ORIGINAL_PHASE_ERROR",
                "original proofread failure",
                category={"cancelled": ErrorCategory.CANCELLED, "incomplete": ErrorCategory.INPUT}.get(
                    phase_result, ErrorCategory.EXTERNAL
                ),
                retryable=phase_result == "incomplete",
            )
            return PostProcessStageOutcome(
                "proofread", tuple(replace(c, accepted=False) for c in candidates), (diagnostic,)
            )
        return PostProcessStageOutcome("proofread", tuple(c.with_text("Reviewed", "proofread") for c in candidates))

    def next_stage(candidates):
        later.append(True)
        return PostProcessStageOutcome("next", candidates)

    stages = () if phase_result == "empty" else (stage, next_stage)
    terminal = PostProcessWorkload(
        stages,
        stage_policy=StagePolicy(),
        stage_names=() if phase_result == "empty" else ("proofread", "next"),
        checkpoint_port=FailingStorage(),
    ).run_with_snapshot("run", (_input(),), owner_id="owner", is_cancelled=lambda: phase_result == "cancel_before")
    expected = OperationOutcome.CANCELLED if phase_result in {"cancelled", "cancel_before"} else OperationOutcome.FAILED
    assert terminal.operation_result.outcome is terminal.report_snapshot.outcome is expected
    assert terminal.operation_result.value is None
    assert terminal.report_snapshot.candidates
    messages = " ".join(d.message for d in terminal.report_snapshot.diagnostics)
    assert "checkpoint unwritable" in messages
    assert "private-test-secret" not in str(terminal.report_snapshot.to_dict())
    assert any(d.code == "POSTPROCESS_CHECKPOINT_SAVE_FAILED" for d in terminal.report_snapshot.diagnostics)
    if phase_result in {"exception", "failed", "incomplete", "cancelled"}:
        assert "original proofread failure" in messages
    assert not later


def test_storage_failure_prevents_commit_and_preserves_reviewed_candidate():
    entry = _input()
    collection = _collection(entry)

    def reviewed(candidates):
        return PostProcessStageOutcome("proofread", tuple(c.with_text("Reviewed", "proofread") for c in candidates))

    result = PostProcessExecutionService(
        PostProcessWorkload(
            (reviewed,), stage_policy=StagePolicy(), stage_names=("proofread",), checkpoint_port=FailingStorage()
        )
    ).execute(
        run_id="run",
        entries=(entry,),
        collection=collection,
        context=RequestContext("owner", run_id="run"),
        commit_guard=ImmediateCommitGuard("run"),
        commit_checkpoint=InMemoryTranslationCheckpointPort(),
    )
    assert result.outcome is OperationOutcome.FAILED
    assert result.commit_result is None
    assert collection.apply_count == 0
    assert result.report_snapshot.candidates[0].text == "Reviewed"


def test_resume_after_storage_failure_only_repeats_the_unsaved_stage():
    class InterruptedStorage(InMemoryPostProcessCheckpointPort):
        interrupted = False

        def save(self, checkpoint):
            if checkpoint.completed_phases == ("refine", "polish") and not self.interrupted:
                self.interrupted = True
                raise OSError("storage temporarily unavailable")
            super().save(checkpoint)

    calls = []

    def stage(phase):
        def run(candidates):
            calls.append(phase)
            return PostProcessStageOutcome(phase, tuple(c.with_text(phase + c.text, phase) for c in candidates))

        return run

    port = InterruptedStorage()
    workload = PostProcessWorkload(
        (stage("refine"), stage("polish")),
        stage_policy=StagePolicy(),
        stage_names=("refine", "polish"),
        checkpoint_port=port,
        settings_fingerprint="a" * 64,
        auto_resume=True,
    )
    first = workload.run_with_snapshot("run", (_input(),), owner_id="owner")
    assert first.operation_result.outcome is OperationOutcome.FAILED
    assert port.load("run").completed_phases == ("refine",)
    second = workload.run_with_snapshot("run", (_input(),), owner_id="owner")
    assert second.operation_result.outcome is OperationOutcome.COMPLETED
    assert calls == ["refine", "polish", "polish"]
    assert second.report_snapshot.candidates[0].text == first.report_snapshot.candidates[0].text
