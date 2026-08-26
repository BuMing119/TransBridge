"""混合模式 Worker：统一调度翻译 + 校改润色候选。"""

from dataclasses import dataclass
from pathlib import Path
import threading
from uuid import uuid4

from PyQt6.QtCore import QThread, pyqtSignal


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


class _MixedWorker(QThread):
    """后台线程：串行/并行执行翻译+润色。"""

    progress = pyqtSignal(MixedProgress)
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
    ):
        super().__init__()
        self._cfg = cfg
        self._translate_entries = translate_entries
        self._polish_entries = polish_entries
        self._order = execution_order
        self._ctx = ctx
        self._run_id = run_id or f"mixed-{uuid4().hex}"
        self._run_spec = run_spec
        self._cancelled = threading.Event()

    def cancel(self):
        self._cancelled.set()

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
            return
        if not self._cancelled.is_set():
            self.finished.emit(result)
        else:
            self.cancelled.emit()

    def _run_serial(self) -> dict:
        result = {}
        # 阶段 1：翻译
        if self._translate_entries:
            prog = MixedProgress()
            prog.stage = "translate"
            prog.translate_total = len(self._translate_entries)
            self.progress.emit(prog)
            t_result = self._do_translate()
            result["translate"] = t_result
            prog.translate_done = prog.translate_total
            prog.translate_success = t_result.success_count if t_result else 0
            prog.translate_failed = t_result.failed_count if t_result else 0
            self.progress.emit(prog)

        if self._cancelled.is_set():
            return result

        # 阶段 2：润色
        if self._polish_entries:
            prog = MixedProgress()
            prog.stage = "polish"
            prog.polish_total = len(self._polish_entries)
            # 继承翻译阶段的计数
            if result.get("translate"):
                prog.translate_total = len(self._translate_entries)
                prog.translate_done = prog.translate_total
                prog.translate_success = result["translate"].success_count
            self.progress.emit(prog)
            p_result = self._do_polish()
            result["polish"] = p_result

        prog = MixedProgress()
        prog.stage = "done"
        if result.get("translate"):
            prog.translate_total = len(self._translate_entries)
            prog.translate_done = prog.translate_total
            prog.translate_success = result["translate"].success_count
        if result.get("polish"):
            prog.polish_total = len(self._polish_entries)
            prog.polish_done = prog.polish_total
            prog.polish_success = result["polish"].success_count
        self.progress.emit(prog)
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

        prog = MixedProgress()
        prog.stage = "done"
        if result.get("translate"):
            prog.translate_total = len(self._translate_entries)
            prog.translate_done = prog.translate_total
            prog.translate_success = result["translate"].success_count
        if result.get("polish"):
            prog.polish_total = len(self._polish_entries)
            prog.polish_done = prog.polish_total
            prog.polish_success = result["polish"].success_count
        self.progress.emit(prog)
        return result

    def _do_translate(self):
        """执行翻译（复用 AutoTranslator）。"""
        from transbridge.ai_translator.translator import AutoTranslator, TranslatorConfig

        if self._ctx is None or not self._ctx.collection or not self._ctx.esp_path:
            raise RuntimeError("混合翻译需要活动集合和源文件路径")
        translator = AutoTranslator(
            TranslatorConfig(
                llm_config=self._cfg,
                esp_path=self._ctx.esp_path,
                overwrite=False,
            ),
            run_id_factory=lambda: self._run_id,
        )
        result = translator.translate(
            collection=self._ctx.collection,
            target_entry_ids=[e.key for e in self._translate_entries],
            progress_callback=lambda *a: None,
            stop_event=self._cancelled,
        )
        return result

    def _do_polish(self):
        """Create proofreading candidates; the UI result boundary commits pass results."""
        from transbridge.ai_translator.post_processor.proofread_pipeline import ProofreadPipeline
        from transbridge.ai_translator.term_database import TermDatabaseManager
        from transbridge.application.translation.ai_execution_profile import AiExecutionProfile
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
            term_manager = TermDatabaseManager(
                self._cfg,
                self._ctx.esp_path,
                paratranz_client,
                project_id,
            )
            term_manager.load_all()
        profile = AiExecutionProfile.from_config("mixed", self._cfg)
        pipeline = ProofreadPipeline.create(
            profile=profile,
            llm_client=create_llm_client(self._cfg) if profile.requires_llm else None,
            term_manager=term_manager,
        )
        candidates = pipeline.process(
            self._polish_entries,
            stop_event=self._cancelled,
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
        return MixedPolishResult(success, len(details) - success, details, candidates)

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
        finalized["artifacts"] = render_snapshot_report(snapshot, esp_stem)
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
