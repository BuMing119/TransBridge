"""Synchronous source workflow; independent of Qt objects and active UI context."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from transbridge.application.translation.entry_alias import ai_entry_id, ai_entry_key

from .task_scope import SourceTask
from .workflow_log_store import WorkflowLogStore

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .result_presenter import PolishApplySummary


@dataclass
class SourceOutcome:
    task: SourceTask
    translation: object | None = None
    polish: dict = field(default_factory=dict)
    polish_summary: PolishApplySummary | None = None
    error: str = ""
    failed_keys: tuple[str, ...] = ()
    snapshot: object | None = None
    report: object | None = None
    log_dir: str = ""
    diagnostics: tuple = ()
    cancelled: bool = False
    record_snapshot: object | None = None

    @property
    def successful(self) -> bool:
        return not self.error and not self.failed_keys


class SourceExecutor:
    """Execute identical stages for each source with the task's shared budget and cancellation."""

    def __init__(
        self,
        request,
        *,
        stop_event,
        pause_event,
        shared_terms,
        terms_lock,
        progress,
        log,
        paratranz_client=None,
        project_id=None,
        consistency=None,
        attempt_id=None,
        checkpoint_root=None,
        log_ready=None,
    ) -> None:
        self.request = request
        self.config = request.config
        self.stop = stop_event
        self.pause = pause_event
        self.shared_terms = shared_terms
        self.terms_lock = terms_lock
        self.progress = progress
        self.log = log
        self.client = paratranz_client
        self.project_id = project_id
        self.consistency = consistency
        self.attempt_id = attempt_id
        self.checkpoint_root = checkpoint_root
        self.log_ready = log_ready

    def execute(self, task: SourceTask) -> SourceOutcome:
        store = WorkflowLogStore(task.esp_path, workflow="ai_task")
        result = SourceOutcome(task, log_dir=store.log_dir)
        try:
            if self.log_ready is not None:
                self.log_ready(task.key, store.log_dir)
            self.pause.wait()
            if self.stop.is_set():
                result.cancelled = True
                result.error = "任务已取消"
                result.failed_keys = tuple(ai_entry_key(e) for e in task.entries)
                return result
            stages = []
            if task.translate_entries:
                stages.append(("translation", lambda: self._translate(task, store)))
            if task.polish_entries:
                stages.append(("polish", lambda: self._polish(task, store)))
            if len(stages) > 1 and self.config.mixed_execution_order == "parallel":
                failures = []
                with ThreadPoolExecutor(max_workers=2, thread_name_prefix="ai-stages") as pool:
                    futures = [(name, pool.submit(operation)) for name, operation in stages]
                    for name, future in futures:
                        try:
                            setattr(result, name, future.result())
                        except Exception as exc:
                            failures.append(exc)
                if failures:
                    raise ExceptionGroup("AI 处理阶段失败", failures)
            else:
                for name, operation in stages:
                    if self.stop.is_set():
                        break
                    self.pause.wait()
                    if self.stop.is_set():
                        break
                    setattr(result, name, operation())
            if task.translate_entries and result.translation is None and not self.stop.is_set():
                raise RuntimeError("翻译阶段未返回结果，不能将该来源标记为成功。")
            diagnostics = tuple(getattr(result.translation, "failed_entries", ()) or ())
            failed = [
                ai_entry_key(e)
                for e in task.translate_entries
                if any(str(d) == ai_entry_key(e) or str(d).startswith(f"{ai_entry_id(e)}:") for d in diagnostics)
            ]
            if int(getattr(result.translation, "failed_count", 0)) and not failed:
                failed.extend(ai_entry_key(e) for e in task.translate_entries)
            for entry in task.polish_entries:
                candidate = result.polish.get(ai_entry_id(entry))
                if candidate is None or getattr(candidate, "verdict", "") in {"error", "failed"}:
                    failed.append(ai_entry_key(entry))
            result.failed_keys = tuple(dict.fromkeys(failed))
            if self.stop.is_set():
                result.cancelled = True
                result.error = "任务已取消"
                result.failed_keys = tuple(ai_entry_key(e) for e in task.entries)
        except Exception as exc:
            logger.exception("AI 来源 %s 执行失败", task.label)
            result.error = f"{task.label}：{exc}"
            result.failed_keys = tuple(ai_entry_key(e) for e in task.entries)
        finally:
            result.diagnostics = tuple(getattr(result.polish, "diagnostics", ()))
            store.close()
        return result

    def _translate(self, task, store):
        from transbridge.ai_translator.translator import AutoTranslator, TranslatorConfig

        from .workflow_logging_client import WorkflowLoggingLLMClient

        translator = AutoTranslator(
            TranslatorConfig(self.config, task.esp_path, self.request.spec.overwrite),
            self.client,
            self.project_id,
            shared_in_flight_terms=self.shared_terms,
            shared_in_flight_lock=self.terms_lock,
            run_id_factory=lambda: self.attempt_id or self.request.run_id,
            request_budget=self.request.request_budget,
            llm_client_wrapper=lambda client: WorkflowLoggingLLMClient(client, store, channel_prefix="translate_call"),
            term_llm_client_wrapper=lambda client: WorkflowLoggingLLMClient(client, store, channel_prefix="term_call"),
            term_snapshot_observer=(
                None
                if self.consistency is None
                else lambda manager: self.consistency.observe_terms(task.key, "translation", manager)
            ),
            term_update_observer=(
                None
                if self.consistency is None
                else lambda manager, terms: self.consistency.record_generated_terms(task.key, manager, terms)
            ),
            **self.request.terminology_binding.translator_kwargs(),
        )
        return translator.translate(
            collection=task.collection,
            target_entry_ids=[ai_entry_key(e) for e in task.translate_entries],
            strict_target_scope=True,
            progress_callback=lambda c, t, m, *_: self.progress(task.key, "翻译", c, t, m),
            stop_event=self.stop,
            pause_event=self.pause,
            log_callback=lambda idx, text: self.log(task.key, f"[{idx}] {text}"),
            stream_callback=lambda idx, chunk: store.write_chunk(f"batch_{idx:03d}", chunk),
            stage_progress_callback=lambda s, c, t, m: self.progress(task.key, s, c, t, m),
        )

    def _polish(self, task, store):
        from transbridge.application.translation.proofread_checkpoint import ProofreadCheckpoint, proofread_settings

        from .proofread_composition import build_proofread_pipeline

        checkpoint = None
        if self.checkpoint_root is not None and self.request.spec.execution_profile.enable_proofread:
            owner = self.request.spec.owner
            checkpoint = ProofreadCheckpoint(
                self.checkpoint_root,
                project=str(owner.project_id),
                variant=str(owner.variant_id),
                source=task.key,
                settings=proofread_settings(self.config),
                reuse=getattr(self.request, "reuse_proofread", False),
                task_id=getattr(self.request, "recovery_task_id", None) or self.request.run_id,
            )
        pipeline = build_proofread_pipeline(
            self.config,
            task.esp_path,
            profile=self.request.spec.execution_profile,
            request_budget=self.request.request_budget,
            terminology_binding=self.request.terminology_binding,
            stop_event=self.stop,
            pause_event=self.pause,
            log_store=store,
            checkpoint=checkpoint,
            paratranz_client=self.client,
            project_id=self.project_id,
            term_snapshot_observer=(
                None
                if self.consistency is None
                else lambda manager: self.consistency.observe_terms(task.key, "polish", manager)
            ),
        )
        results = pipeline.process(
            list(task.polish_entries),
            progress_callback=lambda s, c, t, m: self.progress(task.key, s, c, t, m),
            log_callback=lambda text: self.log(task.key, text),
            stop_event=self.stop,
            pause_event=self.pause,
            max_workers=self.config.max_concurrent,
        )
        return _PolishResults(results, diagnostics=getattr(pipeline, "diagnostics", ()))


