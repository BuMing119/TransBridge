# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Native filesystem paths and SQLite filenames, independent of application layers."""

import os
from pathlib import Path
from typing import Literal
from urllib.parse import quote


def native_path(path: str | os.PathLike[str]) -> Path:
    """Resolve a local path and opt long Windows filenames into extended syntax."""
    resolved = os.path.abspath(path)
    if os.name == "nt" and not resolved.startswith("\\\\?\\") and len(resolved) >= 240:
        resolved = "\\\\?\\UNC\\" + resolved[2:] if resolved.startswith("\\\\") else "\\\\?\\" + resolved
    return Path(os.path.realpath(resolved))


def sqlite_uri(path: str | os.PathLike[str], *, mode: Literal["ro", "rw", "rwc"] = "rwc") -> str:
    """Encode the native filename without treating Windows' '?' as an authority."""
    if mode not in {"ro", "rw", "rwc"}:
        raise ValueError("unsupported SQLite file mode")
    return "file:" + quote(str(native_path(path)), safe="/:") + "?mode=" + mode
