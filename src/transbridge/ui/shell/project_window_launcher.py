"""Launch an explicitly selected Project in an isolated GUI process."""

from __future__ import annotations

from collections.abc import Sequence
import sys

from PyQt6.QtCore import QProcess


def project_window_command(
    project_path: str,
    *,
    executable: str | None = None,
    frozen: bool | None = None,
) -> tuple[str, tuple[str, ...]]:
    """Build the source/install or frozen command without invoking a shell."""

    program = executable or sys.executable
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if is_frozen:
        return program, ("gui", "--open-project", project_path)
    return program, ("-m", "transbridge.cli", "gui", "--open-project", project_path)


def launch_project_in_new_window(project_path: str) -> bool:
    """Return whether the operating system accepted the detached process."""

    program, arguments = project_window_command(project_path)
    result = QProcess.startDetached(program, list(arguments))
    if isinstance(result, Sequence):
        return bool(result[0])
    return bool(result)


__all__ = ["launch_project_in_new_window", "project_window_command"]
