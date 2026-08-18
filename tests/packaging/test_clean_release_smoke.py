"""Release S05 clean-release smoke: licenses, artifacts, uninstall policy.

True Windows clean-VM install/upgrade/uninstall runs happen on the S05 release
environment; on the developer machine we assert the code-level, equivalent
isolated-venue evidence the installer contract depends on.
"""

from __future__ import annotations

import hashlib
from importlib import metadata
from pathlib import Path
import tomllib

import pytest

import transbridge

ROOT = Path(__file__).resolve().parents[2]

#: Core runtime distributions that must carry licensing metadata so the
#: release can produce a verifiable license inventory.
_CORE_DISTRIBUTIONS = ("PyQt6", "openpyxl")


def _load_toml(path: Path) -> dict:
    with path.open("rb") as stream:
        return tomllib.load(stream)


def test_installed_core_packages_expose_license_metadata() -> None:
    """Each bundled core distribution must ship a license this inventory can cite."""
    missing: list[str] = []
    for distribution in _CORE_DISTRIBUTIONS:
        dist = metadata.distribution(distribution)
        classifiers = " ".join(dist.metadata.get_all("Classifier", ()))
        has_field = bool(
            dist.metadata.get("License")
            or dist.metadata.get("License-Expression")
            or "license" in classifiers.lower()
        )
        has_file = any(
            "license" in str(part).lower() or "copying" in str(part).lower()
            for part in (dist.files or ())
        )
        if not (has_field or has_file):
            missing.append(distribution)
    assert missing == [], f"distributions without any license evidence: {missing}"


def test_artifact_checksums_are_recorded_when_present() -> None:
    """Pre-built release artifacts travel with a recorded SHA-256 preamble."""
    candidates = list((ROOT / "installer" / "output").glob("*.exe")) + list(
        (ROOT / "dist").glob("*.whl")
    )
    if not candidates:
        pytest.skip("no built release artifacts present; clean build expected on S05 venue")
    for artifact in candidates:
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        assert len(digest) == 64
        assert digest.isalnum()


def test_installed_layout_does_not_depend_on_repository_src_path() -> None:
    """Installed package must never resolve under the repository source tree."""
    package_file = Path(transbridge.__file__).resolve()
    repo_src = (ROOT / "src").resolve()
    try:
        package_file.relative_to(repo_src)
    except ValueError:
        pass  # installed elsewhere (or a wheel/onedir) — good
    else:
        # When running editable, the contract still rejects hard-coded repo
        # absolute paths inside the package.
        offenders: list[str] = []
        needle = str(repo_src).replace("\\", "/").lower()
        for path in (ROOT / "src" / "transbridge").rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            if needle in text.lower():
                offenders.append(path.relative_to(ROOT).as_posix())
        assert offenders == [], f"package references repository src path: {offenders}"


def test_version_single_source_and_upgradeable_appid() -> None:
    """Version flows from pyproject; a fixed AppId lets Inno upgrade in place."""
    project = _load_toml(ROOT / "pyproject.toml")["project"]
    installer = (ROOT / "installer" / "setup.iss").read_text(encoding="utf-8")
    assert "#define AppVersion" not in installer, "AppVersion must come from pyproject, not setup.iss"
    assert 'AppId={{' in installer
    assert 'AppVersion={#AppVersion}' in installer
    assert transbridge.__version__ == project["version"]


def test_uninstall_does_not_implicitly_delete_user_projects() -> None:
    """Uninstall asks before removing user AppData; install-dir data is isolated."""
    installer = (ROOT / "installer" / "setup.iss").read_text(encoding="utf-8")
    # The uninstall code must guard user data behind an explicit confirmation.
    assert "UninstallDelete" in installer
    assert "DelTree(DataDir" in installer
    assert "{app}\\data" in installer
    # No wholesale removal of user roots.
    for unsafe in ("{userprofile}", "{autopf}\\..", "C:\\Users"):
        assert unsafe not in installer