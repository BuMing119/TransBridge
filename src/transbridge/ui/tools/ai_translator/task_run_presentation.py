"""Plain-language execution events and source labels for task views."""

from __future__ import annotations

from .task_activity import concise_event as concise_event


def source_label(tasks, key) -> str:
    label = next((str(task.label) for task in tasks if task.key == key), "处理来源")
    # A source may be named after a file, but progress never needs its directory.
    return label.replace("\\", "/").rsplit("/", 1)[-1].replace("\n", " ")[:100] or "处理来源"
