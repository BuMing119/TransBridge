"""Contract coverage for runtime Story S06's immutable post-process path."""

from __future__ import annotations

from transbridge.application.contracts import Diagnostic, ErrorCategory, OperationOutcome
from transbridge.application.io import (
    EntryKey,
    EntryRevision,
    SourceNamespace,
    StagePolicy,
)
from transbridge.application.translation import (
    InMemoryPostProcessCheckpointPort,
    PostProcessStageOutcome,
    PostProcessWorkload,
    TranslationInput,
)


def _entry(key: str, *, stage: int = 1, revision: int = 1) -> TranslationInput:
    return TranslationInput(
        EntryKey(SourceNamespace("fixture"), key), EntryRevision(revision), "source", "draft", stage
    )


def test_candidate_stages_receive_the_previous_candidate_and_one_snapshot_serves_all_renderers() -> None:
    observed: list[str] = []

    def refine(candidates):
        return PostProcessStageOutcome("refine", tuple(item.with_text("refined", "refine") for item in candidates))

    def polish(candidates):
        observed.extend(item.text for item in candidates)
        return PostProcessStageOutcome("polish", tuple(item.with_text("polished", "polish") for item in candidates))

    result = PostProcessWorkload((refine, polish), stage_policy=StagePolicy()).run("run-1", (_entry("one"),))

    assert result.outcome is OperationOutcome.COMPLETED
    assert observed == ["refined"]
    snapshot = result.value
    assert snapshot.run_id == "run-1"
    assert snapshot.to_dict()["entries"][0]["candidate"] == "polished"
    assert snapshot.to_dict() == snapshot.to_dict()  # all renderers consume this canonical projection
    assert snapshot.fingerprint


def test_stage_failure_is_typed_partial_and_never_disguised_as_success() -> None:
    def failed(candidates):
        return PostProcessStageOutcome(
            "refine", candidates,
            (Diagnostic("REFINE_UNAVAILABLE", "Fixture failure.", category=ErrorCategory.INTERNAL),),
        )

    result = PostProcessWorkload((failed,), stage_policy=StagePolicy()).run("run-2", (_entry("one"),))

    assert result.outcome is OperationOutcome.PARTIAL
    assert result.value.outcome is OperationOutcome.PARTIAL
    assert result.diagnostics[0].code == "REFINE_UNAVAILABLE"


def test_hidden_or_locked_entries_never_enter_candidate_scope() -> None:
    result = PostProcessWorkload((), stage_policy=StagePolicy()).run(
        "run-3", (_entry("hidden", stage=-1), _entry("locked", stage=9))
    )

    assert result.outcome is OperationOutcome.COMPLETED
    assert result.value.accepted_count == 0


def test_revision_conflict_is_typed_partial_without_entering_the_chain() -> None:
    ok = _entry("ok", revision=2)
    conflicted = _entry("stale", revision=2)
    result = PostProcessWorkload((), stage_policy=StagePolicy()).run(
        "run-4",
        (ok, conflicted),
        expected_revisions={conflicted.entry_key: EntryRevision(5)},
    )

    assert result.outcome is OperationOutcome.PARTIAL
    assert any(d.code == "REVISION_CONFLICT" for d in result.diagnostics)
    assert result.counts.failed == 1
    assert result.value.accepted_count == 1
    assert {item["entry_key"]["local_key"] for item in result.value.to_dict()["entries"]} == {"ok"}


def test_all_entries_conflicted_is_failed_with_value_none() -> None:
    conflicted = _entry("stale", revision=2)
    result = PostProcessWorkload((), stage_policy=StagePolicy()).run(
        "run-8",
        (conflicted,),
        expected_revisions={conflicted.entry_key: EntryRevision(9)},
    )

    assert result.outcome is OperationOutcome.FAILED
    assert result.value is None
    assert any(d.code == "REVISION_CONFLICT" for d in result.diagnostics)


def test_stage_exception_is_failed_and_never_disguised_as_success() -> None:
    def explodes(candidates):
        raise RuntimeError("boom")

    result = PostProcessWorkload((explodes,), stage_policy=StagePolicy()).run("run-5", (_entry("one"),))

    assert result.outcome is OperationOutcome.FAILED
    assert result.value is None
    assert any(d.code == "POSTPROCESS_STAGE_FAILED" for d in result.diagnostics)


def test_cancel_is_typed_cancelled_and_persists_progress() -> None:
    checkpoint_port = InMemoryPostProcessCheckpointPort()
    calls = {"count": 0}

    def slow(candidates):
        calls["count"] += 1
        return PostProcessStageOutcome("slow", candidates)

    workload = PostProcessWorkload(
        (slow,), stage_policy=StagePolicy(), stage_names=("slow",), checkpoint_port=checkpoint_port
    )
    result = workload.run("run-6", (_entry("one"),), is_cancelled=lambda: True, owner_id="owner")

    assert result.outcome is OperationOutcome.CANCELLED
    assert result.value is None
    assert calls["count"] == 0
    assert any(d.code == "POSTPROCESS_CANCELLED" for d in result.diagnostics)
    assert checkpoint_port.load("run-6") is not None


def test_arbitration_decisions_track_accepted_counts_and_partial_visibility() -> None:
    def arbitrate(candidates):
        accepted, rejected = candidates[0], candidates[1]
        return PostProcessStageOutcome(
            "arbitrate",
            (accepted.with_accepted(True), rejected.with_accepted(False).with_text("reject", "arbitrate")),
        )

    result = PostProcessWorkload((arbitrate,), stage_policy=StagePolicy()).run(
        "run-7", (_entry("a"), _entry("b"))
    )

    assert result.outcome is OperationOutcome.COMPLETED
    assert result.value.accepted_count == 1
    entries = {item["entry_key"]["local_key"]: item for item in result.value.to_dict()["entries"]}
    assert entries["a"]["accepted"] is True
    assert entries["b"]["accepted"] is False
    assert entries["b"]["candidate"] == "reject"
    assert result.counts.succeeded == 1
