# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Install an update after exit and reopen the application as the original user."""

from __future__ import annotations

import base64
import os
from pathlib import Path
import subprocess
import sys


def launch_after_exit(installer: Path, *, sha256: str, previous_pid: int, application: Path | None = None) -> None:
    """Retain the caller's credentials; only Setup requests elevation."""

    script = _handoff_script(installer, sha256=sha256, previous_pid=previous_pid, application=application)
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-EncodedCommand", encoded],
        close_fds=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
        cwd=os.environ.get("LOCALAPPDATA") or str(Path.home()),
    )


def _handoff_script(installer: Path, *, sha256: str, previous_pid: int, application: Path | None = None) -> str:
    if len(sha256) != 64 or any(character not in "0123456789abcdefABCDEF" for character in sha256):
        raise ValueError("Invalid installer digest")
    if previous_pid <= 0:
        raise ValueError("Invalid application process ID")
    installer = installer.resolve()
    application = (application or Path(sys.executable)).resolve()
    log = installer.parent / "install.log"
    # Quote for Windows command-line parsing first, then for PowerShell literals.
    arguments = subprocess.list2cmdline([
        "/SP-",
        "/SILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/RESTARTEXITCODE=3010",
        "/NOCLOSEAPPLICATIONS",
        "/NORESTARTAPPLICATIONS",
        f"/DIR={application.parent}",
        f"/LOG={log}",
    ])

    def literal(value: object) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    return f"""
$ErrorActionPreference = 'Stop'
$installer = {literal(installer)}
$application = {literal(application)}
$errorFile = {literal(installer.parent / "last-update-error.txt")}
$logFile = {literal(log)}
try {{
    for ($i = 0; $i -lt 240; $i++) {{
        if (-not (Get-Process -Id {previous_pid} -ErrorAction SilentlyContinue)) {{ break }}
        Start-Sleep -Milliseconds 500
    }}
    if (Get-Process -Id {previous_pid} -ErrorAction SilentlyContinue) {{ throw '等待旧版退出超时' }}
    if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) {{ throw '安装包已移走' }}
    if ((Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash -ne '{sha256.upper()}') {{
        throw '安装包校验失败'
    }}
    # /SILENT skips the installer's postinstall launch. The original user helper
    # owns restart, so the application does not inherit Setup's elevated token.
    $setup = Start-Process -FilePath $installer -ArgumentList {literal(arguments)} -Verb RunAs -Wait -PassThru
    if ($setup.ExitCode -eq 3010) {{
        throw '更新已安装，但需要重启 Windows 才能生效。请保存其他工作后手动重启电脑。'
    }}
    if ($null -eq $setup.ExitCode -or $setup.ExitCode -ne 0) {{
        throw "安装未完成（退出码：$($setup.ExitCode)）。请查看安装日志或手动重新运行安装包。"
    }}
    if (-not (Test-Path -LiteralPath $application -PathType Leaf)) {{ throw '安装结束后未找到 TransBridge 程序' }}
    if (Test-Path -LiteralPath $errorFile -PathType Leaf) {{ Remove-Item -LiteralPath $errorFile }}
    try {{
        Start-Process -FilePath $application -WorkingDirectory {literal(application.parent)}
    }} catch {{
        throw "更新已安装，但无法重新打开 TransBridge：$($_.Exception.Message)。请手动启动软件。"
    }}
}} catch {{
    $message = "$($_.Exception.Message)`n`n安装包：$installer`n安装日志：$logFile"
    try {{
        [System.IO.File]::WriteAllText($errorFile, $message, [System.Text.Encoding]::UTF8)
    }} catch {{
        $message += "`n无法保存错误记录：$($_.Exception.Message)"
    }}
    Add-Type -AssemblyName PresentationFramework
    [void][System.Windows.MessageBox]::Show($message, 'TransBridge 更新未完成', 'OK', 'Error')
}}
"""
