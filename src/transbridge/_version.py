"""Resolve the package version from the project metadata authority."""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import tomllib


def _source_tree_version() -> str | None:
    """Read ``pyproject.toml`` only when running from an uninstalled checkout."""
    for parent in Path(__file__).resolve().parents:
        pyproject = parent / "pyproject.toml"
        if not pyproject.is_file():
            continue
        with pyproject.open("rb") as stream:
            project = tomllib.load(stream).get("project", {})
        value = project.get("version")
        return str(value) if value else None
    return None


try:
    __version__ = version("transbridge")
except PackageNotFoundError:
    __version__ = _source_tree_version() or "0+unknown"
