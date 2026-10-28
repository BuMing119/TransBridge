from __future__ import annotations

from dataclasses import replace
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest

from transbridge.ai_translator.post_processor.proofread_pipeline import ProofreadResult
from transbridge.ai_translator.required_term_matching import match_required_terms
from transbridge.ai_translator.shared_term_pool import SharedTermConflict
from transbridge.ai_translator.translation_entry_outcomes import TranslationEntryOutcome
from transbridge.ai_translator.translator import TranslationResult
from transbridge.application.contracts import DiagnosticSeverity, OperationOutcome, RequestContext
from transbridge.application.io.identity import EntryKey, Provenance, SourceNamespace
from transbridge.application.io.mutation import ChangeSet, EntryPatch, MutationStatus
from transbridge.application.translation.entry_alias import ai_entry_id, ai_entry_key
from transbridge.application.translation.postprocess import PostProcessCandidate, ReportSnapshot
from transbridge.application.translation.task_entry_results import TaskEntryResults
from transbridge.application.translation.task_scope import SourceTask
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.tools.ai_translator.source_execution import SourceOutcome, build_source_snapshot
from transbridge.ui.tools.ai_translator.task_term_closure import close_task_terms, fail_task_term_closure


def _source(name="first", rows=None, *, qualified=False):
    rows = rows or [("a", "Visit Riverwood", "前往旧译名", "succeeded")]
    entries = tuple(
        TranslationEntry(
            key,
            key,
            original,
            text,
            2,
            "INFO:NAM1",
            entry_key=EntryKey(SourceNamespace(name), key, original if qualified else None),
        )
        for key, original, text, _status in rows
    )
    collection = TranslationEntryCollection(entries)
    task = SourceTask(name, name, f"{name}.esp", collection, entries, ())
    result = TranslationResult(
        success_count=sum(row[3] == "succeeded" for row in rows),
        failed_count=sum(row[3] == "failed" for row in rows),
        skipped_count=sum(row[3] == "not_started" for row in rows),
        new_dynamic_terms=7,
        entry_outcomes={
            entry.identity: TranslationEntryOutcome(entry.identity, row[3], entry.translation, entry.stage)
            for entry, row in zip(entries, rows, strict=True)
        },
        failed_entries=[
            f"{ai_entry_id(entry)}: old failure" for entry, row in zip(entries, rows, strict=True) if row[3] == "failed"
        ],
    )
    result.post_process_result = ReportSnapshot(
        "transbridge.postprocess-report.v1",
        "run",
        OperationOutcome.COMPLETED,
        len(entries),
        result.success_count,
        tuple(
            PostProcessCandidate(
                "run",
                entry.identity,
                entry.revision,
                entry.original,
                "before",
                entry.translation,
                entry.stage,
                accepted=row[3] == "succeeded",
                report_details=(("processing_status", row[3]),),
            )
            for entry, row in zip(entries, rows, strict=True)
        ),
        (),
        (),
        failure_count=result.failed_count,
        run_spec_summary={"translation_counts": {"succeeded": result.success_count, "failed": result.failed_count}},
    )
    return SourceOutcome(
        task,
        translation=result,
        failed_keys=tuple(ai_entry_key(entry) for entry, row in zip(entries, rows, strict=True) if row[3] == "failed"),
    )


def _matched(_task, entry):
    return match_required_terms(
        entry.original,
        {
            "Riverwood": ("Riverwood", "溪木镇", False),
            "River": ("River", "河流", False),
        },
    )


def _write(task, entry, text, *, stage=2):
    current = task.collection.get(entry.identity)
    run_id = uuid4().hex
    context = RequestContext(
        "closure-test", run_id=run_id, permissions=frozenset({"entry.translation.write", "entry.stage.write"})
    )
    changes = ChangeSet(
        run_id,
        (EntryPatch.create(entry.identity, translation=text, stage=stage),),
        ((entry.identity, current.revision),),
        Provenance(run_id, "closure-test", "test"),
    )
    assert task.collection.apply(changes, context).status is MutationStatus.APPLIED
    return TranslationEntryOutcome(entry.identity, "succeeded", text, stage)


