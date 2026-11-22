# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Move application-owned data while the runtime has released its files."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import sys
from tempfile import TemporaryDirectory

from transbridge.filesystem_paths import extended_path, sqlite_uri

from .storage_access import storage_access
from .storage_location import active_storage_root, commit_storage_root, default_data_path, default_progress_path


@dataclass(frozen=True)
class _Entry:
    directory: bool
    size: int = 0
    modified: int = 0


def _reject_links(path: Path) -> None:
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            raise ValueError(f"数据迁移不支持链接或目录联接：{part}")


def _inventory(root: Path) -> dict[Path, _Entry]:
    _reject_links(root)
    items: dict[Path, _Entry] = {}
    if not root.exists():
        return items
    if not root.is_dir():
        raise ValueError(f"原数据位置不是文件夹：{root}")
    directories = [root]
    while directories:
        current = directories.pop()
        for item in current.iterdir():
            metadata = item.lstat()
            if stat.S_ISLNK(metadata.st_mode) or item.is_junction():
                raise ValueError(f"数据中含有链接或目录联接：{item}")
            if stat.S_ISDIR(metadata.st_mode):
                items[item.relative_to(root)] = _Entry(True)
                directories.append(item)
            elif stat.S_ISREG(metadata.st_mode):
                items[item.relative_to(root)] = _Entry(False, metadata.st_size, metadata.st_mtime_ns)
            else:
                raise ValueError(f"数据中含有不支持的文件类型：{item}")
    return items


def _identical(left: Path, right: Path) -> bool:
    with left.open("rb") as source, right.open("rb") as destination:
        while True:
            block = source.read(1024 * 1024)
            if block != destination.read(1024 * 1024):
                return False
            if not block:
                return True


def _copy_and_check(source, target, items, offset, total, report):
    target.mkdir()
    copied = offset
    for relative, item in items.items():
        original, copy = source / relative, target / relative
        if item.directory:
            copy.mkdir()
            continue
        copied += 1
        report(f"正在复制第 {copied}/{total} 个文件：{relative}")
        with original.open("rb") as reader, copy.open("xb") as writer:
            shutil.copyfileobj(reader, writer, length=1024 * 1024)
            writer.flush()
            os.fsync(writer.fileno())
        shutil.copystat(original, copy)
    checked = offset
    for relative, item in items.items():
        if not item.directory:
            checked += 1
            report(f"正在核对第 {checked}/{total} 个文件：{relative}")
            if not _identical(source / relative, target / relative):
                raise OSError(f"文件核对失败，原数据已保留：{relative}")
    return copied


def _clean_verified(source, target, items):
    warnings = []
    for relative in reversed(items):
        original = source / relative
        try:
            _reject_links(original)
            if items[relative].directory:
                original.rmdir()
            elif _identical(original, target / relative):
                original.unlink()
            else:
                warnings.append(f"原文件已有变化，已保留：{original}")
        except OSError as exc:
            warnings.append(f"未能清理原位置 {original}：{exc}")
        except ValueError as exc:
            warnings.append(str(exc))
    if source.is_dir():
        try:
            source.rmdir()
        except OSError as exc:
            warnings.append(f"原文件夹已保留：{source}（{exc}）")
    return warnings


def _overlap(a, b):
    return a.is_relative_to(b) or b.is_relative_to(a)


def _managed_databases(items):
    from transbridge.persistence.project_store.format import PROJECT_FILENAME, PROJECT_ROOT

    for name, entry in items.items():
        if entry.directory:
            continue
        project = len(name.parts) == 3 and name.match(f"{PROJECT_ROOT}/id-*/{PROJECT_FILENAME}")
        if project or name == Path("task-store-v1", "tasks.sqlite3"):
            yield name


def _database_sidecars(database, inventory):
    siblings = [database.with_name(database.name + suffix) for suffix in ("-journal", "-wal", "-shm")]
    return [path for path in siblings if path in inventory]


