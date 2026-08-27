"""AI 润色后台线程。"""

from __future__ import annotations

import logging
import threading
import traceback
from typing import TYPE_CHECKING

from PyQt6.QtCore import QThread, pyqtSignal

from .workflow_log_store import WorkflowLogStore
from .workflow_progress import WorkflowProgressTracker, stages_for_profile

if TYPE_CHECKING:
    from transbridge.ai_translator.post_processor.proofread_pipeline import ProofreadResult
    from transbridge.converter.translation_entry import TranslationEntry

_logger = logging.getLogger(__name__)


class _PolishWorker(QThread):
    progress = pyqtSignal(int, int, str)  # current, total, message
    detailed_progress = pyqtSignal(object)
    log = pyqtSignal(str)
    entry_done = pyqtSignal(str, object)  # entry_id, PolishResult
    finished_all = pyqtSignal(dict)  # {entry_id: PolishResult}
    error = pyqtSignal(str)

    def __init__(
        self,
        pipeline_factory,
        entries: list[TranslationEntry],
        *,
        max_workers: int = 1,
        profile: object | None = None,
        esp_path: str | None = None,
        log_store: WorkflowLogStore | None = None,
        stop_event: threading.Event | None = None,
        pause_event: threading.Event | None = None,
    ):
        super().__init__()
        self._pipeline_factory = pipeline_factory
        self._entries = entries
        self._max_workers = max(1, max_workers)
        self._profile = profile
        self._stop_event = stop_event or threading.Event()
        self._pause_event = pause_event or threading.Event()
        self._pause_event.set()
        self._log_store = log_store or WorkflowLogStore(esp_path, workflow="polish")

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

    @property
    def was_cancelled(self) -> bool:
        return self._stop_event.is_set()

    @property
    def stream_log_dir(self) -> str:
        return self._log_store.log_dir if self._log_store.is_available else ""

    @property
    def stream_log_error(self) -> str:
        return self._log_store.last_error

    def run(self):
        results: dict[str, ProofreadResult] = {}
        total = len(self._entries)

        try:
            pipeline = self._pipeline_factory()
            profile = self._profile or pipeline.profile
            tracker = WorkflowProgressTracker(stages_for_profile(profile, include_translation=False))

            def on_progress(phase: str, current: int, phase_total: int, message: str) -> None:
                label = dict(tracker.stages).get(phase, phase)
                self._log_store.write_line(f"stage_{phase}", f"{current}/{phase_total} {message}")
                self.progress.emit(current, phase_total, f"{label}：{message}")
                update = tracker.update(phase, current, phase_total, message)
                if update is not None:
                    self.detailed_progress.emit(update)

            results = pipeline.process(
                self._entries,
                progress_callback=on_progress,
                log_callback=self._on_log,
                stop_event=self._stop_event,
                pause_event=self._pause_event,
                max_workers=self._max_workers,
            )
            for entry_id, result in results.items():
                self.entry_done.emit(entry_id, result)
            success = sum(1 for result in results.values() if result.accepted)
            failed = sum(1 for result in results.values() if result.verdict in {"error", "failed", "reject"})
            pending = len(results) - success - failed
            issues = sum(len(result.issues) for result in results.values())
            if self._stop_event.is_set():
                self.progress.emit(len(results), total, f"校改已停止，保留 {len(results)} 条结果")
            else:
                self.progress.emit(total, total, f"校改完成，共 {len(results)} 条")
                self.detailed_progress.emit(
                    tracker.finish(
                        f"校改完成，共 {len(results)} 条",
                        success=success,
                        failed=failed,
                        pending=pending,
                        issues=issues,
                    )
                )
            self.finished_all.emit(results)

        except Exception as exc:
            _logger.error("润色Worker异常:\n%s", traceback.format_exc())
            self.error.emit(str(exc))
        finally:
            self._log_store.close()

    def _on_log(self, line: str) -> None:
        self._log_store.write_line("workflow", line)
        self.log.emit(line)
