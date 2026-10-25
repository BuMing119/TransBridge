"""Review new shared-reference differences after all source workflows finish."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import copy
from dataclasses import dataclass, is_dataclass, replace
import logging
import time
from typing import TYPE_CHECKING
from uuid import uuid4

from transbridge.ai_translator.translation_entry_outcomes import TranslationEntryOutcome
from transbridge.application.contracts import (
    Diagnostic,
    DiagnosticSeverity,
    ErrorCategory,
    OperationOutcome,
    RequestContext,
)
from transbridge.application.io.identity import Provenance
from transbridge.application.io.mutation import ChangeSet, EntryPatch, MutationStatus
from transbridge.application.translation.entry_alias import ai_entry_id, ai_entry_key
from transbridge.application.translation.protected_syntax import protected_syntax_matches

from .source_execution import SourceOutcome
from .task_scope import SourceTask

if TYPE_CHECKING:
    from transbridge.application.translation.ai_request_budget import EventState
    from transbridge.converter.translation_entry import TranslationEntry

logger = logging.getLogger(__name__)
type ResolveTerms = Callable[[SourceTask, "TranslationEntry"], Mapping[str, str]]
type RepairTerms = Callable[[SourceTask, dict[str, dict[str, str]]], Mapping[str, TranslationEntryOutcome]]
type Progress = Callable[[str, str, int, int, str], None]


@dataclass(frozen=True)
class _Candidate:
    entry: TranslationEntry
    text: str
    stage: int
    polish: bool = False


def close_task_terms(
    outcomes: Sequence[SourceOutcome],
    *,
    resolve_terms: ResolveTerms,
    repair: RepairTerms,
    stop_event: EventState,
    pause_event=None,
    progress: Progress | None = None,
    conflicts: Sequence = (),
) -> tuple[SourceOutcome, ...]:
    """Review new reference differences without invalidating successful translations.

    ``resolve_terms`` owns scope, alias and source-span matching, using the same
    manager lookup as normal consistency checks. The legacy ``repair`` callback
    now runs comprehensive AI review and returns committed draft evidence.
    Existing unsuccessful or unselected entries never enter that callback.
    """
    closed = []
    conflict_diagnostics = tuple(_conflict_diagnostic(item) for item in conflicts)
    for outcome in outcomes:
        current = replace(outcome, diagnostics=(*outcome.diagnostics, *conflict_diagnostics))
        if conflict_diagnostics:
            current.snapshot = current.record_snapshot = current.report = None
        snapshot = getattr(outcome.translation, "post_process_result", None)
        if conflict_diagnostics and snapshot is not None:
            current.translation = copy(outcome.translation)
            current.translation.post_process_result = replace(
                snapshot, diagnostics=(*snapshot.diagnostics, *conflict_diagnostics)
            )
        if not outcome.cancelled:
            current = _close_source(current, resolve_terms, repair, stop_event, progress, pause_event)
        closed.append(current)
    return tuple(closed)


def fail_task_term_closure(outcomes: Sequence[SourceOutcome], error: object) -> tuple[SourceOutcome, ...]:
    """Invalidate successful application evidence after an unexpected final-check error."""
    failed = []
    message = f"任务最终术语检查失败：{error}"
    for outcome in outcomes:
        pending = {ai_entry_key(item.entry): item for item in _failure_candidates(outcome)}
        replacements = {key: _failed(item, message) for key, item in pending.items()}
        diagnostics = tuple(_failure_diagnostic(item.entry, replacements[key]) for key, item in pending.items())
        if pending:
            result = _merge(outcome, pending, replacements, diagnostics)
        else:
            result = replace(outcome, snapshot=None, record_snapshot=None, report=None)
        diagnostic = Diagnostic(
            "AI_TASK_TERMINOLOGY_CHECK_FAILED",
            message,
            category=ErrorCategory.INTERNAL,
            severity=DiagnosticSeverity.ERROR,
            details=(("source", outcome.task.key),),
        )
        failed.append(replace(result, error=message, diagnostics=(*result.diagnostics, diagnostic)))
    return tuple(failed)


def _failure_candidates(outcome: SourceOutcome) -> tuple[_Candidate, ...]:
    """Revoke application evidence even if a source error already flagged its keys."""
    candidates = []
    evidence = getattr(outcome.translation, "entry_outcomes", {})
    for entry in outcome.task.translate_entries:
        result = evidence.get(entry.identity)
        if result is not None and result.status == "succeeded":
            candidates.append(_Candidate(copy(entry), result.text, result.stage))
    for entry in outcome.task.polish_entries:
        result = outcome.polish.get(ai_entry_id(entry))
        if result is not None and getattr(result, "accepted", False) and result.processing_status == "completed":
            stage = getattr(result, "target_stage", None)
            candidates.append(
                _Candidate(copy(entry), result.polished_translation, entry.stage if stage is None else stage, True)
            )
    return tuple(candidates)


def _successful_candidates(outcome: SourceOutcome) -> tuple[_Candidate, ...]:
    candidates = []
    failures = set(outcome.failed_keys)
    evidence = getattr(outcome.translation, "entry_outcomes", {})
    for entry in outcome.task.translate_entries:
        result = evidence.get(entry.identity)
        current = outcome.task.collection.get(entry.identity)
        if ai_entry_key(entry) not in failures and result is not None and result.status == "succeeded" and current:
            candidates.append(_Candidate(copy(current), current.translation, current.stage))
    for entry in outcome.task.polish_entries:
        result = outcome.polish.get(ai_entry_id(entry))
        current = outcome.task.collection.get(entry.identity)
        if (
            ai_entry_key(entry) not in failures
            and result is not None
            and getattr(result, "accepted", False)
            and getattr(result, "processing_status", "") == "completed"
            and current is not None
        ):
            stage = getattr(result, "target_stage", None)
            candidates.append(
                _Candidate(copy(current), result.polished_translation, current.stage if stage is None else stage, True)
            )
    return tuple(candidates)


def _close_source(outcome, resolve_terms, repair, stop_event, progress, pause_event):
    from transbridge.ai_translator.term_validation import contains_term
    from transbridge.application.translation.proofread_advisory import has_review_evidence

    from .source_reference_review import source_review_evidence

    pending, references, replacements, diagnostics = {}, {}, {}, []
    unverified = set()
    if progress:
        progress(outcome.task.key, "term_closure_load", 0, 0, "正在整理已完成译文和校对记录")
    evidence = source_review_evidence(outcome)
    candidates = _successful_candidates(outcome)
    scanned, already_reviewed, last_count, last_update = 0, 0, -100, 0.0

    def scan_progress(*, force=False):
        nonlocal last_count, last_update
        now = time.monotonic()
        if progress and (force or scanned - last_count >= 100 or now - last_update >= 0.25):
            progress(
                outcome.task.key,
                "term_closure_scan",
                scanned,
                len(candidates),
                f"已检查 {scanned}/{len(candidates)} 条；待综合校对 {len(references)} 条；已评估 {already_reviewed} 条",
            )
            last_count, last_update = scanned, now

    scan_progress(force=True)
    for index, candidate in enumerate(candidates):
        while pause_event is not None and not stop_event.is_set() and not pause_event.wait(0.05):
            pass
        if stop_event.is_set():
            # Do not spend minutes resolving references just to classify an
            # already cancelled task. Preserve text and resume unscanned work.
            for remaining in candidates[index:]:
                key = ai_entry_key(remaining.entry)
                pending[key] = remaining
                replacements[key] = _failed(remaining, "任务已取消，项目参考检查未完成", cancelled=True)
            break
        scan_progress()
        scanned += 1
        key = ai_entry_key(candidate.entry)
        try:
            terms = dict(resolve_terms(outcome.task, candidate.entry))
        except Exception as exc:
            logger.exception("Shared reference lookup failed for %s", candidate.entry.identity)
            pending[key] = candidate
            previous = evidence.get(candidate.entry.identity, ("", {}))[1]
            replacements[key] = _pending_review(
                candidate,
                f"参考资料检查未完成（{type(exc).__name__}）",
                previous.get("review_reference_terms", {}),
                previous,
            )
            continue
        differences = {term: target for term, target in terms.items() if not contains_term(candidate.text, target)}
        if not differences:
            continue
        reviewed_text, details = evidence.get(candidate.entry.identity, ("", {}))
        reviewed_terms = details.get("review_reference_terms", {})
        if (
            reviewed_text == candidate.text
            and has_review_evidence(details)
            and all(reviewed_terms.get(term) == target for term, target in differences.items())
        ):
            already_reviewed += 1
            continue
        pending[key], references[key] = candidate, terms
    scan_progress(force=True)
    if not pending:
        if progress:
            progress(
                outcome.task.key,
                "term_closure_done",
                scanned,
                len(candidates),
                f"参考检查完成；无需补充校对（已评估 {already_reviewed} 条）",
            )
        return outcome

    total = len(pending)
    if progress and references and not stop_event.is_set():
        progress(outcome.task.key, "term_closure", 0, len(references), f"综合评估项目参考差异：{len(references)} 条")
    reviewed = {}
    failure = ""
    if references and not stop_event.is_set():
        try:
            reviewed = repair(outcome.task, references)
        except Exception as exc:
            logger.exception("Project reference review failed for source %s", outcome.task.key)
            failure = f"补充校对未完成（{type(exc).__name__}），已保留译文"
    for key, candidate in pending.items():
        if key in replacements:
            continue
        result = reviewed.get(key)
        reason = failure or _invalid_review(outcome.task, candidate, result)
        if reason and (stop_event.is_set() or getattr(result, "status", "") == "cancelled"):
            replacements[key] = _failed(candidate, "任务已取消，补充校对未完成", cancelled=True)
        elif reason:
            replacements[key] = _pending_review(
                candidate, reason, references[key], evidence.get(candidate.entry.identity, ("", {}))[1]
            )
            unverified.add(key)
        else:
            replacements[key] = result
    # A broken callback may have changed draft text before failing. Restore it;
    # merely marking a review pending must not leak an unverified model edit.
    rollback = {
        key: replace(item, status="failed") if key in unverified else item for key, item in replacements.items()
    }
    _restore_failed_drafts(outcome.task, pending, rollback)
    result = _merge(outcome, pending, replacements, tuple(diagnostics))
    if progress:
        waiting = sum(
            dict(item.review_details).get("review_status") == "needs_review" for item in replacements.values()
        )
        cancelled = any(item.status == "cancelled" for item in replacements.values())
        progress(
            outcome.task.key,
            "term_closure_cancelled" if cancelled else "term_closure_done",
            scanned,
            len(candidates),
            "项目参考检查已取消，已保留译文和进度"
            if cancelled
            else f"参考检查完成；补充评估 {total} 条；待复核 {waiting} 条",
        )
    return result


def _pending_review(candidate, reason, references, previous_details):
    from transbridge.application.translation.postprocess import PostProcessCandidate
    from transbridge.application.translation.proofread_advisory import prepare_review_candidate

    base = PostProcessCandidate(
        run_id="reference-review-pending",
        entry_key=candidate.entry.identity,
        before_revision=candidate.entry.revision,
        original=candidate.entry.original,
        before_text=candidate.text,
        text=candidate.text,
        stage=candidate.stage,
        context=candidate.entry.context or "",
        accepted=True,
        report_details=tuple(previous_details.items()),
    )
    details = dict(prepare_review_candidate(base, references).report_details)
    details.update(review_status="needs_review", review_reason=reason, review_before_text=candidate.text)
    return TranslationEntryOutcome(
        candidate.entry.identity, "succeeded", candidate.text, candidate.stage, reason, tuple(details.items())
    )


def _invalid_review(task, candidate, result):
    if result is None:
        return "补充校对未返回有效结果，已保留译文"
    if result.entry_key != candidate.entry.identity:
        return "综合校对结果与词条身份不匹配"
    if result.status != "succeeded":
        return result.reason or "综合校对未完成"
    current = task.collection.get(candidate.entry.identity)
    if current is None or current.translation != result.text or current.stage != result.stage:
        return "综合校对结果未写入任务草稿"
    if current.original != candidate.entry.original:
        return "综合校对期间原文发生变化"
    if not result.text.strip():
        return "综合校对未返回有效译文"
    if not protected_syntax_matches(candidate.entry.original, result.text):
        return "综合校对改变了原文中的受保护标记"
    return ""


def _failed(candidate, reason, *, cancelled=False):
    return TranslationEntryOutcome(
        candidate.entry.identity,
        "cancelled" if cancelled else "failed",
        candidate.text,
        candidate.stage,
        f"AI_TASK_TERMINOLOGY_CLOSURE: {reason}",
    )


def _restore_failed_drafts(task, candidates, replacements):
    rows = []
    for key, candidate in candidates.items():
        current = task.collection.get(candidate.entry.identity)
        if (
            replacements[key].status != "succeeded"
            and current is not None
            and (current.translation, current.stage) != (candidate.entry.translation, candidate.entry.stage)
        ):
            rows.append((current, candidate.entry))
    if not rows:
        return
    run_id = f"term-closure-rollback-{uuid4().hex}"
    context = RequestContext(
        owner_id="ai-task-term-closure",
        run_id=run_id,
        permissions=frozenset({"entry.translation.write", "entry.stage.write"}),
    )
    changes = ChangeSet(
        run_id=run_id,
        patches=tuple(
            EntryPatch.create(current.identity, translation=old.translation, stage=old.stage) for current, old in rows
        ),
        expected_revisions=tuple((current.identity, current.revision) for current, _ in rows),
        provenance=Provenance(run_id, context.owner_id, "ai-task-term-closure"),
    )
    result = task.collection.apply(changes, context)
    if result.status is not MutationStatus.APPLIED:
        raise RuntimeError("综合校对结果无效且无法恢复任务草稿：" + "; ".join(d.message for d in result.diagnostics))


def _merge(outcome, pending, replacements, diagnostics):
    translation = copy(outcome.translation)
    polish = dict(outcome.polish)
    failed_keys = list(outcome.failed_keys)
    translated = {}
    for key, candidate in pending.items():
        result = replacements[key]
        if result.status != "succeeded":
            failed_keys.append(key)
        if candidate.polish:
            previous = polish[ai_entry_id(candidate.entry)]
            success = result.status == "succeeded"
            review = dict(result.review_details)
            metadata = {
                name: review[name]
                for name in (
                    "review_status",
                    "review_reason",
                    "detected_issues",
                    "review_reference_terms",
                    "review_contract_version",
                    "review_before_text",
                )
                if name in review
            }
            polish[ai_entry_id(candidate.entry)] = _replace_polish(
                previous,
                polished_translation=result.text
                if success
                else getattr(previous, "original_translation", candidate.entry.translation),
                candidate_translation=result.text if success else candidate.text,
                target_stage=result.stage,
                verdict=getattr(previous, "verdict", "pass") if success else "error",
                processing_status="completed" if success else result.status,
                note="\n".join(filter(None, (getattr(previous, "note", ""), result.reason))),
                **metadata,
            )
        else:
            if result.status == "succeeded":
                previous = translation.entry_outcomes[result.entry_key]
                result = replace(
                    result, reason="\n".join(dict.fromkeys(filter(None, (previous.reason, result.reason))))
                )
            translated[result.entry_key] = result
    if translated:
        unfinished = sum(item.status != "succeeded" for item in translated.values())
        failures = [item for item in translated.values() if item.status == "failed"]
        translation.entry_outcomes = {**translation.entry_outcomes, **translated}
        translation.success_count = max(0, getattr(translation, "success_count", 0) - unfinished)
        translation.failed_count = getattr(translation, "failed_count", 0) + len(failures)
        translation.failed_entries = [
            *getattr(translation, "failed_entries", ()),
            *(
                f"{ai_entry_id(pending[key].entry)}: {item.reason}"
                for key, item in replacements.items()
                if not pending[key].polish and item.status == "failed"
            ),
        ]
        translation.post_process_result = _update_snapshot(
            getattr(translation, "post_process_result", None), translated, diagnostics, translation
        )
    return replace(
        outcome,
        translation=translation,
        polish=polish,
        polish_summary=None,
        failed_keys=tuple(dict.fromkeys(failed_keys)),
        diagnostics=(*outcome.diagnostics, *diagnostics),
        cancelled=outcome.cancelled or any(item.status == "cancelled" for item in replacements.values()),
        snapshot=None,
        record_snapshot=None,
        report=None,
    )


def _replace_polish(previous, **changes):
    if is_dataclass(previous):
        return replace(previous, **changes)
    # Legacy test/adapter objects implement the same structural result contract.
    result = copy(previous)
    for name, value in changes.items():
        setattr(result, name, value)
    return result


def _update_snapshot(snapshot, replacements, diagnostics, translation):
    if snapshot is None:
        return None
    candidates = []
    for candidate in snapshot.candidates:
        result = replacements.get(candidate.entry_key)
        if result is not None:
            details = dict(candidate.report_details)
            details.update(result.review_details)
            details.update(
                processing_status="completed" if result.status == "succeeded" else result.status,
                result_status="pending" if result.status == "succeeded" else result.status,
                note=result.reason,
            )
            candidate = replace(
                candidate,
                text=result.text,
                stage=result.stage,
                accepted=result.status == "succeeded",
                report_details=tuple(details.items()),
            )
        candidates.append(candidate)
    cancelled = any(item.status == "cancelled" for item in replacements.values())
    failed = sum(item.status == "failed" for item in replacements.values())
    accepted = sum(candidate.accepted for candidate in candidates)
    status = (
        OperationOutcome.CANCELLED
        if cancelled
        else (OperationOutcome.PARTIAL if accepted else OperationOutcome.FAILED)
        if failed
        else snapshot.outcome
    )
    summary = dict(snapshot.run_spec_summary)
    if "translation_counts" in summary:
        summary["translation_counts"] = {
            **summary["translation_counts"],
            "succeeded": translation.success_count,
            "failed": translation.failed_count,
        }
    return replace(
        snapshot,
        candidates=tuple(candidates),
        accepted_count=accepted,
        outcome=status,
        diagnostics=(*snapshot.diagnostics, *diagnostics),
        failure_count=snapshot.failure_count + failed,
        run_spec_summary=summary,
    )


def _failure_diagnostic(entry, result):
    return Diagnostic(
        "AI_TASK_TERMINOLOGY_CLOSURE_FAILED",
        result.reason,
        category=ErrorCategory.CANCELLED if result.status == "cancelled" else ErrorCategory.INPUT,
        severity=DiagnosticSeverity.WARNING if result.status == "cancelled" else DiagnosticSeverity.ERROR,
        details=(("entry_key", entry.identity.to_dict()),),
    )


def _conflict_diagnostic(conflict):
    if isinstance(conflict, Diagnostic):
        return conflict
    decision = (
        f"保留现有参考译法“{conflict.accepted_translation}”" if conflict.accepted_translation else "保留当前参考资料"
    )
    message = (
        f"外部术语库已变化，未写入新增参考“{conflict.term}”；译文保留"
        if conflict.reason == "external-write-conflict"
        else f"共享术语冲突：{conflict.term}；{decision}，未采用“{conflict.proposed_translation}”"
    )
    return Diagnostic(
        "AI_SHARED_TERM_CONFLICT",
        message,
        severity=DiagnosticSeverity.WARNING,
        category=ErrorCategory.CONFLICT,
        details=tuple(
            (name, getattr(conflict, name))
            for name in (
                "term",
                "accepted_translation",
                "proposed_translation",
                "plugin_id",
                "source",
                "reason",
                "revision",
            )
        ),
    )
