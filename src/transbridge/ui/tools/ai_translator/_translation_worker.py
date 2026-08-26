"""AI 翻译后台线程。"""

from __future__ import annotations

from datetime import datetime
import logging
import os
from pathlib import Path
import threading
import traceback

from PyQt6.QtCore import QThread, pyqtSignal

_logger = logging.getLogger(__name__)


class _TranslationWorker(QThread):
    # current, total, message, success, failed, new_terms
    progress = pyqtSignal(int, int, str, int, int, int)
    stage_progress = pyqtSignal(str, int, int, str)
    log = pyqtSignal(int, str)  # (batch_idx, line) 详细日志行；batch_idx=-1 表示轮次级消息
    result = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, translator, collection, target_entries, checkpoint=None, *, esp_path: str, log_store=None):
        super().__init__()
        self._translator = translator
        self._collection = collection
        self._target_entries = target_entries
        self._checkpoint = checkpoint
        self._esp_path = esp_path
        self._log_store = log_store
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()  # 初始为运行状态
        self._stream_log_dir: str | None = None

    @property
    def stream_log_dir(self) -> str | None:
        """LLM 流式响应日志目录，run() 启动后可用。"""
        return self._stream_log_dir

    @property
    def esp_stem(self) -> str:
        return Path(self._esp_path).stem

    def _make_stream_log_dir(self) -> str:
        if self._log_store is not None and self._log_store.is_available:
            return self._log_store.log_dir
        from transbridge.paratranz.config_manager import ParatranzConfig

        log_base = os.path.join(ParatranzConfig.get_data_dir(), "log")
        esp_stem = self.esp_stem
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_dir = os.path.join(log_base, f"{esp_stem}_{timestamp}")
        os.makedirs(log_dir, exist_ok=True)
        return log_dir

    def stop(self):
        self._stop_event.set()
        self._pause_event.set()  # 确保 wait() 不阻塞

    def pause(self):
        self._pause_event.clear()

    def resume(self):
        self._pause_event.set()

    @property
    def is_paused(self) -> bool:
        return not self._pause_event.is_set()

    def run(self):
        self._stream_log_dir = self._make_stream_log_dir()
        _file_handles: dict[int, object] = {}
        try:

            def _stream_cb(batch_idx: int, chunk: str):
                if batch_idx not in _file_handles:
                    path = os.path.join(self._stream_log_dir, f"batch_{batch_idx:03d}.log")
                    _file_handles[batch_idx] = open(path, "w", encoding="utf-8")
                fh = _file_handles[batch_idx]
                fh.write(chunk)
                fh.flush()

            result = self._translator.translate(
                collection=self._collection,
                target_entry_ids=self._target_entries,
                progress_callback=lambda c, t, m, s, fc, n: self.progress.emit(c, t, m, s, fc, n),
                stop_event=self._stop_event,
                pause_event=self._pause_event,
                checkpoint=self._checkpoint,
                log_callback=lambda idx, line: self.log.emit(idx, line),
                stream_callback=_stream_cb,
                stage_progress_callback=lambda stage, current, total, message: self.stage_progress.emit(
                    stage, current, total, message
                ),
            )
            try:
                from .reporting import render_translation_report

                report = render_translation_report(result.post_process_result, self.esp_stem)
                result.report_path = report.excel_path
                result.report_paths = report.paths
                result.report_diagnostics = report.diagnostics
            except Exception:
                _logger.exception("AI 翻译完成，但报告生成失败")
                result.report_diagnostics = ("REPORT_RENDER_FAILED: AI 翻译完成，但报告文件生成失败。",)
            self.result.emit(result)
        except Exception as exc:
            _logger.error("翻译Worker异常:\n%s", traceback.format_exc())
            self.error.emit(str(exc))
        finally:
            for fh in _file_handles.values():
                fh.close()
            if self._log_store is not None:
                self._log_store.close()
