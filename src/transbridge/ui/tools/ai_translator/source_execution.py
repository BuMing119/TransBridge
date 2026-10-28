"""Synchronous source workflow; independent of Qt objects and active UI context."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass, field
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from transbridge.ai_translator.activity import batch_message
from transbridge.application.translation.entry_alias import ai_entry_id, ai_entry_key
from transbridge.application.translation.task_scope import SourceTask

from .source_reporting import (
    build_source_snapshot as build_source_snapshot,
    build_task_record as build_task_record,
    render_source_report as render_source_report,
)
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
        project_progress=None,
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
        self.project_progress = project_progress
        self.name_progress = None
        from .source_terms import project_term_pool
        from .task_references import reference_store_for_request

        self.reference_store = getattr(consistency, "reference_store", None) or reference_store_for_request(request)
        from transbridge.ai_translator.project_reference_snapshot import capture_project_reference

        self.reference_store.capture_published(capture_project_reference(getattr(request, "terminology_binding", None)))
        self.shared_term_pool = getattr(consistency, "shared_term_pool", None) or project_term_pool(request)
        if consistency is not None:
            consistency.shared_term_pool = self.shared_term_pool
        self.initial_term_revision = 0 if self.shared_term_pool is None else self.shared_term_pool.revision
        self.batch_executor = None
        self.prepared_names = {}
        self.prepared_name_term_counts = {}
        self.reused_drafts = {}

    def prepare_names(self, tasks):
        from .task_name_preparation import prepare_project_names
        from .task_translation_resume import prepare_translation_recovery

        recovered = prepare_translation_recovery(self, tasks)
        self.recovered_translations = recovered
        self.prepared_names = prepare_project_names(self, tasks)
        for source, values in recovered.items():
            self.prepared_names.setdefault(source, {}).update(values)

    def execute(self, task: SourceTask) -> SourceOutcome:
        store = WorkflowLogStore(task.esp_path, workflow="ai_task", max_concurrent=self.config.max_concurrent)
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
            result.cancelled = self.stop.is_set()
        finally:
            result.diagnostics = tuple(getattr(result.polish, "diagnostics", ()))
            store.close()
        return result

    def _translate(self, task, store, *, repair_requirements=None, name_preparation=False):
        from transbridge.ai_translator.translator import AutoTranslator, TranslatorConfig

        from .source_terms import source_run_id, source_terminology_path
        from .task_translation_resume import postprocess_recovery_options
        from .workflow_logging_client import WorkflowLoggingLLMClient

        config = deepcopy(self.config)
        if repair_requirements or name_preparation:
            config.enable_post_process = False
        run_id = source_run_id(self.attempt_id or self.request.run_id, task.key)
        if repair_requirements:
            run_id += "-term-repair"
        elif name_preparation:
            run_id += "-names"

        translator = AutoTranslator(
            TranslatorConfig(
                config,
                source_terminology_path(task.esp_path, task.key),
                bool(repair_requirements) or self.request.spec.overwrite,
            ),
            self.client,
            self.project_id,
            shared_in_flight_terms=self.shared_terms if self.shared_term_pool is None else None,
            shared_in_flight_lock=self.terms_lock if self.shared_term_pool is None else None,
            run_id_factory=lambda: run_id,
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
            terminology_access=None if self.consistency is None else self.consistency.terminology_access,
            terminology_write_access=(None if self.consistency is None else self.consistency.terminology_write_access),
            shared_term_pool=self.shared_term_pool,
            generated_term_plugin_id=Path(task.esp_path).name if task.esp_path else None,
            reference_store=self.reference_store,
            reference_source=task.key,
            request_lane=task.key,
            batch_executor=self.batch_executor,
            prepared_names=None if name_preparation or repair_requirements else self.prepared_names.get(task.key),
            committed_draft=None if name_preparation or repair_requirements else self.reused_drafts.get(task.key),
            defer_generated_terms=name_preparation,
            **({} if name_preparation or repair_requirements else postprocess_recovery_options(self, task)),
            **self.request.terminology_binding.translator_kwargs(),
        )

        def translation_progress(current, total, message, *_):
            if name_preparation and self.name_progress is not None:
                self.name_progress.update("translate", task.key, current=current, detail=message)
            self.progress(task.key, "project_names" if name_preparation else "翻译", current, total, message)

        def stage_progress(stage, current, total, message):
            if name_preparation and self.name_progress is not None:
                # Terms, commits and reports have their own local denominators;
                # they describe this source without advancing project names.
                self.name_progress.update("translate", task.key, detail=message)
            self.progress(task.key, "project_names" if name_preparation else stage, current, total, message)

        result = translator.translate(
            collection=task.collection,
            target_entry_ids=[ai_entry_key(e) for e in task.translate_entries],
            strict_target_scope=True,
            progress_callback=translation_progress,
            stop_event=self.stop,
            pause_event=self.pause,
            log_callback=lambda idx, text: self.log(task.key, batch_message(idx, text)),
            stream_callback=lambda idx, chunk: store.write_chunk(f"batch_{idx:03d}", chunk),
            stage_progress_callback=stage_progress,
            term_repair_requirements=repair_requirements,
        )
        if (
            not name_preparation
            and not repair_requirements
            and (added := self.prepared_name_term_counts.get(task.key, 0))
        ):
            result.new_dynamic_terms += added
        return result

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
            terminology_access=None if self.consistency is None else self.consistency.terminology_access,
            shared_term_pool=self.shared_term_pool,
            generated_term_plugin_id=Path(task.esp_path).name if task.esp_path else None,
            request_lane=task.key,
            reference_store=self.reference_store,
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

    def close_terms(self, outcomes):
        from .source_terms import close_source_terms

        return close_source_terms(self, outcomes)


class _PolishResults(dict):
    def __init__(self, values=(), *, diagnostics=()):
        super().__init__(values)
        self.diagnostics = tuple(diagnostics)
