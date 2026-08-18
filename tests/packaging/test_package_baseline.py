from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tomllib

import pytest

import transbridge
from transbridge import dependency_capabilities
from transbridge.application.capabilities import CapabilityState

ROOT = Path(__file__).resolve().parents[2]


def _load_toml(path: Path) -> dict:
    with path.open("rb") as stream:
        return tomllib.load(stream)


def test_source_import_is_headless_and_uses_project_version() -> None:
    project = _load_toml(ROOT / "pyproject.toml")["project"]
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import transbridge; "
                "assert 'PyQt6' not in sys.modules; "
                "assert 'transbridge.ui.app' not in sys.modules; "
                "print(transbridge.__version__)"
            ),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == project["version"]
    assert transbridge.__version__ == project["version"]


@pytest.mark.parametrize(
    ("module", "program"),
    (("transbridge.cli", "transbridge"), ("transbridge.entrypoints.mcp", "transbridge-mcp")),
)
def test_help_is_headless(module: str, program: str) -> None:
    completed = subprocess.run(
        [sys.executable, "-m", module, "--help"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.startswith(f"usage: {program}")
    assert "PyQt6" not in completed.stderr


def test_declared_locked_and_bundled_dependency_baseline() -> None:
    project = _load_toml(ROOT / "pyproject.toml")
    lock = _load_toml(ROOT / "uv.lock")
    spec = (ROOT / "transbridge.spec").read_text(encoding="utf-8")
    declared = {value.split(">=", 1)[0].split("==", 1)[0].lower() for value in project["project"]["dependencies"]}
    locked = {package["name"].lower() for package in lock["package"]}

    for capability in dependency_capabilities.DEPENDENCY_BASELINE:
        assert capability.distribution in declared
        assert capability.distribution in locked
        assert f'"{capability.import_name}"' in spec


def test_missing_optional_dependency_reports_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    capability = dependency_capabilities.DEPENDENCY_BASELINE[0]
    monkeypatch.setattr(dependency_capabilities, "find_spec", lambda _name: None)

    result = dependency_capabilities.probe_dependency(capability)

    assert result.state is CapabilityState.UNAVAILABLE
    assert capability.distribution in result.missing_prerequisites
    assert result.to_dict()["state"] == "unavailable"


def test_production_imports_do_not_depend_on_repository_src_namespace() -> None:
    legacy_namespace = "src" + ".transbridge"
    offenders = []
    for path in (ROOT / "src" / "transbridge").rglob("*.py"):
        if legacy_namespace in path.read_text(encoding="utf-8"):
            offenders.append(path.relative_to(ROOT).as_posix())

    assert offenders == []


def test_build_version_is_supplied_from_pyproject() -> None:
    installer = (ROOT / "installer" / "setup.iss").read_text(encoding="utf-8")
    build = (ROOT / "build.bat").read_text(encoding="utf-8")
    spec = (ROOT / "transbridge.spec").read_text(encoding="utf-8")

    assert '#define AppVersion "' not in installer
    assert "pyproject.toml" in build
    assert "/DAppVersion=%APP_VERSION%" in build
    assert 'copy_metadata("transbridge")' in spec
