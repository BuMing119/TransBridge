"""Automatic phase recovery preserves review evidence and retries failed phases."""

from dataclasses import replace

import pytest

from tests.contracts.translation.test_postprocess_checkpoint import _entry, _make
from transbridge.application.contracts import Diagnostic, DiagnosticSeverity, ErrorCategory
from transbridge.application.io import EntryRevision, StagePolicy
from transbridge.application.translation import (
    FilesystemPostProcessCheckpointPort,
    PostProcessStageOutcome,
    PostProcessWorkload,
)


def _workload(root, stages, *, settings="a" * 64):
    return PostProcessWorkload(
        stages,
        stage_policy=StagePolicy(),
        stage_names=("first", "second"),
        checkpoint_port=FilesystemPostProcessCheckpointPort(root),
        settings_fingerprint=settings,
        auto_resume=True,
    )


def test_new_workload_resumes_after_last_successful_phase_with_evidence(tmp_path):
    calls = []
    details = {"review_status": "needs_review", "review_reason": "名称有歧义", "detected_issues": ["term"]}

    def first(candidate):
        calls.append("first")
        return candidate.with_text("reviewed", "first").with_report_details(details)

    def failed(candidates):
        calls.append("failed")
        return PostProcessStageOutcome(
            "second",
            tuple(candidate.with_text("incomplete", "second") for candidate in candidates),
            (Diagnostic("MODEL_FAILED", "retry", category=ErrorCategory.EXTERNAL),),
        )

    result = _workload(tmp_path, (_make("first", first), failed)).run("run", (_entry(),), owner_id="owner")
    assert result.outcome.value == "partial"
    saved = FilesystemPostProcessCheckpointPort(tmp_path).load("run")
    assert saved.completed_phases == ("first",)
    assert saved.entries[0].text == "reviewed"
    assert dict(saved.entries[0].report_details) == details

    def second(candidate):
        calls.append("second")
        assert dict(candidate.report_details) == details
        return candidate.with_text(candidate.text + " completed", "second")

    resumed = _workload(
        tmp_path,
        (_make("first", lambda _: pytest.fail("completed stage replayed")), _make("second", second)),
    ).run("run", (_entry(),), owner_id="owner")
    assert resumed.outcome.value == "completed"
    assert resumed.value.candidates[0].text == "reviewed completed"
    assert dict(resumed.value.candidates[0].report_details) == details
    assert calls == ["first", "failed", "second"]


@pytest.mark.parametrize("tamper", ["text", "report_details"])
def test_automatic_resume_rejects_changed_settings_input_and_corrupt_review(tmp_path, tamper):
    stages = (_make("first", lambda candidate: candidate), _make("second", lambda candidate: candidate))
    _workload(tmp_path, stages).run("run", (_entry(),), owner_id="owner")
    with pytest.raises(ValueError, match="settings"):
        _workload(tmp_path, stages, settings="b" * 64).run("run", (_entry(),), owner_id="owner")
    with pytest.raises(ValueError, match="identity"):
        _workload(tmp_path, stages).run("run", (_entry(revision=10),), owner_id="owner")
    port = FilesystemPostProcessCheckpointPort(tmp_path)
    checkpoint = port.load("run")
    changed = {"text": "tampered"} if tamper == "text" else {"report_details": (("review_reason", "tampered"),)}
    port.save(
        replace(checkpoint, revision=checkpoint.revision + 1, entries=(replace(checkpoint.entries[0], **changed),))
    )
    with pytest.raises(ValueError, match="integrity"):
        _workload(tmp_path, stages).run("run", (_entry(),), owner_id="owner")


def test_stable_phase_identity_rebases_only_reconstructed_revision(tmp_path):
    from transbridge.application.translation.postprocess_resume import ExecutionPostProcessCheckpoint

    calls = []

    def stage(candidate):
        calls.append(candidate.run_id)
        return candidate.with_text("reviewed", "first")

    def run(run_id, item):
        port = ExecutionPostProcessCheckpoint(
            tmp_path,
            run_id=run_id,
            identity="task-source",
            settings="a" * 64,
            entries=(item,),
            stages=("first",),
        )
        workload = PostProcessWorkload(
            (_make("first", stage),),
            stage_policy=StagePolicy(),
            stage_names=("first",),
            checkpoint_port=port,
            settings_fingerprint="a" * 64,
            auto_resume=True,
        )
        return workload.run(run_id, (item,), owner_id="owner")

    assert run("initial", _entry()).value.candidates[0].text == "reviewed"
    result = run("continued", replace(_entry(), revision=EntryRevision(20)))
    assert calls == ["initial"]
    assert result.value.candidates[0].run_id == "continued"
    assert result.value.candidates[0].before_revision == EntryRevision(20)
    run("changed", replace(_entry(), original="changed source"))
    assert calls == ["initial", "changed"]


@pytest.mark.parametrize("later_failure", [False, True])
def test_partial_batch_keeps_valid_results_without_checkpointing_an_incomplete_prefix(tmp_path, later_failure):
    calls = []
    entries = (_entry(), replace(_entry(), entry_key=replace(_entry().entry_key, local_key="two")))
    port = FilesystemPostProcessCheckpointPort(tmp_path)

    def incomplete(candidates):
        calls.append("incomplete")
        return PostProcessStageOutcome(
            "second",
            (candidates[0].with_text("valid", "second"), candidates[1].with_accepted(False)),
            (Diagnostic("MISSING", "Retry missing response", severity=DiagnosticSeverity.WARNING, retryable=True),),
        )

    def last(candidates):
        calls.append("last")
        if later_failure:
            raise TimeoutError("interrupted after partial batch")
        return PostProcessStageOutcome("last", candidates)

    def workload(second, final):
        return PostProcessWorkload(
            (_make("first", lambda item: item.with_text("prepared", "first")), second, final),
            stage_policy=StagePolicy(),
            stage_names=("first", "second", "last"),
            checkpoint_port=port,
            settings_fingerprint="a" * 64,
            auto_resume=True,
        )

    result = workload(incomplete, last).run("run", entries, owner_id="owner")
    assert calls == ["incomplete", "last"]
    assert result.outcome.value == ("failed" if later_failure else "completed")
    if not later_failure:
        assert result.counts.succeeded == 1
        assert result.value.candidates[0].text == "valid"
    saved = port.load("run")
    assert saved.completed_phases == ("first",)
    assert all(item.text == "prepared" for item in saved.entries)
    resumed = workload(
        _make("second", lambda item: item.with_text("retried", "second")),
        _make("last", lambda item: item),
    ).run("run", entries, owner_id="owner")
    assert resumed.counts.succeeded == 2
    assert port.load("run").completed_phases == ("first", "second", "last")
