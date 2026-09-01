"""Safe ZIP members and portable source/owned-asset capture for project archives."""

from __future__ import annotations

from contextlib import closing
import hashlib
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import sqlite3
import stat
import tempfile
import zipfile

MAX_ARCHIVE_BYTES = 2 * 1024**3


def validate_archive(archive: zipfile.ZipFile) -> None:
    seen = set()
    total = 0
    for info in archive.infolist():
        if "\\" in info.orig_filename or "\0" in info.orig_filename:
            raise ValueError("项目包包含非法的原始文件名")
        name = info.filename.rstrip("/")
        path = PurePosixPath(name)
        if (
            not name
            or "\\" in name
            or path.is_absolute()
            or PureWindowsPath(name).drive
            or any(part in {"", ".", ".."} or ":" in part or part.endswith((".", " ")) for part in name.split("/"))
            or any(PureWindowsPath(part).is_reserved() for part in path.parts)
            or stat.S_ISLNK(info.external_attr >> 16)
        ):
            raise ValueError(f"项目包包含非法路径或符号链接: {info.filename}")
        normalized = name.casefold()
        if normalized in seen:
            raise ValueError(f"项目包包含重复路径: {name}")
        seen.add(normalized)
        total += info.file_size
        if total > MAX_ARCHIVE_BYTES:
            raise ValueError("项目包解压后超过 2 GiB 安全限制")


def capture_sources(archive: zipfile.ZipFile, sources: list[dict]) -> list[dict]:
    locations = []
    for index, source in enumerate(sources):
        path = Path(source["location"]).resolve(strict=True)
        if not path.is_file():
            raise ValueError(f"工程来源不是文件: {path}")
        payload = path.read_bytes()
        fingerprint = source.get("fingerprint")
        if fingerprint and hashlib.sha256(payload).hexdigest() != fingerprint:
            raise ValueError(f"来源文件已改变，请先在工程中重新载入: {path.name}")
        member = f"sources/{index}/{path.name}"
        archive.writestr(member, payload)
        locations.append({"source_id": source["source_id"], "member": member})
        if path.suffix.lower() in {".esp", ".esm", ".esl"}:
            strings = path.parent / "Strings"
            if strings.is_dir():
                for sidecar in sorted(strings.iterdir()):
                    if (
                        sidecar.is_file()
                        and sidecar.name.casefold().startswith(f"{path.stem}_".casefold())
                        and sidecar.suffix.lower() in {".strings", ".dlstrings", ".ilstrings"}
                    ):
                        archive.write(sidecar, f"sources/{index}/Strings/{sidecar.name}")
    return locations


def capture_owned_assets(archive: zipfile.ZipFile, directory: Path) -> None:
    if not directory.is_dir():
        return
    root = directory.resolve()
    for source in sorted(directory.rglob("*")):
        if not source.is_file():
            continue
        relative = source.relative_to(directory)
        # Sources are captured from the current registry, while staging is never an archive asset.
        parts = tuple(part.casefold() for part in relative.parts)
        if parts[0] in {"sources", "variants"} or "staging" in parts or ".staging" in parts:
            continue
        if not source.resolve().is_relative_to(root):
            raise ValueError(f"工程资产链接超出目录: {relative}")
        if source.name.endswith(("-wal", "-shm", "-journal")):
            continue
        member = f"assets/{relative.as_posix()}"
        if source.suffix.lower() in {".sqlite3", ".sqlite", ".db"}:
            with tempfile.TemporaryDirectory(prefix="transbridge-archive-db-") as temporary:
                backup = Path(temporary) / source.name
                with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as reader:
                    with closing(sqlite3.connect(backup)) as writer:
                        reader.backup(writer)
                        if writer.execute("PRAGMA quick_check").fetchone() != ("ok",):
                            raise ValueError(f"工程数据库完整性检查失败: {relative}")
                archive.write(backup, member)
        else:
            archive.write(source, member)


def member_destination(name: str, project_directory: str) -> str:
    path = PurePosixPath(name)
    if path.parts[0] == "assets":
        relative = path.parts[1:]
        if not relative or relative[0].casefold() in {"sources", "variants", "staging", ".staging"}:
            raise ValueError("项目包资产目录无效")
    elif path.parts[0] == "sources":
        relative = path.parts
    else:
        raise ValueError(f"项目包包含未声明文件: {name}")
    return os.path.join(project_directory, *relative)
