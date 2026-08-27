"""Presentation progress shared by proofreading and mixed AI workflows."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock

_STAGE_LABELS = {
    "terms": "术语抽取",
    "translate": "翻译",
    "proofread": "校对",
    "detect": "检测",
    "refine": "修复",
    "polish": "润色",
    "arbitrate": "裁决",
    "execute": "汇总",
}


@dataclass(frozen=True, slots=True)
class WorkflowProgress:
    stage: str
    stage_label: str
    current: int
    total: int
    message: str
    overall_current: int
    overall_total: int = 1000
    success: int | None = None
    failed: int | None = None
    pending: int | None = None
    issues: int | None = None
    new_terms: int | None = None


def stages_for_profile(
    profile: object | None,
    *,
    include_translation: bool,
    include_term_extraction: bool = False,
) -> tuple[tuple[str, str], ...]:
    """Return only stages that the frozen execution profile says will run."""

    stages: list[tuple[str, str]] = []
    if include_translation and (profile is None or bool(getattr(profile, "enable_translation", True))):
        if include_term_extraction:
            stages.append(("terms", _STAGE_LABELS["terms"]))
        stages.append(("translate", _STAGE_LABELS["translate"]))
    if profile is None:
        if not include_translation:
            stages.extend((key, _STAGE_LABELS[key]) for key in ("detect", "refine", "polish", "arbitrate"))
    else:
        if bool(getattr(profile, "enable_proofread", False)):
            stages.append(("proofread", _STAGE_LABELS["proofread"]))
        else:
            if any(
                bool(getattr(profile, field, False))
                for field in ("enable_consistency_check", "enable_format_validation", "enable_quality_gate")
            ):
                stages.append(("detect", _STAGE_LABELS["detect"]))
            if bool(getattr(profile, "enable_refinement", False)):
                stages.append(("refine", _STAGE_LABELS["refine"]))
            if bool(getattr(profile, "enable_polish", False)):
                stages.append(("polish", _STAGE_LABELS["polish"]))
            if bool(getattr(profile, "enable_arbitration", False)):
                stages.append(("arbitrate", _STAGE_LABELS["arbitrate"]))
    if any(key not in {"terms", "translate"} for key, _label in stages):
        stages.append(("execute", _STAGE_LABELS["execute"]))
    return tuple(stages)


class WorkflowProgressTracker:
    """Aggregate real stage counters into one monotonic display percentage."""

    def __init__(self, stages: tuple[tuple[str, str], ...], *, sequential: bool = True) -> None:
        self.stages = stages
        self._labels = dict(stages)
        self._fractions = {key: 0.0 for key, _label in stages}
        self._sequential = sequential
        self._lock = Lock()

    def update(
        self,
        stage: str,
        current: int,
        total: int,
        message: str,
        **stats: int | None,
    ) -> WorkflowProgress | None:
        if stage not in self._fractions:
            return None
        safe_total = max(0, int(total))
        safe_current = max(0, min(int(current), safe_total)) if safe_total else 0
        with self._lock:
            if self._sequential:
                index = next(index for index, value in enumerate(self.stages) if value[0] == stage)
                for previous, _label in self.stages[:index]:
                    self._fractions[previous] = 1.0
            fraction = safe_current / safe_total if safe_total else 0.0
            self._fractions[stage] = max(self._fractions[stage], fraction)
            overall = round(sum(self._fractions.values()) / max(1, len(self._fractions)) * 1000)
        return WorkflowProgress(
            stage=stage,
            stage_label=self._labels[stage],
            current=safe_current,
            total=safe_total,
            message=message,
            overall_current=overall,
            **stats,
        )

    def finish(self, message: str = "全部阶段已完成", **stats: int | None) -> WorkflowProgress:
        with self._lock:
            for stage in self._fractions:
                self._fractions[stage] = 1.0
        return WorkflowProgress(
            stage="done",
            stage_label="完成",
            current=1,
            total=1,
            message=message,
            overall_current=1000,
            **stats,
        )


__all__ = ["WorkflowProgress", "WorkflowProgressTracker", "stages_for_profile"]
