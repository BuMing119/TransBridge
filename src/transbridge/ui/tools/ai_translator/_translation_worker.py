"""AI 翻译后台线程。"""

from __future__ import annotations

import os
import threading
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal


class _TranslationWorker(QThread):
    # current, total, message, success, failed, new_terms
    progress = pyqtSignal(int, int, str, int, int, int)
    log = pyqtSignal(int, str)  # (batch_idx, line) 详细日志行；batch_idx=-1 表示轮次级消息
    result = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, translator, collection, target_entries, checkpoint=None):
        super().__init__()
        self._translator = translator
        self._collection = collection
        self._target_entries = target_entries
        self._checkpoint = checkpoint
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()   # 初始为运行状态
        self._stream_log_path: str | None = None

    @property
    def stream_log_path(self) -> str | None:
        """LLM 流式响应日志文件路径，run() 启动后可用。"""
        return self._stream_log_path

    def _make_stream_log_path(self) -> str:
        from src.transbridge.paratranz.config_manager import ParatranzConfig
        log_dir = os.path.join(ParatranzConfig.get_data_dir(), "log")
        os.makedirs(log_dir, exist_ok=True)
        esp_stem = Path(self._translator._cfg.esp_path).stem
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return os.path.join(log_dir, f"{esp_stem}_{timestamp}.log")

    def stop(self):
        self._stop_event.set()
        self._pause_event.set()   # 确保 wait() 不阻塞

    def pause(self):
        self._pause_event.clear()

    def resume(self):
        self._pause_event.set()

    @property
    def is_paused(self) -> bool:
        return not self._pause_event.is_set()

    def run(self):
        self._stream_log_path = self._make_stream_log_path()
        try:
            with open(self._stream_log_path, "w", encoding="utf-8") as log_f:
                def _stream_cb(chunk: str):
                    log_f.write(chunk)
                    log_f.flush()

                result = self._translator.translate(
                    collection=self._collection,
                    target_entry_ids=self._target_entries,
                    progress_callback=lambda c, t, m, s, fc, n: self.progress.emit(c, t, m, s, fc, n),
                    stop_event=self._stop_event,
                    pause_event=self._pause_event,
                    checkpoint=self._checkpoint,
                    log_callback=lambda idx, line: self.log.emit(idx, line),
                    stream_callback=_stream_cb,
                )
            self.result.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))