"""混合模式 Worker：统一调度翻译 + 润色。"""

import threading

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


class _MixedWorker(QThread):
    """后台线程：串行/并行执行翻译+润色。"""

    progress = pyqtSignal(MixedProgress)
    finished = pyqtSignal(dict)   # {"translate": result, "polish": result}
    error = pyqtSignal(str)

    def __init__(self, cfg, translate_entries, polish_entries,
                 execution_order="serial", ctx=None):
        super().__init__()
        self._cfg = cfg
        self._translate_entries = translate_entries
        self._polish_entries = polish_entries
        self._order = execution_order
        self._ctx = ctx
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
        except Exception as exc:
            if not self._cancelled.is_set():
                self.error.emit(str(exc))
            return
        if not self._cancelled.is_set():
            self.finished.emit(result)

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

        # 等待完成，期间检查取消
        for name, t in threads:
            while t.is_alive():
                if self._cancelled.is_set():
                    return result
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
        translator = AutoTranslator(TranslatorConfig(
            llm_config=self._cfg,
            esp_path=self._ctx.esp_path,
            overwrite=False,
        ))
        stop_event = threading.Event()
        result = translator.translate(
            collection=self._ctx.collection,
            target_entry_ids=[e.key for e in self._translate_entries],
            progress_callback=lambda *a: None,
            stop_event=stop_event,
        )
        return result

    def _do_polish(self):
        """执行润色（复用 LLMPolisher）。"""
        from transbridge.ai_translator.post_processor.polisher import LLMPolisher
        polisher = LLMPolisher(self._cfg)
        details = []
        for entry in self._polish_entries:
            if self._cancelled.is_set():
                break
            try:
                r = polisher.polish_single(entry)
                details.append({
                    "entry_id": entry.id,
                    "key": entry.key,
                    "original": entry.original,
                    "translation": entry.translation,
                    "polished": r.polished_text if hasattr(r, 'polished_text') else str(r),
                    "success": True,
                })
            except Exception as exc:
                details.append({
                    "entry_id": entry.id,
                    "key": entry.key,
                    "original": entry.original,
                    "translation": entry.translation,
                    "success": False,
                    "error": str(exc),
                })
        ok = sum(1 for d in details if d["success"])
        fail = len(details) - ok
        class PolishResult:
            def __init__(self, success, failed, details):
                self.success_count = success
                self.failed_count = failed
                self.details = details
        return PolishResult(ok, fail, details)
