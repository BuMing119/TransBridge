# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""配置与数据目录路径工具。"""

from base64 import urlsafe_b64encode
import os
from pathlib import Path
import sys

from transbridge.filesystem_paths import extended_path

from .storage_location import active_storage_root, default_data_path, default_progress_path


def translation_progress_root() -> Path:
    root = active_storage_root()
    return extended_path(root / "翻译进度") if root is not None else default_progress_path()


def task_run_directory(root: Path, run_id: str) -> Path:
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("Task run ID must be a non-empty string")
    segment = urlsafe_b64encode(run_id.encode()).decode("ascii").rstrip("=")
    return root / f"run-{segment}"


def get_data_dir() -> str:
    """Return the current user data directory, creating only the default on first use."""
    root = active_storage_root()
    if root is not None:
        return str(extended_path(root / "data"))
    data_dir = default_data_path()
    data_dir.mkdir(parents=True, exist_ok=True)
    return str(data_dir)


def get_data_resource_dir(name: str) -> str:
    """Resolve a versioned data resource with a frozen-bundle fallback.

    Development checkouts keep editable defaults below ``data/``. Frozen
    builds keep user state below ``%APPDATA%`` but ship immutable defaults in
    the PyInstaller bundle. A user-provided directory takes precedence.
    """

    relative_name = str(name or "").strip()
    if not relative_name or os.path.isabs(relative_name) or relative_name in {".", ".."}:
        raise ValueError("data resource name must be a non-empty relative directory name")
    if any(part in {"", ".", ".."} for part in relative_name.replace("\\", "/").split("/")):
        raise ValueError("data resource name must not contain path traversal")

    user_path = os.path.join(get_data_dir(), relative_name)
    if os.path.isdir(user_path):
        return user_path

    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root is not None:
        bundled_path = os.path.join(str(bundle_root), "data", relative_name)
        if os.path.isdir(bundled_path):
            return bundled_path

    return user_path


def get_config_file_path() -> str:
    """Return the single versioned application INI path."""
    return os.path.join(get_data_dir(), "transbridge.ini")


def get_legacy_config_file_path() -> str:
    """Return the read-only V1 migration source path."""
    return os.path.join(get_data_dir(), "paratranz_config.ini")