def _repair(task, required):
    return {
        ai_entry_key(entry): _write(task, entry, "前往" + "、".join(required[ai_entry_key(entry)].values()))
        for entry in task.entries
        if ai_entry_key(entry) in required
    }


def _close(outcomes, **kwargs):
    return close_task_terms(
        outcomes,
        resolve_terms=kwargs.pop("resolve_terms", _matched),
        repair=kwargs.pop("repair", _repair),
        stop_event=kwargs.pop("stop_event", threading.Event()),
        **kwargs,
    )


def _request():
    return SimpleNamespace(run_id="run", config=SimpleNamespace(mixed_execution_order="parallel"))


def test_closure_repairs_earlier_source_and_preserves_counts_and_success_evidence():
    first = _source()
    second = _source("second", [("a", "Visit Riverwood", "前往溪木镇", "succeeded")])
    calls, progress = [], []

    def repair(task, required):
        calls.append((task.key, required))
        return _repair(task, required)

    closed = _close((first, second), repair=repair, progress=lambda *args: progress.append(args))

    assert calls == [("first", {"a": {"Riverwood": "溪木镇"}})]
    result = closed[0].translation
    assert (result.success_count, result.failed_count, result.new_dynamic_terms) == (1, 0, 7)
    assert result.entry_outcomes[first.task.entries[0].identity].text == "前往溪木镇"
    assert first.translation.entry_outcomes[first.task.entries[0].identity].text == "前往旧译名"
    assert first.task.collection.get(first.task.entries[0].identity).translation == "前往溪木镇"
    report = build_source_snapshot(closed[0], _request())
    assert report.candidates[0].text == "前往溪木镇" and report.candidates[0].accepted
    evidence = TaskEntryResults((first.task, second.task), "run")
    evidence.ingest(closed, evidence.entries)
    assert len(evidence.ready_keys) == 2
    assert progress[0][1] == "term_closure_load" and progress[-1][2:4] == (1, 1)
    assert progress[-1][1] == "term_closure_done"
    _close(closed, repair=lambda *_: pytest.fail("consistent results must not be repaired again"))


def test_closure_never_repairs_failed_cancelled_skipped_unselected_or_partial_word_matches():
    outcome = _source(
        rows=[
            ("success", "Visit Riverwood", "前往旧译名", "succeeded"),
            ("failed", "Visit Riverwood", "旧译名", "failed"),
            ("cancelled", "Visit Riverwood", "旧译名", "cancelled"),
            ("skipped", "Visit Riverwood", "旧译名", "not_started"),
            ("word", "Riverside", "河边", "succeeded"),
        ]
    )
    extra = replace(
        outcome.task.entries[0], id="extra", key="extra", entry_key=EntryKey(SourceNamespace("first"), "extra")
    )
    outcome.task.collection.add(extra)
    observed = []

    def repair(task, required):
        observed.extend(required)
        return _repair(task, required)

    (closed,) = _close((outcome,), repair=repair)
    assert observed == ["success"]
    for entry in outcome.task.entries[1:]:
        assert closed.translation.entry_outcomes[entry.identity] == outcome.translation.entry_outcomes[entry.identity]
    assert outcome.task.collection.get(extra.identity).translation == "前往旧译名"
    assert closed.failed_keys == ("failed",)


