# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Locate user-selected storage before opening project databases or preferences."""

import json
import os
from pathlib import Path
import sys
from tempfile import mkstemp

from transbridge.filesystem_paths import extended_path


class StorageLocationError(RuntimeError):
    """The application cannot safely open its configured storage."""


def location_directory() -> Path:
    local = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
    return Path(local) / "TransBridge"


def default_progress_path() -> Path:
    return location_directory() / "翻译进度"


def default_data_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(os.environ.get("APPDATA") or Path.home()) / "TransBridge" / "data"
    base = Path(__file__).resolve().parent
    while not (base / "src").exists() and base != base.parent:
        base = base.parent
    return base / "data"


def selected_storage_root(path: str | Path) -> Path:
    selected = Path(path).absolute()
    return selected if selected.name.casefold() == "transbridge" else selected / "TransBridge"


def active_storage_root() -> Path | None:
    record = location_directory() / "storage-location.json"
    try:
        payload = record.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError) as exc:
        raise StorageLocationError(f"无法读取数据位置记录 {record}：{exc}") from exc
    try:
        root = Path(json.loads(payload)["root"])
        if not root.is_absolute():
            raise ValueError("需要完整路径")
    except (KeyError, TypeError, ValueError) as exc:
        raise StorageLocationError(f"数据位置记录损坏，请检查 {record}。") from exc
    _require_storage(root)
    return root


def _require_storage(root: Path) -> None:
    if not extended_path(root / "data").is_dir() or not extended_path(root / "翻译进度").is_dir():
        raise StorageLocationError(f"找不到完整的数据文件夹 {root}。请重新连接磁盘或恢复该文件夹，再试一次。")


def commit_storage_root(root: Path) -> None:
    _require_storage(root)
    record = location_directory() / "storage-location.json"
    record.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = mkstemp(dir=record.parent)
    temp = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"root": str(root.absolute())}, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(record)
    finally:
        temp.unlink(missing_ok=True)
