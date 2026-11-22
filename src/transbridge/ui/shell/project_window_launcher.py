# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Launch an explicitly selected Project in an isolated GUI process."""

from __future__ import annotations

from collections.abc import Sequence
import sys

from PyQt6.QtCore import QProcess


def project_window_command(
    project_path: str | None = None,
    *,
    executable: str | None = None,
    frozen: bool | None = None,
) -> tuple[str, tuple[str, ...]]:
    """Build the source/install or frozen command without invoking a shell."""

    program = executable or sys.executable
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    arguments = ("gui",) if is_frozen else ("-m", "transbridge.cli", "gui")
    if project_path is not None:
        arguments += ("--open-project", project_path)
    return program, arguments


def launch_project_in_new_window(project_path: str) -> bool:
    """Return whether the operating system accepted the detached process."""

    program, arguments = project_window_command(project_path)
    result = QProcess.startDetached(program, list(arguments))
    if isinstance(result, Sequence):
        return bool(result[0])
    return bool(result)


__all__ = ["launch_project_in_new_window", "project_window_command"]