def test_scoped_alias_resolution_and_original_qualified_keys_are_authoritative():
    outcome = _source(
        rows=[
            ("same", "WhiterunHold", "旧一", "succeeded"),
            ("same", "Riverwood", "旧二", "succeeded"),
        ],
        qualified=True,
    )
    calls = []

    def scoped(_task, entry):
        # A manager can match an alias that differs from the canonical term.
        return {"Whiterun": "雪漫领"} if entry.original == "WhiterunHold" else {"Riverwood": "专属溪木镇"}

    def repair(task, required):
        calls.append(required)
        return _repair(task, required)

    (closed,) = _close((outcome,), resolve_terms=scoped, repair=repair)
    assert set(calls[0]) == {ai_entry_key(entry) for entry in outcome.task.entries}
    assert [row.text for row in closed.translation.entry_outcomes.values()] == ["前往雪漫领", "前往专属溪木镇"]


@pytest.mark.parametrize("failure", ["missing", "wrong_identity", "uncommitted", "syntax"])
def test_unverified_review_keeps_successful_translation_without_draft_leaks(failure):
    outcome = _source(rows=[("a", "Visit Riverwood <Alias=Hero>", "旧译名 <Alias=Hero>", "succeeded")])
    entry = outcome.task.entries[0]

    def repair(task, _required):
        if failure == "missing":
            return {}
        if failure == "wrong_identity":
            return {"a": TranslationEntryOutcome(EntryKey(SourceNamespace("other"), "a"), "succeeded", "溪木镇", 2)}
        if failure == "uncommitted":
            return {"a": TranslationEntryOutcome(entry.identity, "succeeded", "溪木镇 <Alias=Hero>", 2)}
        return {"a": _write(task, entry, "溪木镇")}

    (closed,) = _close((outcome,), repair=repair)
    result = closed.translation.entry_outcomes[entry.identity]
    assert result.status == "succeeded"
    assert dict(result.review_details)["review_status"] == "needs_review"
    assert closed.failed_keys == ()
    assert (closed.translation.success_count, closed.translation.failed_count) == (1, 0)
    assert outcome.task.collection.get(entry.identity).translation == entry.translation
    report = build_source_snapshot(closed, _request())
    assert report.candidates[0].accepted
    assert dict(report.candidates[0].report_details)["processing_status"] == "completed"
    assert dict(report.candidates[0].report_details)["review_status"] == "needs_review"
    evidence = TaskEntryResults((outcome.task,), "run")
    evidence.ingest((closed,), evidence.entries)
    assert evidence.ready_keys == {entry.identity} and not evidence.failed_keys


def test_review_exception_after_partial_draft_commit_rolls_back_and_marks_pending():
    outcome = _source()
    entry = outcome.task.entries[0]

    def repair(task, required):
        _repair(task, required)
        raise RuntimeError("provider failure after one batch")

    (closed,) = _close((outcome,), repair=repair)
    assert outcome.task.collection.get(entry.identity).translation == entry.translation
    result = closed.translation.entry_outcomes[entry.identity]
    assert result.status == "succeeded"
    assert "RuntimeError" in result.reason
    assert dict(result.review_details)["review_status"] == "needs_review"


def test_cancel_before_closure_preserves_text_and_marks_unscanned_entries_unfinished_without_sending():
    outcome = _source(
        rows=[
            ("a", "Visit Riverwood", "旧译名", "succeeded"),
            ("b", "Visit Riverwood", "溪木镇", "succeeded"),
        ]
    )
    stop = threading.Event()
    stop.set()
    (closed,) = _close(
        (outcome,),
        stop_event=stop,
        resolve_terms=lambda *_: pytest.fail("cancelled closure continued scanning"),
        repair=lambda *_: pytest.fail("cancelled closure sent a repair"),
    )
    first, second = outcome.task.entries
    assert closed.cancelled
    assert closed.translation.entry_outcomes[first.identity].status == "cancelled"
    assert closed.translation.entry_outcomes[second.identity].status == "cancelled"
    assert closed.translation.entry_outcomes[second.identity].text == "溪木镇"
    assert closed.failed_keys == ("a", "b")
    report = build_source_snapshot(closed, _request())
    assert report.outcome is OperationOutcome.CANCELLED
    assert dict(report.candidates[0].report_details)["processing_status"] == "cancelled"
    assert closed.translation.failed_count == 0
    evidence = TaskEntryResults((outcome.task,), "run")
    evidence.ingest((closed,), evidence.entries, cancelled=True)
    assert evidence.entries[first.identity].status == "cancelled"


