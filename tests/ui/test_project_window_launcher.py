from __future__ import annotations

from PyQt6.QtCore import QProcess

from transbridge.ui.shell.project_window_launcher import (
    launch_project_in_new_window,
    project_window_command,
)


def test_project_window_command_uses_module_entrypoint_outside_frozen_build() -> None:
    assert project_window_command(
        "D:/data/projects/project.json",
        executable="C:/Python/python.exe",
        frozen=False,
    ) == (
        "C:/Python/python.exe",
        ("-m", "transbridge.cli", "gui", "--open-project", "D:/data/projects/project.json"),
    )


def test_project_window_command_uses_cli_arguments_for_frozen_executable() -> None:
    assert project_window_command(
        "D:/data/projects/project.json",
        executable="D:/Apps/TransBridge.exe",
        frozen=True,
    ) == (
        "D:/Apps/TransBridge.exe",
        ("gui", "--open-project", "D:/data/projects/project.json"),
    )


def test_detached_launcher_passes_arguments_without_a_shell(monkeypatch) -> None:
    calls = []

    def start_detached(program, arguments):
        calls.append((program, arguments))
        return True, 42

    monkeypatch.setattr(QProcess, "startDetached", start_detached)

    assert launch_project_in_new_window("D:/Projects/Mod With Spaces/project.json") is True
    assert calls[0][1][-2:] == ["--open-project", "D:/Projects/Mod With Spaces/project.json"]
