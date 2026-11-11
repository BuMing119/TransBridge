"""Execute the shipped PowerShell control flow with isolated process/UI boundaries."""

from __future__ import annotations

import base64
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

import pytest

from transbridge.ui.shell.update_handoff import _handoff_script

pytestmark = [pytest.mark.integration, pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell helper")]

# Keep actual file existence checks, SHA-256, error writes and PowerShell parsing.
# Replace process launching and message boxes so no installer or UAC is invoked.
_BOUNDARIES = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Import-Module Microsoft.PowerShell.Management
Import-Module Microsoft.PowerShell.Utility
$ProgressPreference = 'SilentlyContinue'
Add-Type -TypeDefinition @'
namespace System.Windows {
    public static class MessageBox {
        public static void Show(string message, string title, string buttons, string icon) {
            System.Console.WriteLine("REPORT:" + message);
        }
    }
}
'@
function Add-Type { param($AssemblyName) }
function Get-Process {
    [CmdletBinding()] param($Id)
    if ($scenario -eq 'timeout') { [PSCustomObject]@{Id=$Id} }
}
function Start-Sleep { param($Milliseconds) }
function Start-Process {
    param($FilePath, $ArgumentList, $Verb, [switch]$Wait, [switch]$PassThru, $WorkingDirectory)
    Write-Host ('EVENT:' + (@{
        path=$FilePath; arguments=$ArgumentList; verb=$Verb;
        wait=[bool]$Wait; passThru=[bool]$PassThru; cwd=$WorkingDirectory
    } | ConvertTo-Json -Compress))
    if ($Verb) {
        if ($scenario -eq 'uac') { throw 'UAC cancelled' }
        $code = 0
        if ($scenario -eq 'install_error') { $code = 2 }
        if ($scenario -eq 'reboot') { $code = 3010 }
        if ($scenario -eq 'unknown') { $code = $null }
        return [PSCustomObject]@{ExitCode=$code}
    }
    if ($scenario -eq 'restart_error') { throw 'Application launch failed' }
}
"""


@pytest.mark.parametrize(
    "scenario,expected_error,launches",
    [
        ("success", None, 2),
        ("timeout", "等待旧版退出超时", 0),
        ("missing_installer", "安装包已移走", 0),
        ("tampered", "安装包校验失败", 0),
        ("uac", "UAC cancelled", 1),
        ("install_error", "退出码：2", 1),
        ("unknown", "安装未完成", 1),
        ("reboot", "需要重启 Windows", 1),
        ("missing_application", "未找到 TransBridge", 1),
        ("restart_error", "无法重新打开 TransBridge", 2),
        ("unwritable_error", "安装包已移走", 0),
    ],
)
def test_install_and_restart_control_flow(tmp_path: Path, scenario, expected_error, launches):
    directory = tmp_path / "O'Brien 中文 $dollar `tick space"
    directory.mkdir()
    installer = directory / "Setup.exe"
    application = directory / "app" / "TransBridge.exe"
    application.parent.mkdir()
    if scenario not in {"missing_installer", "unwritable_error"}:
        installer.write_bytes(b"tampered" if scenario == "tampered" else b"installer")
    if scenario != "missing_application":
        application.write_bytes(b"application")
    error_file = directory / "last-update-error.txt"
    if scenario == "unwritable_error":
        error_file.mkdir()
    else:
        error_file.write_text("previous error", encoding="utf-8")
    script = (
        f"$scenario = '{scenario}'\n"
        + _BOUNDARIES
        + _handoff_script(
            installer, sha256=sha256(b"installer").hexdigest(), previous_pid=1234, application=application
        )
    )
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-WindowStyle",
            "Hidden",
            "-EncodedCommand",
            base64.b64encode(script.encode("utf-16le")).decode("ascii"),
        ],
        capture_output=True,
        encoding="utf-8-sig",
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    events = [
        json.loads(line.removeprefix("EVENT:")) for line in result.stdout.splitlines() if line.startswith("EVENT:")
    ]
    assert len(events) == launches, result.stdout + result.stderr
    if launches:
        setup = events[0]
        assert setup["path"] == str(installer)
        assert setup["verb"] == "RunAs" and setup["wait"] and setup["passThru"]
        assert setup["arguments"] == subprocess.list2cmdline([
            "/SP-",
            "/SILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/RESTARTEXITCODE=3010",
            "/NOCLOSEAPPLICATIONS",
            "/NORESTARTAPPLICATIONS",
            f"/DIR={application.parent}",
            f"/LOG={directory / 'install.log'}",
        ])
    if launches == 2:
        restart = events[1]
        assert restart["path"] == str(application)
        assert restart["cwd"] == str(application.parent)
        assert restart["verb"] is None and not restart["wait"]
    if expected_error is None:
        assert not error_file.exists()
        assert "REPORT:" not in result.stdout
    else:
        assert "REPORT:" in result.stdout and expected_error in result.stdout
        assert str(directory / "install.log") in result.stdout
        if scenario == "unwritable_error":
            assert "无法保存错误记录" in result.stdout
        else:
            assert expected_error in error_file.read_text(encoding="utf-8-sig")
