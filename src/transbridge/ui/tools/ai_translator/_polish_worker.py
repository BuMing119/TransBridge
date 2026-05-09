"""AI 润色后台线程。"""

from __future__ import annotations

import logging
import threading
import traceback
from typing import TYPE_CHECKING

from PyQt6.QtCore import QThread, pyqtSignal

if TYPE_CHECKING:
    from src.transbridge.ai_translator.post_processor.polisher import PolishResult
    from src.transbridge.converter.translation_entry import TranslationEntry

_logger = logging.getLogger(__name__)


class _PolishWorker(QThread):
    progress = pyqtSignal(int, int, str)  # current, total, message
    entry_done = pyqtSignal(str, object)  # entry_id, PolishResult
    finished_all = pyqtSignal(dict)       # {entry_id: PolishResult}
    error = pyqtSignal(str)

    def __init__(self, polisher, entries: list["TranslationEntry"]):
        super().__init__()
        self._polisher = polisher
        self._entries = entries
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()

    def stop(self):
        self._stop_event.set()
        self._pause_event.set()

    def pause(self):
        self._pause_event.clear()

    def resume(self):
        self._pause_event.set()

    @property
    def is_paused(self) -> bool:
        return not self._pause_event.is_set()

    def run(self):
        results: dict[str, "PolishResult"] = {}
        total = len(self._entries)

        try:
            for i, entry in enumerate(self._entries):
                if self._stop_event.is_set():
                    self.progress.emit(i, total, "已停止")
                    break

                if not self._pause_event.is_set():
                    self.progress.emit(i, total, "已暂停")
                    self._pause_event.wait()
                    if self._stop_event.is_set():
                        break
                    self.progress.emit(i, total, "已恢复")

                self.progress.emit(i, total, f"润色中 {i+1}/{total}: {entry.key[:60]}")

                try:
                    result = self._polisher.polish(entry)
                    results[entry.id] = result
                    self.entry_done.emit(entry.id, result)
                except Exception as exc:
                    _logger.error("润色条目 %s 失败: %s", entry.id, exc)
                    from src.transbridge.ai_translator.post_processor.polisher import PolishResult
                    results[entry.id] = PolishResult(
                        entry_id=entry.id,
                        original_translation=entry.translation or "",
                        polished_translation=entry.translation or "",
                        confidence=0.0,
                        needs_arbitration=True,
                        note=f"润色失败: {exc}",
                    )

            self.progress.emit(total, total, f"润色完成，共 {len(results)} 条")
            self.finished_all.emit(results)

        except Exception as exc:
            _logger.error("润色Worker异常:\n%s", traceback.format_exc())
            self.error.emit(str(exc))
