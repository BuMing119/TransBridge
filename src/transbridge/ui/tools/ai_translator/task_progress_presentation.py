"""Keep project phase totals separate from concurrent source progress."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re

from transbridge.application.translation.postprocess_progress import PHASE_LABELS

_TRANSLATION_PREFIX = re.compile(r"^\[\s*\d+\s*%\]\s*\d+\s*/\s*\d+\s*\|\s*")
_NAME_PHASES = {
    "scan": ("统计重复名称", "条"),
    "load": ("加载名称术语", "个来源"),
    "resolve": ("检查名称复用约束", "次出现"),
    "reuse": ("写入已有译文", "条"),
    "translate": ("翻译代表名称", "个名称"),
    "validate": ("校验名称结果", "个名称"),
    "publish": ("保存共享名称", "个名称"),
    "done": ("名称准备完成", "个名称"),
    "cancelled": ("名称准备已取消", "项"),
}


def progress_detail(message: str) -> str:
    """Strip only the translator's known duplicate counter prefix."""
    return _TRANSLATION_PREFIX.sub("", str(message).strip())


@dataclass(frozen=True, slots=True)
class SourceProgress:
    phase: str
    current: int
    total: int
    detail: str
    unit: str = "条"

    @property
    def count_text(self) -> str:
        if self.total <= 0:
            return "—"
        return f"{self.current:,} / {self.total:,} {self.unit}（{self.current * 100 // self.total}%）"


class TaskProgressPresentation:
    """One attempt's display state; completion is owned by the task runtime."""

    def __init__(self, tasks):
        self.reset(tasks)

    def reset(self, tasks):
        self._labels = {task.key: str(getattr(task, "label", task.key)) for task in tasks}
        self._keys = set(self._labels)
        self._finished = set()
        self._closure_finished = set()
        self._closure_current = None
        self.source_rows = {}
        self._workflow_started = False
        self.names_observed = False
        self.summary = "等待任务开始"
        self.progress = (0, len(self._keys), "来源流程 %v/%m 已结束")

    def project_names(self, event):
        self.names_observed = True
        label, unit = _NAME_PHASES[event.phase]
        total = max(0, event.total)
        current = min(max(0, event.current), total)
        self.progress = (current, total, f"{label} %v/%m {unit}")
        if total == 0:
            self.progress = (0, 0, label)
        self.summary = event.message if event.phase in {"done", "cancelled"} else f"项目名称首批 · {label}"
        if event.name_count:
            self.summary += f"\n{event.occurrence_count:,} 次出现 · {event.name_count:,} 个独立名称"
            if event.phase not in {"scan", "load", "resolve", "reuse"}:
                self.summary += f" · 已写入草稿 {event.reused_count:,} 个名称（无需模型翻译）"
        for source in event.sources:
            if source.key in self._keys:
                source_total = max(0, source.total)
                self.source_rows[source.key] = SourceProgress(
                    label,
                    min(max(0, source.current), source_total),
                    source_total,
                    progress_detail(source.detail),
                    unit,
                )

    def source_started(self, key):
        if key in self._keys:
            if not self._workflow_started:
                self.source_rows.clear()
                self._workflow_started = True
            self.source_rows[key] = SourceProgress("执行中", 0, 0, "准备来源工作流")
            self._source_summary()

    def source_progress(self, key, stage, current, total, message):
        if key not in self._keys:
            return
        total = max(0, total)
        unit = "步" if stage in {"commit_translation", "finalize"} else "条"
        self.source_rows[key] = SourceProgress(
            PHASE_LABELS.get(stage, stage), min(max(0, current), total), total, progress_detail(message), unit
        )
        if stage == "term_closure" or stage.startswith("term_closure_"):
            self._closure_current = key
            if stage == "term_closure_done":
                self._closure_finished.add(key)
            else:
                self._closure_finished.discard(key)
        self._source_summary()

    def source_finished(self, key):
        if key in self._keys:
            self._finished.add(key)
            self.source_rows[key] = SourceProgress("等待项目收尾", 0, 0, "来源流程已结束，等待参考差异评估")
            self._source_summary()

    def finish(self):
        self.source_rows.clear()
        self.summary = ""

    def _source_summary(self):
        if self._closure_current is not None:
            self._closure_summary()
            return
        self.progress = (len(self._finished), len(self._keys), "来源流程 %v/%m 已结束")
        phases = Counter(row.phase for key, row in self.source_rows.items() if key not in self._finished)
        self.summary = "各插件独立处理"
        if phases:
            self.summary += " · " + " · ".join(f"{phase} {count}" for phase, count in phases.items())
        if len(self._finished) == len(self._keys):
            self.summary = "来源流程已结束 · 正在准备项目收尾"
            self.progress = (0, 0, "正在准备项目收尾")

    def _closure_summary(self):
        key = self._closure_current
        row = self.source_rows[key]
        completed, total = len(self._closure_finished), len(self._keys)
        self.summary = f"项目收尾 · 已检查 {completed}/{total} 个来源"
        if completed == total:
            self.summary += " · 正在整理任务结果"
            self.progress = (0, 0, "正在整理任务结果")
        elif key in self._closure_finished:
            self.summary += " · 准备检查下一来源"
            self.progress = (completed, total, "项目收尾 %v/%m 个来源已检查")
        else:
            self.summary += f" · {self._labels[key]} · {row.phase}"
            self.progress = (row.current, row.total, f"{row.phase} %v/%m {row.unit}" if row.total else row.phase)


__all__ = ["SourceProgress", "TaskProgressPresentation", "progress_detail"]
