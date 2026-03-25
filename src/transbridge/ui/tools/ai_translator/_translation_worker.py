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
        self._stream_log_dir: str | None = None

    @property
    def stream_log_dir(self) -> str | None:
        """LLM 流式响应日志目录，run() 启动后可用。"""
        return self._stream_log_dir

    def _make_stream_log_dir(self) -> str:
        from src.transbridge.paratranz.config_manager import ParatranzConfig
        log_base = os.path.join(ParatranzConfig.get_data_dir(), "log")
        esp_stem = Path(self._translator._cfg.esp_path).stem
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_dir = os.path.join(log_base, f"{esp_stem}_{timestamp}")
        os.makedirs(log_dir, exist_ok=True)
        return log_dir

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
        self._stream_log_dir = self._make_stream_log_dir()
        try:
            _file_handles: dict[int, object] = {}

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
            )
            for fh in _file_handles.values():
                fh.close()
            self.result.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))