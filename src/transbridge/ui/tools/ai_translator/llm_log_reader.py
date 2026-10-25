"""Filesystem-only snapshots for the live LLM log viewer."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os

from .bounded_read_text import bounded_display_text


@dataclass(frozen=True)
class LogReadRequest:
    directory: str
    selected: str
    active_path: str
    active_stamp: tuple[int, int] | None
    following: bool
    force: bool
    scan: bool
    read_text: Callable[[str], str]


@dataclass(frozen=True)
class LogSnapshot:
    files: tuple[str, ...] | None = None
    path: str = ""
    stamp: tuple[int, int] | None = None
    content: str | None = None
    message: str = ""
    empty: bool = False


def read_log_snapshot(request: LogReadRequest) -> LogSnapshot:
    files = None
    path = request.selected
    if request.scan:
        try:
            with os.scandir(request.directory) as directory:
                files = tuple(sorted(item.name for item in directory if item.name.endswith(".log") and item.is_file()))
        except FileNotFoundError:
            return LogSnapshot(message="日志目录不存在", empty=True)
        except OSError as exc:
            return LogSnapshot(message=f"无法读取日志目录：{exc}", empty=True)
        if not files:
            return LogSnapshot(files=files, message="暂无日志，正在等待任务写入…", empty=True)
        if os.path.basename(path) not in files:
            selected = "workflow.log" if "workflow.log" in files else files[-1]
            path = os.path.join(request.directory, selected)
    if not path:
        return LogSnapshot(files=files)
    try:
        stat = os.stat(path)
        stamp = (stat.st_mtime_ns, stat.st_size)
        if path == request.active_path and not request.force:
            if stamp == request.active_stamp:
                return LogSnapshot(files=files, path=path, stamp=stamp)
            if not request.following:
                return LogSnapshot(
                    files=files, path=path, message="有新日志 · 正在保留阅读位置，滚动到底部或点击刷新以更新"
                )
        content = bounded_display_text(
            request.read_text(path), tail=True, full_text_hint="完整内容保留在原始日志文件中。"
        )
        return LogSnapshot(files=files, path=path, stamp=stamp, content=content)
    except OSError as exc:
        return LogSnapshot(files=files, path=path, message=f"无法读取日志：{exc}")


def read_log_tail(path: str, limit: int) -> str:
    size = os.path.getsize(path)
    start = max(0, size - limit)
    with open(path, "rb") as stream:
        stream.seek(start)
        data = stream.read(limit)
    if start:
        newline = data.find(b"\n")
        if newline >= 0:
            data = data[newline + 1 :]
        prefix = f"[日志过大，仅显示最后 {limit // (1024 * 1024)} MiB]\n\n"
    else:
        prefix = ""
    return prefix + data.decode("utf-8", errors="replace")
