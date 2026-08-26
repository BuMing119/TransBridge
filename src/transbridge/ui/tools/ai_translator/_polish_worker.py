"""AI 润色后台线程。"""

from __future__ import annotations

import logging
import threading
import traceback
from typing import TYPE_CHECKING

from PyQt6.QtCore import QThread, pyqtSignal

if TYPE_CHECKING:
    from transbridge.ai_translator.post_processor.proofread_pipeline import ProofreadResult
    from transbridge.converter.translation_entry import TranslationEntry

_logger = logging.getLogger(__name__)


class _PolishWorker(QThread):
    progress = pyqtSignal(int, int, str)  # current, total, message
    entry_done = pyqtSignal(str, object)  # entry_id, PolishResult
    finished_all = pyqtSignal(dict)  # {entry_id: PolishResult}
    error = pyqtSignal(str)

    def __init__(self, pipeline_factory, entries: list[TranslationEntry], *, max_workers: int = 1):
        super().__init__()
        self._pipeline_factory = pipeline_factory
        self._entries = entries
        self._max_workers = max(1, max_workers)
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
        results: dict[str, ProofreadResult] = {}
        total = len(self._entries)

        try:
            phase_labels = {
                "detect": "检测",
                "refine": "修复",
                "polish": "润色",
                "arbitrate": "裁决",
                "execute": "汇总",
            }

            def on_progress(phase: str, current: int, phase_total: int, message: str) -> None:
                label = phase_labels.get(phase, phase)
                self.progress.emit(current, phase_total, f"{label}：{message}")

            pipeline = self._pipeline_factory()
            results = pipeline.process(
                self._entries,
                progress_callback=on_progress,
                stop_event=self._stop_event,
                pause_event=self._pause_event,
                max_workers=self._max_workers,
            )
            for entry_id, result in results.items():
                self.entry_done.emit(entry_id, result)
            self.progress.emit(total, total, f"校改完成，共 {len(results)} 条")
            self.finished_all.emit(results)

        except Exception as exc:
            _logger.error("润色Worker异常:\n%s", traceback.format_exc())
            self.error.emit(str(exc))