def test_cancel_between_sources_preserves_completed_repair_and_stops_next_request():
    first, second = _source(), _source("second")
    stop = threading.Event()
    calls = []

    def repair(task, required):
        calls.append(task.key)
        repaired = _repair(task, required)
        stop.set()
        return repaired

    closed = _close((first, second), stop_event=stop, repair=repair)
    assert calls == ["first"]
    assert closed[0].translation.entry_outcomes[first.task.entries[0].identity].status == "succeeded"
    assert closed[1].translation.entry_outcomes[second.task.entries[0].identity].status == "cancelled"


def _polish_source():
    outcome = _source(
        rows=[
            ("accepted", "Visit Riverwood", "旧译名", "succeeded"),
            ("rejected", "Visit Riverwood", "旧译名", "succeeded"),
        ]
    )
    task = replace(outcome.task, translate_entries=(), polish_entries=outcome.task.entries)
    results = {
        entry.id: ProofreadResult(
            entry.id,
            entry.key,
            entry.translation,
            "润色旧译名",
            0.9,
            False,
            "原校对说明",
            "pass" if entry.id == "accepted" else "reject",
            processing_status="completed",
            candidate_translation="润色旧译名",
            identity=entry.identity,
        )
        for entry in task.entries
    }
    return SourceOutcome(task, polish=results)


@pytest.mark.parametrize("success", [True, False])
def test_polish_final_fields_and_application_status_follow_closure(success):
    outcome = _polish_source()
    (closed,) = _close((outcome,), repair=_repair if success else lambda *_: {})
    result = closed.polish["accepted"]
    assert closed.polish["rejected"] is outcome.polish["rejected"]
    assert result.original_translation == "旧译名"
    assert result.polished_translation == ("前往溪木镇" if success else "润色旧译名")
    assert result.candidate_translation == ("前往溪木镇" if success else "润色旧译名")
    assert result.processing_status == "completed"
    assert result.accepted
    if not success:
        assert result.review_status == "needs_review"
    evidence = TaskEntryResults((outcome.task,), "run")
    evidence.ingest((closed,), evidence.entries)
    accepted = outcome.task.entries[0].identity
    assert accepted in evidence.ready_keys
    assert accepted not in evidence.failed_keys
    snapshot = build_source_snapshot(closed, _request())
    assert dict(snapshot.candidates[0].report_details)["processing_status"] == "completed"


def test_conflicting_proposal_is_reported_without_replacing_accepted_translation():
    conflict = SharedTermConflict(2, "Riverwood", "溪木镇", "河木镇", None, "second", "generated-term")
    (closed,) = _close((_source(),), conflicts=(conflict,))
    assert closed.task.collection.get(closed.task.entries[0].identity).translation == "前往溪木镇"
    diagnostic = next(item for item in closed.diagnostics if item.code == "AI_SHARED_TERM_CONFLICT")
    assert diagnostic.severity is DiagnosticSeverity.WARNING
    assert dict(diagnostic.details)["proposed_translation"] == "河木镇"
    assert any(item.code == "AI_SHARED_TERM_CONFLICT" for item in build_source_snapshot(closed, _request()).diagnostics)


def test_external_terminology_write_conflict_reports_skipped_publication_and_retained_translation():
    conflict = SharedTermConflict(2, "Riverwood", "", "溪木镇", None, "second", "external-write-conflict")
    (closed,) = _close((_source(),), conflicts=(conflict,))
    diagnostic = next(item for item in closed.diagnostics if item.code == "AI_SHARED_TERM_CONFLICT")
    assert "外部术语库已变化，未写入新增参考" in diagnostic.message
    assert "译文保留" in diagnostic.message
    assert diagnostic.severity is DiagnosticSeverity.WARNING
    assert closed.translation.success_count == 1 and closed.translation.failed_count == 0


