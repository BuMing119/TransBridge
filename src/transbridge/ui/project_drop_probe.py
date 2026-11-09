"""Bounded format identification before the project owner admits a dropped file."""

from __future__ import annotations

import json
from pathlib import Path
import stat
import zipfile

from transbridge.fileops.archive import ArchiveExtractionError
from transbridge.fileops.archive_policy import ArchiveMember, ArchiveMemberType, ArchivePolicy
from transbridge.persistence.project_store.format import (
    PROJECT_APPLICATION_ID,
    PROJECT_FILENAME,
    PROJECT_FORMAT,
    PROJECT_FORMAT_VERSION,
)


def is_project_database(path: Path) -> bool:
    if path.name != PROJECT_FILENAME:
        return False
    with path.open("rb") as stream:
        header = stream.read(100)
    return (
        header.startswith(b"SQLite format 3\x00")
        and int.from_bytes(header[60:64], "big") == PROJECT_FORMAT_VERSION
        and int.from_bytes(header[68:72], "big") == PROJECT_APPLICATION_ID
    )


def is_legacy_project_json(content: bytes) -> bool:
    if b'"entity_type"' not in content or b'"project"' not in content:
        return False
    try:
        document = json.loads(content)
    except (UnicodeError, ValueError):
        return False
    return isinstance(document, dict) and document.get("entity_type") == "project"


def is_project_archive(path: Path, policy: ArchivePolicy) -> bool:
    try:
        with zipfile.ZipFile(path) as archive:
            manifest = policy.evaluate(tuple(_zip_member(info) for info in archive.infolist()), archive_format="zip")
            if not {"manifest.json", PROJECT_FILENAME} <= {member.name for member in manifest.files}:
                return False
            with archive.open("manifest.json") as stream:
                raw = stream.read(65537)
    except KeyError:
        return False
    except zipfile.BadZipFile as exc:
        raise ArchiveExtractionError("ARCHIVE_CORRUPT", "ZIP central directory or manifest is invalid") from exc
    except RuntimeError as exc:
        raise ArchiveExtractionError("ARCHIVE_READ_FAILED", "ZIP manifest could not be read") from exc
    if len(raw) > 65536:
        return False
    try:
        info = json.loads(raw)
    except (UnicodeError, ValueError):
        return False
    return (
        isinstance(info, dict)
        and info.get("format") == PROJECT_FORMAT
        and type(info.get("version")) is int
        and info["version"] == PROJECT_FORMAT_VERSION
    )


def _zip_member(info: zipfile.ZipInfo) -> ArchiveMember:
    mode = (info.external_attr >> 16) & 0xFFFF
    if info.is_dir():
        member_type = ArchiveMemberType.DIRECTORY
    elif stat.S_ISLNK(mode):
        member_type = ArchiveMemberType.SYMLINK
    elif stat.S_IFMT(mode) and not stat.S_ISREG(mode):
        member_type = ArchiveMemberType.SPECIAL
    else:
        member_type = ArchiveMemberType.FILE
    return ArchiveMember(info.filename, info.file_size, info.compress_size, member_type)
