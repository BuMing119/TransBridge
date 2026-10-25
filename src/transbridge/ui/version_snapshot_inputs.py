"""Detached legacy version payloads for background snapshot I/O."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from transbridge.persistence.variant_store import VariantStore


@dataclass(frozen=True)
class LegacySnapshotInput:
    path: Path
    snapshot_dir: Path
    labels: dict
    label_library: dict

    def require_current(self, ctx):
        if ctx.entry_labels != self.labels or ctx.label_library != self.label_library:
            raise RuntimeError("项目标签或标签库在 AI 运行期间已修改，请重新运行任务。")
        if ctx.variant_store is None or ctx.variant_store._path != self.path:
            raise RuntimeError("项目版本保存位置已变化，请重新运行任务。")

    def store(self, entries):
        store = VariantStore(self.path)
        store.collect_from(list(entries), self.labels, self.label_library)
        return store


class LegacySnapshotCapture:
    """Allow the task launcher to copy labels in the same GUI slices as entries."""

    def __init__(self, ctx):
        project, store, variant = ctx.active_project, ctx.variant_store, ctx.active_variant
        if project is None or store is None or variant is None:
            raise RuntimeError("请先打开一个项目版本。")
        self.path = store._path
        self.snapshot_dir = project.variant_dir(variant) / "snapshots"
        self._labels = ctx.entry_labels
        self._library = ctx.label_library
        self.labels, self.library = {}, {}

    def steps(self):
        for key, values in self._labels.items():
            self.labels[key] = set(values)
            yield
        for key, values in self._library.items():
            self.library[key] = deepcopy(values)
            yield

    def finish(self):
        return LegacySnapshotInput(self.path, self.snapshot_dir, self.labels, self.library)


def capture_legacy_snapshot(ctx):
    capture = LegacySnapshotCapture(ctx)
    for _ in capture.steps():
        pass
    return capture.finish()
