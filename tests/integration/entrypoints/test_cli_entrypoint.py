"""Headless CLI packaging and serialization contracts."""

from __future__ import annotations

import json
import logging
from pathlib import Path
import subprocess
import sys

import pytest

from transbridge.entrypoints.mcp import RedactingLogFilter


def test_gui_cli_forwards_explicit_project_path(monkeypatch) -> None:
    from transbridge import cli
    from transbridge.entrypoints import gui

    received = []
    monkeypatch.setattr(gui, "main", lambda *, initial_project_path=None: received.append(initial_project_path) or 0)

    assert cli.main(["gui", "--open-project", "D:/data/projects/project.json"]) == 0
    assert received == ["D:/data/projects/project.json"]


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["gui", "--import-project", "D:/backup/project.transbridge"], "D:/backup/project.transbridge"),
        (["D:/backup/project.transbridge"], "D:/backup/project.transbridge"),
    ],
)
def test_gui_cli_routes_project_archives_to_the_import_flow(monkeypatch, argv, expected) -> None:
    from transbridge import cli
    from transbridge.entrypoints import gui

    received = []
    monkeypatch.setattr(
        gui,
        "main",
        lambda *, initial_project_path=None, initial_import_path=None: (
            received.append((initial_project_path, initial_import_path)) or 0
        ),
    )

    assert cli.main(argv) == 0
    assert received == [(None, expected)]


@pytest.mark.parametrize("stdout_encoding", ["ascii", "gbk", "utf-8"])
def test_cli_capability_command_is_headless_and_json_serializable(stdout_encoding: str) -> None:
    project_root = Path(__file__).parents[3]
    script = (
        f"import sys; sys.path.insert(0, {str(project_root / 'src')!r}); "
        f"sys.stdout.reconfigure(encoding={stdout_encoding!r}); "
        "from transbridge.cli import main; "
        "code = main(['capabilities']); "
        "assert not any(name == 'PyQt6' or name.startswith('PyQt6.') for name in sys.modules); "
        "raise SystemExit(code)"
    )

    completed = subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=project_root,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["outcome"] == "completed"
    assert result["value"]["capabilities"]


def test_mcp_stderr_log_filter_redacts_secret_canary() -> None:
    record = logging.LogRecord(
        "mcp-test",
        logging.ERROR,
        __file__,
        1,
        "adapter failed with api_key=supersecretvalue",
        (),
        None,
    )

    assert RedactingLogFilter().filter(record) is True
    assert record.getMessage() == "adapter failed with ***REDACTED***"