def test_lookup_failure_is_pending_review_without_failing_existing_translation():
    def resolve(_task, _entry):
        raise OSError("pool could not be read")

    (closed,) = _close((_source(),), resolve_terms=resolve, repair=lambda *_: pytest.fail("lookup failed"))
    assert closed.successful
    result = next(iter(closed.translation.entry_outcomes.values()))
    assert "OSError" in result.reason
    assert dict(result.review_details)["review_status"] == "needs_review"
    assert closed.translation.failed_count == 0


def test_review_sees_all_references_but_literal_mismatch_does_not_reject_its_result():
    outcome = _source(rows=[("a", "Riverwood beside River", "旧名旁的河流", "succeeded")])
    entry = outcome.task.entries[0]
    required_seen = []

    def repair(task, required):
        required_seen.append(required)
        return {"a": _write(task, entry, "溪木镇旁的旧名")}

    (closed,) = _close((outcome,), repair=repair)
    assert required_seen == [{"a": {"Riverwood": "溪木镇", "River": "河流"}}]
    assert closed.translation.entry_outcomes[entry.identity].status == "succeeded"
    assert closed.task.collection.get(entry.identity).translation == "溪木镇旁的旧名"


def test_same_text_and_reference_version_with_semantic_judgment_are_not_reviewed_again():
    outcome = _source(rows=[("a", "Bruma guards fighting predators", "布鲁玛卫兵对抗掠食者", "succeeded")])
    entry = outcome.task.entries[0]
    reviewed = replace(
        outcome.translation.entry_outcomes[entry.identity],
        review_details=tuple(
            {
                "review_status": "term_suspect",
                "review_reason": "guards 指卫兵，参考译法不适用",
                "review_contract_version": 1,
                "review_reference_terms": {"Guards": "瓜尔"},
                "detected_issues": [{"issue_type": "term_mismatch", "term": "Guards"}],
            }.items()
        ),
    )
    outcome.translation.entry_outcomes[entry.identity] = reviewed

    (closed,) = _close(
        (outcome,),
        resolve_terms=lambda *_: {"Guards": "瓜尔"},
        repair=lambda *_: pytest.fail("already assessed reference must not trigger repeated repair"),
    )

    assert closed.translation.entry_outcomes[entry.identity] == reviewed
    assert closed.task.collection.get(entry.identity).translation == "布鲁玛卫兵对抗掠食者"


@pytest.mark.parametrize("failure", ["lookup", "review"])
def test_unfinished_supplemental_review_preserves_previous_findings_in_report(failure):
    outcome = _source()
    entry = outcome.task.entries[0]
    issue = {"issue_type": "format_inconsistency", "message": "标点需要结合语境复核"}
    previous = {
        "review_status": "retained",
        "review_reason": "已评估原有标点问题",
        "review_contract_version": 1,
        "review_reference_terms": {"Riverwood": "不同版本的参考"},
        "detected_issues": [issue],
    }
    outcome.translation.entry_outcomes[entry.identity] = replace(
        outcome.translation.entry_outcomes[entry.identity], review_details=tuple(previous.items())
    )

    def unavailable(*_args):
        raise RuntimeError("unavailable")

    (closed,) = _close(
        (outcome,),
        resolve_terms=unavailable if failure == "lookup" else _matched,
        repair=unavailable,
    )

    result = closed.translation.entry_outcomes[entry.identity]
    details = dict(result.review_details)
    assert result.status == "succeeded" and result.text == "前往旧译名"
    assert details["review_status"] == "needs_review"
    assert details["review_contract_version"] == 0
    assert issue in details["detected_issues"]
    assert any(item["issue_type"] == "term_mismatch" for item in details["detected_issues"])
    report_details = dict(build_source_snapshot(closed, _request()).candidates[0].report_details)
    assert issue in report_details["detected_issues"]


