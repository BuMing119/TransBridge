"""Synchronous source workflow; independent of Qt objects and active UI context."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import logging
from pathlib import Path
from typing import TYPE_CHECKING

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

    def execute(self, task: SourceTask) -> SourceOutcome:
        store = WorkflowLogStore(task.esp_path, workflow="ai_task")
        result = SourceOutcome(task, log_dir=store.log_dir)
        try:
            self.pause.wait()
            if self.stop.is_set():
                result.error = "任务已取消"
                result.failed_keys = tuple(e.key for e in task.entries)
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
                e.key
                for e in task.translate_entries
                if any(str(d) == e.key or str(d).startswith(f"{e.id}:") for d in diagnostics)
            ]
            if int(getattr(result.translation, "failed_count", 0)) and not failed:
                failed.extend(e.key for e in task.translate_entries)
            for entry in task.polish_entries:
                candidate = result.polish.get(entry.id)
                if candidate is None or getattr(candidate, "verdict", "") in {"error", "failed"}:
                    failed.append(entry.key)
            result.failed_keys = tuple(dict.fromkeys(failed))
            if self.stop.is_set():
                result.error = "任务已取消"
                result.failed_keys = tuple(e.key for e in task.entries)
        except Exception as exc:
            logger.exception("AI 来源 %s 执行失败", task.label)
            result.error = f"{task.label}：{exc}"
            result.failed_keys = tuple(e.key for e in task.entries)
        finally:
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
            run_id_factory=lambda: self.request.run_id,
            request_budget=self.request.request_budget,
            llm_client_wrapper=lambda client: WorkflowLoggingLLMClient(client, store, channel_prefix="translate_call"),
            term_llm_client_wrapper=lambda client: WorkflowLoggingLLMClient(client, store, channel_prefix="term_call"),
            **self.request.terminology_binding.translator_kwargs(),
        )
        return translator.translate(
            collection=task.collection,
            target_entry_ids=[e.key for e in task.translate_entries],
            progress_callback=lambda c, t, m, *_: self.progress(task.key, "翻译", c, t, m),
            stop_event=self.stop,
            pause_event=self.pause,
            log_callback=lambda idx, text: self.log(task.key, f"[{idx}] {text}"),
            stream_callback=lambda idx, chunk: store.write_chunk(f"batch_{idx:03d}", chunk),
            stage_progress_callback=lambda s, c, t, m: self.progress(task.key, s, c, t, m),
        )

    def _polish(self, task, store):
        from .proofread_composition import build_proofread_pipeline

        pipeline = build_proofread_pipeline(
            self.config,
            task.esp_path,
            profile=self.request.spec.execution_profile,
            request_budget=self.request.request_budget,
            terminology_binding=self.request.terminology_binding,
            stop_event=self.stop,
            pause_event=self.pause,
            log_store=store,
            paratranz_client=self.client,
            project_id=self.project_id,
        )
        return pipeline.process(
            list(task.polish_entries),
            progress_callback=lambda s, c, t, m: self.progress(task.key, s, c, t, m),
            log_callback=lambda text: self.log(task.key, text),
            stop_event=self.stop,
            pause_event=self.pause,
            max_workers=self.config.max_concurrent,
        )


def render_source_report(outcome: SourceOutcome, request) -> None:
    """Create the canonical report after preview decisions have been applied."""
    from transbridge.application.translation.mixed_report import build_mixed_report_snapshot
    from transbridge.application.translation.polish_report import build_polish_report_snapshot

    from .reporting import render_translation_report

    snapshot = getattr(outcome.translation, "post_process_result", None)
    if outcome.task.polish_entries:
        summary = outcome.polish_summary
        failed = tuple(entry.id for entry in outcome.task.polish_entries if entry.key in outcome.failed_keys)
        polish = build_polish_report_snapshot(
            outcome.polish,
            list(outcome.task.polish_entries),
            run_id=request.run_id,
            accepted_entry_ids=summary.accepted_entry_ids if summary else (),
            failed_entry_ids=summary.failed_entry_ids if summary else failed,
            rejected_entry_ids=summary.rejected_entry_ids if summary else (),
            pending_entry_ids=(
                () if summary else tuple(entry.id for entry in outcome.task.polish_entries if entry.id not in failed)
            ),
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
    outcome.snapshot = snapshot
    if snapshot is not None:
        outcome.report = render_translation_report(snapshot, Path(outcome.task.esp_path or outcome.task.label).stem)