def _check_databases(data, databases, scratch, report):
    for name, sidecars in databases.items():
        database = data / name
        report(f"正在检查数据库：{name}")
        try:
            with TemporaryDirectory(dir=scratch) as probe:
                if sidecars:
                    # Never open the staged WAL in place: even a read-only connection
                    # may update its shared-memory index. Rebuild that index privately.
                    for path in [name, *sidecars]:
                        if not path.name.endswith("-shm"):
                            shutil.copyfile(data / path, Path(probe) / path.name)
                    uri = sqlite_uri(Path(probe) / name.name, mode="ro")
                else:
                    uri = sqlite_uri(database, mode="ro") + "&immutable=1"
                with closing(sqlite3.connect(uri, uri=True)) as connection:
                    connection.execute("PRAGMA trusted_schema=OFF")
                    if connection.execute("PRAGMA page_count").fetchone() == (0,):
                        raise ValueError("数据库为空")
                    for (result,) in connection.execute("PRAGMA quick_check(1)"):
                        if result != "ok":
                            raise ValueError(result)
        except (OSError, sqlite3.Error, ValueError) as exc:
            raise OSError(f"数据库检查失败，原数据已保留：{name}（{exc}）") from exc


def migrate_storage(destination: Path, progress: Callable[[str], None] | None = None) -> tuple[str, ...]:
    """Copy, verify and switch under the application-wide exclusive lock.

    Returned messages describe cleanup issues after a successful switch.
    """
    with storage_access(exclusive=True):
        return _migrate(Path(destination), progress or (lambda _message: None))


def _migrate(destination, report):
    if not destination.is_absolute():
        raise ValueError("请选择完整的目标文件夹路径。")
    selected = destination
    destination = extended_path(selected, force=True)
    _reject_links(destination)
    old = active_storage_root()
    sources = {
        "data": old / "data" if old else default_data_path(),
        "翻译进度": old / "翻译进度" if old else default_progress_path(),
    }
    sources = {name: extended_path(source, force=True) for name, source in sources.items()}
    pointer_directory = extended_path(default_progress_path().parent, force=True)
    if pointer_directory.is_relative_to(destination):
        raise ValueError("目标位置包含程序记录数据位置的固定目录。")
    if any(_overlap(destination, source) for source in sources.values()) or (
        old and _overlap(destination, extended_path(old, force=True))
    ):
        raise ValueError("目标与当前数据位置重叠，请选择其他文件夹。")
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError("目标文件夹必须为空，现有内容不会被覆盖。")
    inventories = {name: _inventory(path) for name, path in sources.items()}
    required = sum(entry.size for inventory in inventories.values() for entry in inventory.values())
    data = inventories["data"]
    databases = {db: _database_sidecars(db, data) for db in _managed_databases(data)}
    probe_sizes = [data[db].size + sum(data[p].size for p in sides) for db, sides in databases.items() if sides]
    required += max(probe_sizes, default=0)
    total = sum(not entry.directory for inventory in inventories.values() for entry in inventory.values())
    destination.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(destination.parent).free < required:
        raise OSError(f"目标磁盘空间不足，至少需要 {required:,} 字节。")

    existed = destination.exists()
    with TemporaryDirectory(prefix=".transbridge-move-", dir=destination.parent) as temp:
        staging = Path(temp) / "TransBridge"
        staging.mkdir()
        copied = 0
        for name, source in sources.items():
            copied = _copy_and_check(source, staging / name, inventories[name], copied, total, report)
        _check_databases(staging / "data", databases, Path(temp), report)
        for name, source in sources.items():
            if inventories[name] != _inventory(source):
                raise OSError(f"复制期间原数据发生变化，请关闭其他写入程序后重试：{source}")
        if existed:
            destination.rmdir()  # Succeeds only if it is still empty.
        try:
            staging.rename(destination)
            try:
                commit_storage_root(selected)
            except BaseException as error:
                # The published directory is now visible to other programs. Roll back
                # only the verified copies we created, preserving any later additions.
                for name, source in sources.items():
                    for warning in _clean_verified(destination / name, source, inventories[name]):
                        error.add_note(warning)
                if not existed:
                    try:
                        destination.rmdir()
                    except OSError as exc:
                        error.add_note(f"目标文件夹已保留：{destination}（{exc}）")
                raise
        except BaseException:
            if existed:
                destination.mkdir(exist_ok=True)
            raise

    warnings = []
    for name, source in sources.items():
        if name == "data" and old is None and not getattr(sys, "frozen", False):
            continue  # The checkout contains tracked data resources.
        warnings.extend(_clean_verified(source, destination / name, inventories[name]))
    if old:
        try:
            if old.is_dir() and not any(old.iterdir()):
                old.rmdir()
        except OSError as exc:
            warnings.append(f"原文件夹已保留：{old}（{exc}）")
    return tuple(warnings)
