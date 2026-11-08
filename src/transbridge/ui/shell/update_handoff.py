"""Start the interactive Windows installer only after the old process exits."""

from __future__ import annotations

import base64
import os
from pathlib import Path
import subprocess


def launch_after_exit(installer: Path, *, sha256: str, previous_pid: int) -> None:
    """Use an independent Windows process to wait, recheck and elevate Setup."""

    path_literal = str(installer.resolve()).replace("'", "''")
    error_literal = str((installer.parent / "last-update-error.txt").resolve()).replace("'", "''")
    if len(sha256) != 64 or any(character not in "0123456789abcdefABCDEF" for character in sha256):
        raise ValueError("Invalid installer digest")
    if previous_pid <= 0:
        raise ValueError("Invalid application process ID")
    script = f"""
$ErrorActionPreference = 'Stop'
try {{
    for ($i = 0; $i -lt 240; $i++) {{
        if (-not (Get-Process -Id {previous_pid} -ErrorAction SilentlyContinue)) {{ break }}
        Start-Sleep -Milliseconds 500
    }}
    if (Get-Process -Id {previous_pid} -ErrorAction SilentlyContinue) {{ throw '等待旧版退出超时' }}
    $installer = '{path_literal}'
    if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) {{ throw '安装包已移走' }}
    if ((Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash -ne '{sha256.upper()}') {{
        throw '安装包校验失败'
    }}
    Start-Process -FilePath $installer -ArgumentList '/SP-' -Verb RunAs
}} catch {{
    [System.IO.File]::WriteAllText('{error_literal}', $_.Exception.Message, [System.Text.Encoding]::UTF8)
}}
"""
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-EncodedCommand", encoded],
        close_fds=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
        cwd=os.environ.get("LOCALAPPDATA") or str(Path.home()),
    )