class _PolishResults(dict):
    def __init__(self, values=(), *, diagnostics=()):
        super().__init__(values)
        self.diagnostics = tuple(diagnostics)


def build_source_snapshot(outcome: SourceOutcome, request, *, cancelled: bool = False):
    """Build an in-memory report without exporting files."""
    from transbridge.application.contracts import OperationOutcome
    from transbridge.application.io import EntryKey
    from transbridge.application.translation.mixed_report import build_mixed_report_snapshot
    from transbridge.application.translation.polish_report import build_polish_report_snapshot

    if outcome.record_snapshot is not None:
        return outcome.record_snapshot
    cancelled = cancelled or outcome.cancelled
    snapshot = getattr(outcome.translation, "post_process_result", None)
    if outcome.task.polish_entries:
        summary = outcome.polish_summary
        failed = tuple(
            ai_entry_id(entry) for entry in outcome.task.polish_entries if ai_entry_key(entry) in outcome.failed_keys
        )
        polish = build_polish_report_snapshot(
            outcome.polish,
            list(outcome.task.polish_entries),
            run_id=request.run_id,
            accepted_entry_ids=summary.accepted_entry_ids if summary else (),
            failed_entry_ids=summary.failed_entry_ids if summary else failed,
            rejected_entry_ids=summary.rejected_entry_ids if summary else (),
            pending_entry_ids=(
                ()
                if summary
                else tuple(
                    ai_entry_id(entry) for entry in outcome.task.polish_entries if ai_entry_id(entry) not in failed
                )
            ),
        )
        candidates = []
        failed_keys = set()
        counts: dict[str, int] = {"accepted": 0, "rejected": 0, "failed": 0}
        for candidate, entry in zip(polish.candidates, outcome.task.polish_entries, strict=True):
            result = outcome.polish.get(ai_entry_id(entry))
            details = dict(candidate.report_details)
            processing = getattr(result, "processing_status", "")
            if result is None:
                processing = "not_started"
            if processing:
                details["processing_status"] = processing
            if processing in {"cancelled", "not_started"}:
                details["result_status"] = processing
            elif cancelled and processing == "completed":
                details["result_status"] = "not_applied"
            status = details["result_status"]
            counts[status] = counts.get(status, 0) + 1
            if status == "failed":
                failed_keys.add(candidate.entry_key)
            candidates.append(
                replace(
                    candidate,
                    text=getattr(result, "candidate_translation", None) or candidate.text,
                    report_details=tuple(details.items()),
                )
            )
        polish = replace(
            polish,
            candidates=tuple(candidates),
            diagnostics=(
                *(
                    item
                    for item in polish.diagnostics
                    if item.code != "POLISH_ENTRY_FAILED"
                    or EntryKey.from_dict(dict(item.details)["entry_key"]) in failed_keys
                ),
                *outcome.diagnostics,
            ),
            failure_count=counts.get("failed", 0),
            run_spec_summary={**polish.run_spec_summary, "polish_counts": counts},
            outcome=OperationOutcome.CANCELLED if cancelled else polish.outcome,
        )
        snapshot = (
            build_mixed_report_snapshot(
                snapshot,
                polish,
                run_id=request.run_id,
                execution_order=request.config.mixed_execution_order,
            )
            if snapshot is not None
            else polish
        )
    if snapshot is None or len(snapshot.candidates) < len(outcome.task.entries):
        snapshot = _include_unprocessed(snapshot, outcome, request.run_id)
    snapshot = _include_translation_failures(snapshot, outcome)
    if cancelled:
        snapshot = replace(snapshot, outcome=OperationOutcome.CANCELLED)
    outcome.snapshot = snapshot
    return snapshot