def test_callback_cancellation_evidence_survives_without_global_stop_flag():
    outcome = _source()
    entry = outcome.task.entries[0]

    def repair(_task, _required):
        return {"a": TranslationEntryOutcome(entry.identity, "cancelled", entry.translation, entry.stage, "已取消")}

    (closed,) = _close((outcome,), repair=repair)
    assert closed.cancelled
    assert closed.translation.entry_outcomes[entry.identity].status == "cancelled"
    assert (
        dict(build_source_snapshot(closed, _request()).candidates[0].report_details)["processing_status"] == "cancelled"
    )


def test_successful_repair_preserves_previous_review_reason():
    outcome = _source()
    entry = outcome.task.entries[0]
    outcome.translation.entry_outcomes[entry.identity] = replace(
        outcome.translation.entry_outcomes[entry.identity], reason="之前的人工复核提示"
    )
    (closed,) = _close((outcome,))
    assert closed.translation.entry_outcomes[entry.identity].reason == "之前的人工复核提示"
    assert dict(closed.translation.post_process_result.candidates[0].report_details)["note"] == "之前的人工复核提示"


def test_global_closure_failure_invalidates_ready_translation_and_polish_candidates():
    translated = _source(
        rows=[
            ("a", "Visit Riverwood", "旧译名", "succeeded"),
            ("b", "Visit Riverwood", "失败候选", "failed"),
        ]
    )
    polished = _polish_source()
    polished.task = replace(polished.task, key="second")
    closed = fail_task_term_closure((translated, polished), RuntimeError("shared pool changed"))
    assert fail_task_term_closure((), RuntimeError("initialization failed")) == ()
    assert all(not item.successful for item in closed)
    translation = closed[0].translation
    assert (translation.success_count, translation.failed_count) == (0, 2)
    assert translation.entry_outcomes[translated.task.entries[0].identity].text == "旧译名"
    assert (
        translation.entry_outcomes[translated.task.entries[1].identity]
        == translated.translation.entry_outcomes[translated.task.entries[1].identity]
    )
    assert closed[1].polish["accepted"].processing_status == "failed"
    assert closed[1].polish["accepted"].candidate_translation == "润色旧译名"
    assert closed[1].polish["rejected"] is polished.polish["rejected"]
    assert closed[0].diagnostics[-1].code == "AI_TASK_TERMINOLOGY_CHECK_FAILED"
    for item in closed:
        evidence = TaskEntryResults((item.task,), "run")
        evidence.ingest((item,), evidence.entries)
        assert not evidence.ready_keys
        assert build_source_snapshot(item, _request()).failure_count > 0


def test_global_closure_failure_handles_structural_polish_results_and_does_not_mutate_original():
    outcome = _polish_source()
    outcome.polish["accepted"] = SimpleNamespace(
        accepted=True, confidence=1.0, polished_translation="保留候选", processing_status="completed", verdict="pass"
    )
    (closed,) = fail_task_term_closure((outcome,), "report error")
    assert outcome.polish["accepted"].processing_status == "completed"
    assert closed.polish["accepted"].processing_status == "failed"
    assert closed.polish["accepted"].candidate_translation == "保留候选"
    evidence = TaskEntryResults((closed.task,), "run")
    evidence.ingest((closed,), evidence.entries)
    assert not evidence.ready_keys


def test_global_closure_failure_revokes_evidence_even_when_source_error_already_flagged_keys():
    outcome = _source()
    outcome.failed_keys = ("a",)
    (closed,) = fail_task_term_closure((outcome,), "final failure")
    evidence = TaskEntryResults((closed.task,), "run")
    evidence.ingest((closed,), evidence.entries)
    assert not evidence.ready_keys
    assert closed.translation.failed_count == 1
