# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Native filesystem paths and SQLite filenames, independent of application layers."""

import os
from pathlib import Path
from typing import Literal
from urllib.parse import quote


def extended_path(path: str | os.PathLike[str], *, force: bool = False) -> Path:
    """Use Windows extended syntax without resolving symlinks or junctions.

    Force it for a traversal root when its children may exceed MAX_PATH.
    """
    resolved = os.path.abspath(path)
    if os.name == "nt" and not resolved.startswith("\\\\?\\") and (force or len(resolved) >= 240):
        resolved = "\\\\?\\UNC\\" + resolved[2:] if resolved.startswith("\\\\") else "\\\\?\\" + resolved
    return Path(resolved)


def native_path(path: str | os.PathLike[str]) -> Path:
    """Resolve a local path and opt long Windows filenames into extended syntax."""
    return Path(os.path.realpath(extended_path(path)))


def sqlite_uri(path: str | os.PathLike[str], *, mode: Literal["ro", "rw", "rwc"] = "rwc") -> str:
    """Encode the native filename without treating Windows' '?' as an authority."""
    if mode not in {"ro", "rw", "rwc"}:
        raise ValueError("unsupported SQLite file mode")
    return "file:" + quote(str(native_path(path)), safe="/:") + "?mode=" + mode