def _include_translation_failures(snapshot, outcome):
    """Project execution failures onto rows, including empty and retained old translations."""
    reasons = tuple(str(item) for item in getattr(outcome.translation, "failed_entries", ()) or ())
    # Cancellation marks the entire source failed for atomic application. Only
    # explicit per-entry failures remain failures in its historical record.
    by_id: dict[str, list[str]] = {}
    by_key = {ai_entry_key(entry): ai_entry_id(entry) for entry in outcome.task.translate_entries}
    for reason in reasons:
        entry_id = by_key.get(reason, reason.partition(": ")[0])
        by_id.setdefault(entry_id, []).append(reason)
    has_owned_failures = any(ai_entry_id(entry) in by_id for entry in outcome.task.translate_entries)
    use_source_failures = outcome.translation is None or (
        getattr(outcome.translation, "failed_count", 0) > 0 and not has_owned_failures
    )
    # A later polish exception also marks every source key failed. An available
    # translation result is authoritative for that stage's individual failures.
    failed_keys = set(outcome.failed_keys) if use_source_failures and not outcome.cancelled else set()
    failures = {}
    for entry in outcome.task.translate_entries:
        owned = by_id.get(ai_entry_id(entry), ())
        if owned or ai_entry_key(entry) in failed_keys:
            failures[entry.identity] = "\n".join(owned) or outcome.error or "翻译失败"
    if not failures:
        return snapshot
    candidates = []
    for candidate in snapshot.candidates:
        reason = failures.get(candidate.entry_key)
        if reason is not None:
            details = dict(candidate.report_details)
            details.update(result_status="failed", processing_status="failed", note=reason)
            candidate = replace(candidate, accepted=False, report_details=tuple(details.items()))
        candidates.append(candidate)
    return replace(
        snapshot,
        candidates=tuple(candidates),
        accepted_count=sum(candidate.accepted for candidate in candidates),
        failure_count=max(snapshot.failure_count, len(failures)),
    )


