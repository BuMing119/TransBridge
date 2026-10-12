"""Translate post-process counters into safe, visible workflow progress."""

from collections.abc import Callable
import logging

from .proofread_events import ProofreadEventLog

logger = logging.getLogger(__name__)

PHASE_LABELS = {
    "terms": "术语准备",
    "proofread": "校对",
    "detect": "检测",
    "consistency": "一致性检查",
    "format": "格式检查",
    "quality_gate": "质量检查",
    "refine": "精修",
    "refinement": "精修",
    "polish": "润色",
    "arbitrate": "仲裁",
    "arbitration": "仲裁",
    "finalize": "整理结果",
    "commit_translation": "整理翻译结果",
}


class PostProcessProgress:
    """Bind stage-local counters without changing candidate processing or results."""

    def __init__(
        self,
        progress_callback: Callable[[str, int, int, str], None] | None,
        log_callback: Callable[[str], None] | None,
    ) -> None:
        self._callback = progress_callback
        self._events = ProofreadEventLog(log_callback)

    def for_phase(self, phase: str) -> Callable[[int, int, str], None]:
        label = PHASE_LABELS.get(phase, phase)

        def report(current: int, total: int, message: str = "") -> None:
            message = message or (f"开始{label}，共 {total} 条" if current == 0 else f"{label} {current}/{total} 条")
            if self._callback is not None:
                try:
                    self._callback(phase, current, total, message)
                except Exception:
                    logger.warning("Post-process progress callback failed; processing will continue.", exc_info=True)
            if current == 0:
                self._events.emit(message)
            elif phase != "proofread":
                # ProofreadStage already emits bounded progress and recovery events.
                self._events.progress(label, current, total)

        return report

    def event(self, message: str) -> None:
        self._events.emit(message)
