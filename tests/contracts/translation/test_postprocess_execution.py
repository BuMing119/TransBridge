from __future__ import annotations

from transbridge.application.contracts import Diagnostic, ErrorCategory, OperationOutcome, RequestContext
from transbridge.application.io import EntryKey, EntryRevision, SourceNamespace, StagePolicy
from transbridge.application.io.publish import ImmediateCommitGuard
from transbridge.application.translation import (
    InMemoryTranslationCheckpointPort,
    PostProcessExecutionService,
    PostProcessStageOutcome,
    PostProcessWorkload,
    TranslationInput,
)
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection


class AuditedCollection(TranslationEntryCollection):
    def __init__(self, entries):
        super().__init__(entries)
        self.apply_count = 0

    def apply(self, change_set, context):
        self.apply_count += 1
        return super().apply(change_set, context)


def _input() -> TranslationInput:
    return TranslationInput(
        EntryKey(SourceNamespace("fixture"), "one"),
        EntryRevision(0),
        "source",
        "draft",
        1,
    )


def _collection(entry: TranslationInput) -> AuditedCollection:
    return AuditedCollection((
        TranslationEntry(
            id="one",
            key="one",
            original=entry.original,
            translation=entry.translation,
            stage=entry.stage,
            context=entry.context,
            entry_key=entry.entry_key,
            revision=entry.revision,
        ),
    ))


def _context() -> RequestContext:
    return RequestContext(
        "owner",
        run_id="post-run",
        permissions=frozenset({"entry.translation.write", "entry.stage.write"}),
    )


def test_execution_commits_accepted_candidates_once_and_reuses_report_snapshot() -> None:
    entry = _input()
    collection = _collection(entry)

    def polish(candidates):
        return PostProcessStageOutcome(
            "polish",
            tuple(candidate.with_text("polished", "polish") for candidate in candidates),
        )

    execution = PostProcessExecutionService(PostProcessWorkload((polish,), stage_policy=StagePolicy())).execute(
        run_id="post-run",
        entries=(entry,),
        collection=collection,
        context=_context(),
        commit_guard=ImmediateCommitGuard("post-run"),
        commit_checkpoint=InMemoryTranslationCheckpointPort(),
    )

    assert execution.report_result.outcome is OperationOutcome.COMPLETED
    assert execution.commit_result is not None
    assert execution.commit_result.outcome is OperationOutcome.COMPLETED
    assert collection.apply_count == 1
    assert collection.get("one").translation == "polished"
    assert execution.report_result.value.to_dict()["entries"][0]["candidate"] == "polished"


def test_rejected_candidate_never_reaches_formal_collection() -> None:
    entry = _input()
    collection = _collection(entry)

    def reject(candidates):
        return PostProcessStageOutcome(
            "arbitrate",
            tuple(candidate.with_accepted(False) for candidate in candidates),
        )

    execution = PostProcessExecutionService(PostProcessWorkload((reject,), stage_policy=StagePolicy())).execute(
        run_id="post-run",
        entries=(entry,),
        collection=collection,
        context=_context(),
        commit_guard=ImmediateCommitGuard("post-run"),
        commit_checkpoint=InMemoryTranslationCheckpointPort(),
    )

    assert execution.commit_result is None
    assert collection.apply_count == 0
    assert collection.get("one").translation == "draft"


def test_partial_report_remains_partial_after_successful_candidate_commit() -> None:
    entry = _input()
    collection = _collection(entry)

    def partial(candidates):
        return PostProcessStageOutcome(
            "polish",
            tuple(candidate.with_text("salvaged", "polish") for candidate in candidates),
            (Diagnostic("POLISH_PARTIAL", "Some inputs were unavailable.", category=ErrorCategory.INTERNAL),),
        )

    execution = PostProcessExecutionService(PostProcessWorkload((partial,), stage_policy=StagePolicy())).execute(
        run_id="post-run",
        entries=(entry,),
        collection=collection,
        context=_context(),
        commit_guard=ImmediateCommitGuard("post-run"),
        commit_checkpoint=InMemoryTranslationCheckpointPort(),
    )

    assert execution.report_result.outcome is OperationOutcome.PARTIAL
    assert execution.commit_result is not None
    assert execution.commit_result.outcome is OperationOutcome.COMPLETED
    assert execution.outcome is OperationOutcome.PARTIAL
    assert collection.apply_count == 1


def test_failed_execution_exposes_terminal_snapshot_without_committing() -> None:
    entry = _input()
    collection = _collection(entry)

    def explodes(candidates):
        raise RuntimeError("boom")

    execution = PostProcessExecutionService(PostProcessWorkload((explodes,), stage_policy=StagePolicy())).execute(
        run_id="post-run",
        entries=(entry,),
        collection=collection,
        context=_context(),
        commit_guard=ImmediateCommitGuard("post-run"),
        commit_checkpoint=InMemoryTranslationCheckpointPort(),
    )

    assert execution.report_result.outcome is OperationOutcome.FAILED
    assert execution.report_result.value is None
    assert execution.report_snapshot is not None
    assert execution.report_snapshot.outcome is OperationOutcome.FAILED
    assert execution.commit_result is None
    assert collection.apply_count == 0