def _include_unprocessed(snapshot, outcome, run_id):
    from transbridge.application.contracts import OperationOutcome
    from transbridge.application.translation.postprocess import PostProcessCandidate, ReportSnapshot

    known = {candidate.entry_key for candidate in snapshot.candidates} if snapshot else set()
    missing = tuple(
        PostProcessCandidate(
            run_id=run_id,
            entry_key=entry.identity,
            before_revision=entry.revision,
            original=entry.original or "",
            before_text=entry.translation or "",
            text=entry.translation or "",
            stage=entry.stage,
            accepted=False,
            context=entry.context or "",
            report_details=(("result_status", "not_started"), ("processing_status", "not_started")),
        )
        for entry in outcome.task.entries
        if entry.identity not in known
    )
    if snapshot:
        return replace(snapshot, candidates=(*snapshot.candidates, *missing), input_count=len(outcome.task.entries))
    return ReportSnapshot(
        schema="transbridge.polish-report.v1",
        run_id=run_id,
        outcome=OperationOutcome.FAILED,
        input_count=len(outcome.task.entries),
        accepted_count=0,
        candidates=missing,
        stage_outcomes=(),
        diagnostics=(),
    )


def build_task_record(outcomes, request, *, state: str, applied: bool = False, saved: bool = False) -> dict:
    """A durable result record, not an executable checkpoint."""
    sources = [
        {
            "key": outcome.task.key,
            "label": outcome.task.label,
            "error": outcome.error,
            "snapshot": build_source_snapshot(outcome, request, cancelled=state == "cancelled").to_dict(),
        }
        for outcome in outcomes
    ]
    counts = {"successful": 0, "failed": 0, "unprocessed": 0, "cancelled": 0}
    for source in sources:
        for entry in source["snapshot"]["entries"]:
            details = entry.get("report_details", {})
            status = details.get("processing_status", details.get("result_status", ""))
            if status == "not_started":
                counts["unprocessed"] += 1
            elif status == "cancelled":
                counts["cancelled"] += 1
            elif status == "failed":
                counts["failed"] += 1
            elif (
                status == "completed"
                or entry["accepted"]
                or (details.get("verdict") == "pass" and details.get("confidence", 0) > 0)
            ):
                counts["successful"] += 1
            else:
                counts["failed"] += 1
    now = datetime.now(UTC).isoformat()
    owner = getattr(getattr(request, "spec", None), "owner", None)
    return {
        "schema": "transbridge.task-history.v1",
        "run_id": request.run_id,
        "created_at": now,
        "updated_at": now,
        "ended_at": now if state in {"completed", "cancelled", "failed", "error"} else None,
        "project_id": getattr(owner, "project_id", None),
        "variant_id": getattr(owner, "variant_id", None),
        "state": state,
        "applied": applied,
        "saved": saved,
        "counts": counts,
        "sources": sources,
    }


def render_source_report(outcome: SourceOutcome, request) -> None:
    """Compatibility export entrypoint, invoked only when an export is requested."""
    from .reporting import render_translation_report

    snapshot = build_source_snapshot(outcome, request)
    if snapshot is not None:
        outcome.report = render_translation_report(snapshot, Path(outcome.task.esp_path or outcome.task.label).stem)
