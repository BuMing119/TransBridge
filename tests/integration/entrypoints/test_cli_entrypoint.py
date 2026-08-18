"""Headless CLI packaging and serialization contracts."""

from __future__ import annotations

import json
import logging
from pathlib import Path
import subprocess
import sys

from transbridge.entrypoints.mcp import RedactingLogFilter


def test_cli_capability_command_is_headless_and_json_serializable() -> None:
    project_root = Path(__file__).parents[3]
    script = (
        f"import sys; sys.path.insert(0, {str(project_root / 'src')!r}); "
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
