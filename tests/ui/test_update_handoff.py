"""The installer helper waits for the old process and verifies the executable."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from transbridge.ui.shell import update_handoff


def test_handoff_waits_and_rechecks_before_elevating(monkeypatch, tmp_path: Path) -> None:
    installer = tmp_path / "O'Brien Setup.exe"
    calls = []
    monkeypatch.setattr(update_handoff.subprocess, "Popen", lambda *args, **kwargs: calls.append((args, kwargs)))
    digest = "a" * 64

    update_handoff.launch_after_exit(installer, sha256=digest, previous_pid=1234)

    args, options = calls[0]
    command = args[0]
    assert command[:5] == ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden"]
    assert command[5] == "-EncodedCommand"
    script = base64.b64decode(command[6]).decode("utf-16le")
    assert "Get-Process -Id 1234" in script
    assert script.index("Get-Process -Id 1234") < script.index("Start-Process")
    assert "Get-FileHash -LiteralPath $installer -Algorithm SHA256" in script
    assert script.index("Get-FileHash") < script.index("Start-Process")
    assert "O''Brien Setup.exe" in script
    assert digest.upper() in script
    assert "-Verb RunAs" in script
    assert "last-update-error.txt" in script
    assert options["creationflags"] == update_handoff.subprocess.CREATE_NO_WINDOW


@pytest.mark.parametrize("digest,pid", [("bad", 1234), ("a" * 64, 0)])
def test_handoff_rejects_invalid_arguments(tmp_path: Path, digest: str, pid: int) -> None:
    with pytest.raises(ValueError):
        update_handoff.launch_after_exit(tmp_path / "Setup.exe", sha256=digest, previous_pid=pid)
