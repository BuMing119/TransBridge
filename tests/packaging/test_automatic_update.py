"""Real install/restart smoke test using a private installer and disposable app."""

from __future__ import annotations

import base64
from hashlib import sha256
import os
from pathlib import Path
import sys
import uuid

import pytest

from tests.packaging.test_installer_upgrade import _compile, _run
from transbridge.ui.shell.update_handoff import _handoff_script


@pytest.mark.integration
@pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("TRANSBRIDGE_RUN_INSTALLER_TESTS") != "1",
    reason="opt-in actual Windows installer execution",
)
def test_automatic_update_installs_and_restarts_once(tmp_path: Path):
    compiler = Path(os.environ["SystemRoot"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    if not compiler.is_file():
        pytest.skip(".NET Framework compiler unavailable")
    source = tmp_path / "app.cs"
    source.write_text(
        "class App { static void Main() { System.IO.File.AppendAllText(System.IO.Path.Combine("
        'System.AppDomain.CurrentDomain.BaseDirectory, "launched.txt"), "updated\\n"); } }',
        encoding="utf-8",
    )
    stub = tmp_path / "app.exe"
    _run([str(compiler), "/nologo", "/target:winexe", f"/out:{stub}", str(source)])
    target = tmp_path / "O'Brien 中文 $dollar `tick"
    assert target.resolve().is_relative_to(tmp_path.resolve())
    app_id = "{{" + str(uuid.uuid4()).upper() + "}"
    installer = _compile(tmp_path, app_id, target, legacy=False, application=stub)
    target.mkdir()
    (target / "TransBridge.exe").write_bytes(b"previous application")
    (target / "_internal").mkdir()
    (target / "_internal/obsolete.dll").write_bytes(b"previous runtime")
    user_data = target / "translations.json"
    user_data.write_bytes(b"user data must survive")
    # Exercise actual Setup and app processes. The private test installer requires
    # no elevation; suppress its progress window and wait for the short-lived stub.
    # Module preloading prevents autoload from replacing the process proxy.
    prefix = """
Import-Module Microsoft.PowerShell.Management
Import-Module Microsoft.PowerShell.Utility
function Start-Process {
    [CmdletBinding()]
    param($FilePath, $ArgumentList, $Verb, [switch]$Wait, [switch]$PassThru, $WorkingDirectory)
    [void]$PSBoundParameters.Remove('Verb')
    $PSBoundParameters['WindowStyle'] = 'Hidden'
    $PSBoundParameters['Wait'] = $true
    Microsoft.PowerShell.Management\\Start-Process @PSBoundParameters
}
"""
    script = _handoff_script(
        installer,
        sha256=sha256(installer.read_bytes()).hexdigest(),
        previous_pid=2147483647,
        application=target / "TransBridge.exe",
    )
    # Convert error presentation to a test failure instead of leaving a modal UI.
    popup = "[void][System.Windows.MessageBox]::Show($message, 'TransBridge 更新未完成', 'OK', 'Error')"
    assert script.count(popup) == 1
    script = prefix + script.replace(popup, "throw $message")
    try:
        _run([
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-WindowStyle",
            "Hidden",
            "-EncodedCommand",
            base64.b64encode(script.encode("utf-16le")).decode("ascii"),
        ])
        assert (target / "TransBridge.exe").read_bytes() == stub.read_bytes()
        assert not (target / "_internal/obsolete.dll").exists()
        assert (target / "_internal/current.dll").is_file()
        assert (target / "launched.txt").read_text(encoding="utf-8") == "updated\n"
        assert user_data.read_bytes() == b"user data must survive"
        assert not (tmp_path / "last-update-error.txt").exists()
        assert (tmp_path / "install.log").is_file()
    finally:
        uninstaller = target / "unins000.exe"
        if uninstaller.exists():
            _run([str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"])
