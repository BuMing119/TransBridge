"""Opt-in Windows installer tests with private AppIds and disposable payloads.

TRANSBRIDGE_RUN_INSTALLER_TESTS=1 runs actual Inno installers/uninstallers,
without touching the real TransBridge installation or user data.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[2]
APP_ID = "{{A3F2C1D4-5B6E-4F7A-8C9D-0E1F2A3B4C5D}"


def _compiler() -> Path:
    for base in ("ProgramFiles(x86)", "ProgramFiles"):
        candidate = Path(os.environ.get(base, "C:/Program Files")) / "Inno Setup 6/ISCC.exe"
        if candidate.is_file():
            return candidate
    pytest.skip("Inno Setup 6 is not installed")


def _run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        command,
        capture_output=True,
        timeout=90,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if check:
        assert result.returncode == 0, result.stdout.decode(errors="replace") + result.stderr.decode(errors="replace")
    return result


def _compile(tmp_path: Path, app_id: str, target: Path, *, legacy: bool) -> Path:
    name = "legacy" if legacy else "current"
    payload = tmp_path / f"payload-{name}"
    (payload / "_internal").mkdir(parents=True)
    (payload / "TransBridge.exe").write_bytes(name.encode())
    (payload / "_internal" / f"{name}.dll").write_bytes(name.encode())
    script_dir = tmp_path / "installer"
    script_dir.mkdir(exist_ok=True)
    (tmp_path / "LICENSE").write_text("Installer test fixture", encoding="utf-8")
    script = (ROOT / "installer/setup.iss").read_text(encoding="utf-8")
    assert script.count(APP_ID) == 1
    script = script.replace(APP_ID, app_id)
    script = script.replace("PrivilegesRequired=admin", "PrivilegesRequired=lowest")
    script = script.replace("DefaultDirName={autopf}\\{#AppName}", f"DefaultDirName={target}")
    script = script.replace("Compression=lzma2/ultra64", "Compression=none")
    if legacy:
        # Emulate the previous uninstaller's dangerous install-directory data cleanup.
        script = script.split("[Code]", 1)[0] + '[UninstallDelete]\nType: filesandordirs; Name: "{app}\\data"\n'
    path = script_dir / f"{name}.iss"
    path.write_text(script, encoding="utf-8-sig")
    _run([
        str(_compiler()),
        "/DAppVersion=0.2.1.2" if legacy else "/DAppVersion=0.2.1.3",
        f"/DDistDir={payload}",
        f"/O{tmp_path}",
        f"/F{name}",
        str(path),
    ])
    return tmp_path / f"{name}.exe"


@pytest.mark.integration
@pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("TRANSBRIDGE_RUN_INSTALLER_TESTS") != "1",
    reason="opt-in actual Windows installer execution",
)
@pytest.mark.parametrize("upgrade", [False, True], ids=["fresh", "legacy-upgrade"])
def test_install_upgrade_and_uninstall_preserve_data(tmp_path: Path, upgrade: bool) -> None:
    target = tmp_path / "installed"
    assert target.resolve().is_relative_to(tmp_path.resolve())
    app_id = "{{" + str(uuid.uuid4()).upper() + "}"
    switches = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/NOICONS", "/NOCLOSEAPPLICATIONS"]
    current = _compile(tmp_path, app_id, target, legacy=False)
    legacy = _compile(tmp_path, app_id, target, legacy=True) if upgrade else None
    if legacy:
        _run([str(legacy), *switches, f"/DIR={target}"])
    try:
        user_data = target / "data/project.json"
        user_data.parent.mkdir(parents=True, exist_ok=True)
        user_data.write_bytes(b"project-data-must-survive")
        unrelated = target / "my-translation.xml"
        unrelated.write_bytes(b"user-owned-translation")
        external = tmp_path / "external-data"
        external.mkdir()
        external_file = external / "config.ini"
        external_file.write_bytes(b"outside-runtime")
        if upgrade:
            # A locked obsolete DLL must stop cleanup, never silently mix versions.
            stale = target / "_internal/obsolete.dll"
            stale.write_bytes(b"obsolete")
            with stale.open("rb"):
                result = _run([str(current), *switches, f"/LOG={tmp_path / 'locked.log'}"], check=False)
                assert result.returncode != 0
            assert user_data.read_bytes() == b"project-data-must-survive"
            assert (target / "TransBridge.exe").read_bytes() == b"legacy"
            # Directory junction targets must not be traversed by recursive cleanup.
            junction = target / "_internal/external-link"
            _run([
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                f"New-Item -ItemType Junction -Path '{junction}' -Target '{external}' | Out-Null",
            ])
        # No /DIR override: verify that the registered previous directory is reused.
        _run([str(current), *switches, f"/LOG={tmp_path / 'install.log'}"])
        assert (target / "TransBridge.exe").read_bytes() == b"current"
        assert (target / "_internal/current.dll").read_bytes() == b"current"
        assert not (target / "_internal/legacy.dll").exists()
        assert not (target / "_internal/obsolete.dll").exists()
        assert not (target / "_internal/external-link").exists()
        assert user_data.read_bytes() == b"project-data-must-survive"
        assert unrelated.read_bytes() == b"user-owned-translation"
        assert external_file.read_bytes() == b"outside-runtime"
    finally:
        uninstaller = target / "unins000.exe"
        if uninstaller.exists():
            # If an assertion failed before upgrade, do not invoke the unsafe legacy
            # cleanup against sentinel data: install the new uninstaller first.
            _run([str(current), *switches, f"/DIR={target}"])
            _run([str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"])
    assert not (target / "TransBridge.exe").exists()
    assert not (target / "_internal/current.dll").exists()
    assert user_data.read_bytes() == b"project-data-must-survive"
    assert unrelated.read_bytes() == b"user-owned-translation"
    assert external_file.read_bytes() == b"outside-runtime"
