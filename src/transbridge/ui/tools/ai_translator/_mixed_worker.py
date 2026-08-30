"""混合模式 Worker：统一调度翻译 + 校改润色候选。"""

from dataclasses import dataclass
from pathlib import Path
import re
import threading
from uuid import uuid4

from PyQt6.QtCore import QThread, pyqtSignal

from .workflow_log_store import WorkflowLogStore
from .workflow_logging_client import WorkflowLoggingLLMClient
from .workflow_progress import WorkflowProgressTracker, stages_for_profile


class MixedProgress:
    """混合执行进度。"""

    translate_total: int = 0
    translate_done: int = 0
    translate_success: int = 0
    translate_failed: int = 0
    polish_total: int = 0
    polish_done: int = 0
    polish_success: int = 0
    polish_failed: int = 0
    stage: str = ""  # "translate" | "polish" | "done"


@dataclass(frozen=True, slots=True)
class MixedPolishResult:
    success_count: int
    failed_count: int
    details: tuple[dict, ...]
    candidates: dict
    pending_count: int = 0


class _MixedWorker(QThread):
    """后台线程：串行/并行执行翻译+润色。"""

    progress = pyqtSignal(object)
    log = pyqtSignal(str)
    finished = pyqtSignal(dict)  # {"translate": result, "polish": result}
    error = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(
        self,
        cfg,
        translate_entries,
        polish_entries,
        execution_order="serial",
        ctx=None,
        *,
        run_id: str | None = None,
        run_spec: object | None = None,
        request_budget: object | None = None,
        terminology_binding: object | None = None,
    ):
        super().__init__()
        self._cfg = cfg
        self._translate_entries = translate_entries
        self._polish_entries = polish_entries
        self._order = execution_order
        self._ctx = ctx
        self._run_id = run_id or f"mixed-{uuid4().hex}"
        self._run_spec = run_spec
        if request_budget is None:
            from transbridge.application.translation.ai_request_budget import AiRequestBudget

            request_budget = AiRequestBudget(int(getattr(cfg, "max_concurrent", 1)))
        self._request_budget = request_budget
        if terminology_binding is None:
            from transbridge.ai_translator.project_terminology_runtime import ProjectTerminologyBinding

            terminology_binding = ProjectTerminologyBinding()
        self._terminology_binding = terminology_binding
        self._cancelled = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._stats_lock = threading.Lock()
        self._translate_success = 0
        self._translate_failed = 0
        self._new_terms = 0
        self._term_completed = 0
        self._term_failed = 0
        self._term_candidates = 0
        esp_path = str(getattr(ctx, "esp_path", "") or "")
        self._log_store = WorkflowLogStore(esp_path, workflow="mixed")

        profile = getattr(run_spec, "execution_profile", None)
        if profile is None:
            from transbridge.application.translation.ai_execution_profile import AiExecutionProfile

            profile = AiExecutionProfile.from_config("mixed", cfg)
        self._profile = profile
        progress_stages = stages_for_profile(
            profile,
            include_translation=bool(translate_entries),
            include_term_extraction=bool(translate_entries) and bool(getattr(cfg, "retrieval_enabled", True)),
        )
        if not polish_entries:
            progress_stages = tuple(stage for stage in progress_stages if stage[0] in {"terms", "translate"})
        self._tracker = WorkflowProgressTracker(progress_stages, sequential=execution_order != "parallel")

    def cancel(self):
        self._cancelled.set()
        self._pause_event.set()

    @property
    def execution_order(self) -> str:
        return self._order

    @property
    def progress_stages(self) -> tuple[tuple[str, str], ...]:
        return self._tracker.stages

    @property
    def stream_log_dir(self) -> str:
        return self._log_store.log_dir if self._log_store.is_available else ""

    @property
    def stream_log_error(self) -> str:
        return self._log_store.last_error

    def pause(self):
        self._pause_event.clear()

    def resume(self):
        self._pause_event.set()

    @property
    def is_paused(self) -> bool:
        return not self._pause_event.is_set()

    def run(self):
        result = {"translate": None, "polish": None}
        try:
            if self._order == "parallel":
                result = self._run_parallel()
            else:
                result = self._run_serial()
            result = self._finalize_report(result)
        except Exception as exc:
            if not self._cancelled.is_set():
                self.error.emit(str(exc))
            else:
                self.cancelled.emit()
        else:
            if not self._cancelled.is_set():
                self._emit_terminal_progress(result)
                self.finished.emit(result)
            else:
                self.cancelled.emit()
        finally:
            self._log_store.close()

    def _run_serial(self) -> dict:
        result = {}
        # 阶段 1：翻译
        if self._translate_entries:
            t_result = self._do_translate()
            result["translate"] = t_result

        if self._cancelled.is_set():
            return result

        # 阶段 2：润色
        if self._polish_entries:
            p_result = self._do_polish()
            result["polish"] = p_result
        return result

    def _run_parallel(self) -> dict:
        """并行执行：使用线程分别跑翻译和润色。"""
        result = {}
        threads = []

        def run_translate():
            if self._translate_entries:
                result["translate"] = self._do_translate()

        def run_polish():
            if self._polish_entries:
                result["polish"] = self._do_polish()

        if self._translate_entries:
            t = threading.Thread(target=run_translate, daemon=True)
            threads.append(("translate", t))
        if self._polish_entries:
            t = threading.Thread(target=run_polish, daemon=True)
            threads.append(("polish", t))

        for _, t in threads:
            t.start()

        # A cancel request stops new work, but the outer worker remains alive
        # until both children leave their current safe point.  Returning while
        # daemon children still mutate results would publish a false terminal.
        for _name, t in threads:
            while t.is_alive():
                t.join(0.1)

        return result

    def _do_translate(self):
        """执行翻译（复用 AutoTranslator）。"""
        from transbridge.ai_translator.translator import AutoTranslator, TranslatorConfig

        if self._ctx is None or not self._ctx.collection or not self._ctx.esp_path:
            raise RuntimeError("混合翻译需要活动集合和源文件路径")
        terminology = self._terminology_binding
        translator = AutoTranslator(
            TranslatorConfig(
                llm_config=self._cfg,
                esp_path=self._ctx.esp_path,
                overwrite=False,
            ),
            run_id_factory=lambda: self._run_id,
            request_budget=self._request_budget,
            llm_client_wrapper=lambda client: WorkflowLoggingLLMClient(
                client,
                self._log_store,
                channel_prefix="translation_call",
            ),
            term_llm_client_wrapper=lambda client: WorkflowLoggingLLMClient(
                client,
                self._log_store,
                channel_prefix="term_llm",
            ),
            **terminology.translator_kwargs(),
        )
        result = translator.translate(
            collection=self._ctx.collection,
            target_entry_ids=[e.key for e in self._translate_entries],
            progress_callback=self._on_translate_progress,
            stop_event=self._cancelled,
            pause_event=self._pause_event,
            log_callback=self._on_translate_log,
            stream_callback=lambda batch_index, chunk: self._log_store.write_chunk(f"batch_{batch_index:03d}", chunk),
            stage_progress_callback=self._on_translate_stage_progress,
        )
        return result

    def _do_polish(self):
        """Create proofreading candidates; the UI result boundary commits pass results."""
        from transbridge.ai_translator.post_processor.proofread_pipeline import ProofreadPipeline
        from transbridge.ai_translator.term_database import TermDatabaseManager
        from transbridge.infra.llm_client import create_llm_client

        term_manager = None
        if self._ctx is not None and self._ctx.esp_path:
            from transbridge.ui.paratranz.target_context import bound_paratranz_project

            remote_project = bound_paratranz_project(self._ctx)
            paratranz_client = None
            project_id = None
            if remote_project:
                from transbridge.paratranz.api.paratranz_terms_api import ParatranzTermsAPI

                paratranz_client = ParatranzTermsAPI(self._ctx.config)
                project_id = remote_project["id"]
            terminology = self._terminology_binding
            term_manager = TermDatabaseManager(
                self._cfg,
                self._ctx.esp_path,
                paratranz_client,
                project_id,
                **terminology.term_database_kwargs(),
            )
            term_manager.load_all()
        profile = self._profile
        llm_client = create_llm_client(self._cfg) if profile.requires_llm else None
        arbitration_llm_client = None
        if llm_client is not None:
            from transbridge.infra.limited_llm_client import LimitedLLMClient
            from transbridge.infra.llm_reasoning import ReasoningIntent, with_reasoning_intent

            provider_client = llm_client
            direct_client = with_reasoning_intent(provider_client, self._cfg, ReasoningIntent.PREFER_DIRECT)
            low_client = with_reasoning_intent(provider_client, self._cfg, ReasoningIntent.PREFER_LOW)
            llm_client = LimitedLLMClient(
                direct_client,
                self._request_budget,
                cancel_event=self._cancelled,
                pause_event=self._pause_event,
            )
            llm_client = WorkflowLoggingLLMClient(
                llm_client,
                self._log_store,
                channel_prefix="proofread_call",
            )
            arbitration_llm_client = LimitedLLMClient(
                low_client,
                self._request_budget,
                cancel_event=self._cancelled,
                pause_event=self._pause_event,
            )
            arbitration_llm_client = WorkflowLoggingLLMClient(
                arbitration_llm_client,
                self._log_store,
                channel_prefix="arbitration_call",
            )
        pipeline = ProofreadPipeline.create(
            profile=profile,
            llm_client=llm_client,
            arbitration_llm_client=arbitration_llm_client,
            term_manager=term_manager,
            model=self._cfg.model,
            max_tokens_per_batch=self._cfg.max_tokens_per_batch,
            max_output_tokens=self._cfg.max_output_tokens,
        )
        candidates = pipeline.process(
            self._polish_entries,
            progress_callback=self._on_polish_progress,
            log_callback=self._on_polish_log,
            stop_event=self._cancelled,
            pause_event=self._pause_event,
            max_workers=self._cfg.max_concurrent,
        )
        details = tuple(
            {
                "entry_id": entry.id,
                "key": entry.key,
                "original": entry.original,
                "translation": entry.translation,
                "polished": candidate.polished_translation,
                "verdict": candidate.verdict,
                "success": candidate.accepted,
                "error": "" if candidate.accepted else candidate.note or candidate.verdict,
            }
            for entry in self._polish_entries
            if (candidate := candidates.get(entry.id)) is not None
        )
        success = sum(1 for detail in details if detail["success"])
        failed = sum(1 for detail in details if detail["verdict"] in {"error", "failed", "reject"})
        pending = len(details) - success - failed
        return MixedPolishResult(success, failed, details, candidates, pending)

    def _on_translate_progress(
        self,
        current: int,
        total: int,
        message: str,
        success: int,
        failed: int,
        new_terms: int,
    ) -> None:
        """Project AutoTranslator batch progress into the shared workflow view."""
        with self._stats_lock:
            self._translate_success = success
            self._translate_failed = failed
            self._new_terms = new_terms
        update = self._tracker.update(
            "translate",
            current,
            total,
            message,
            success=success,
            failed=failed,
            new_terms=new_terms,
        )
        if update is not None:
            self.progress.emit(update)

    def _on_translate_log(self, batch_index: int, line: str) -> None:
        """Flatten translation's indexed logs for the unified text log."""
        self._log_store.write_line("workflow", line)
        if batch_index < 0:
            self.log.emit(line)
        else:
            self.log.emit(f"[翻译批次 {batch_index}] {line}")

    def _on_translate_stage_progress(self, stage: str, current: int, total: int, message: str) -> None:
        """Project translation preparation, especially existing-term extraction."""
        self._log_store.write_line(f"stage_{stage}", f"{current}/{total} {message}")
        with self._stats_lock:
            if stage == "terms":
                if message.startswith("已完成术语抽取") and current > self._term_completed:
                    if match := re.search(r"本批新增候选\s+(\d+)\s+个", message):
                        self._term_candidates += int(match.group(1))
                    self._term_completed = current
                terminal_failure = "失败" in message and current >= total
                if terminal_failure:
                    self._term_failed = max(1, total - self._term_completed)
                elif "失败" in message:
                    self._term_failed = 1
                success = self._term_completed
                failed = self._term_failed
                pending = 0 if terminal_failure else max(0, total - success - failed)
                new_terms = self._term_candidates
            else:
                success = self._translate_success
                failed = self._translate_failed
                pending = None
                new_terms = self._new_terms
        update = self._tracker.update(
            stage,
            current,
            total,
            message,
            success=success,
            failed=failed,
            pending=pending,
            new_terms=new_terms,
        )
        if update is not None:
            self.progress.emit(update)

    def _on_polish_log(self, line: str) -> None:
        self._log_store.write_line("workflow", line)
        self.log.emit(line)

    def _on_polish_progress(self, phase: str, current: int, total: int, message: str) -> None:
        """Forward all proofreading stages, including concurrently completed batches."""
        self._log_store.write_line(f"stage_{phase}", f"{current}/{total} {message}")
        with self._stats_lock:
            success = self._translate_success
            failed = self._translate_failed
            new_terms = self._new_terms
        update = self._tracker.update(
            phase,
            current,
            total,
            message,
            success=success,
            failed=failed,
            new_terms=new_terms,
        )
        if update is not None:
            self.progress.emit(update)

    def _emit_terminal_progress(self, result: dict) -> None:
        """Publish authoritative aggregate statistics at the terminal boundary."""
        translate_result = result.get("translate")
        polish_result = result.get("polish")
        translate_success = int(getattr(translate_result, "success_count", 0))
        translate_failed = int(getattr(translate_result, "failed_count", 0))
        new_terms = int(getattr(translate_result, "new_dynamic_terms", 0))

        candidates = getattr(polish_result, "candidates", {}) or {}
        polish_success = sum(1 for candidate in candidates.values() if candidate.accepted)
        polish_failed = sum(
            1 for candidate in candidates.values() if candidate.verdict in {"error", "failed", "reject"}
        )
        pending = len(candidates) - polish_success - polish_failed
        issues = sum(len(getattr(candidate, "issues", ()) or ()) for candidate in candidates.values())
        self.progress.emit(
            self._tracker.finish(
                "混合运行完成",
                success=translate_success + polish_success,
                failed=translate_failed + polish_failed,
                pending=pending,
                issues=issues,
                new_terms=new_terms,
            )
        )

    def _finalize_report(self, result: dict) -> dict:
        """Build and render the one canonical report before crossing the worker boundary."""
        profile = getattr(self._run_spec, "execution_profile", None)
        if self._polish_entries and bool(getattr(profile, "preview_enabled", False)):
            deferred = dict(result)
            deferred["snapshot"] = None
            deferred["artifacts"] = None
            return deferred

        from transbridge.application.translation.completion_report import build_translation_report_snapshot
        from transbridge.application.translation.mixed_report import build_mixed_report_snapshot
        from transbridge.application.translation.polish_report import build_polish_report_snapshot

        from .reporting import render_snapshot_report

        translate_result = result.get("translate")
        translation_snapshot = None
        if translate_result is not None:
            translation_snapshot = getattr(translate_result, "post_process_result", None)
            if translation_snapshot is None:
                translation_snapshot = build_translation_report_snapshot(
                    translate_result,
                    self._translate_entries,
                    run_id=self._run_id,
                    cancelled=self._cancelled.is_set(),
                )

        polish_result = result.get("polish")
        polish_snapshot = None
        if polish_result is not None:
            candidates = polish_result.candidates
            accepted = tuple(entry_id for entry_id, value in candidates.items() if value.accepted)
            rejected = tuple(
                entry_id for entry_id, value in candidates.items() if not value.accepted and value.confidence > 0
            )
            failed = tuple(
                str(entry.id) for entry in self._polish_entries if str(entry.id) not in set(accepted) | set(rejected)
            )
            polish_snapshot = build_polish_report_snapshot(
                candidates,
                self._polish_entries,
                accepted_entry_ids=accepted,
                rejected_entry_ids=rejected,
                failed_entry_ids=failed,
                run_id=self._run_id,
                polish_level=getattr(self._cfg, "pp_polish_level", None),
                run_spec_summary=self._run_summary(),
            )

        snapshot = build_mixed_report_snapshot(
            translation_snapshot,
            polish_snapshot,
            run_id=self._run_id,
            execution_order=self._order,
            run_spec_summary=self._run_summary(),
        )
        esp_stem = Path(self._ctx.esp_path).stem if self._ctx is not None and self._ctx.esp_path else "unknown"
        finalized = dict(result)
        finalized["snapshot"] = snapshot
        try:
            finalized["artifacts"] = render_snapshot_report(snapshot, esp_stem)
        except Exception as exc:  # report persistence must not reverse a completed business run
            finalized["artifacts"] = None
            finalized["report_error"] = f"REPORT_RENDER_FAILED: {type(exc).__name__}: {exc}"
        return finalized

    def _run_summary(self) -> dict[str, object]:
        spec = self._run_spec
        if spec is None:
            return {"run_mode": "mixed"}
        profile = getattr(spec, "execution_profile", None)
        return {
            "run_mode": "mixed",
            "input_fingerprint": str(getattr(spec, "input_fingerprint", "")),
            "config_digest": str(getattr(spec, "config_digest", "")),
            "execution_profile": {
                "stages": list(getattr(profile, "stages", ())),
                "summary": str(getattr(profile, "summary", "")),
                "digest": str(getattr(profile, "digest", "")),
            },
        }
