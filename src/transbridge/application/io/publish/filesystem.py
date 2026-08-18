"""Fault-injectable filesystem boundary for atomic publication."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
from typing import Protocol, runtime_checkable

from .models import FileFingerprint


@runtime_checkable
class PublishFilesystemPort(Protocol):
    def canonicalize(self, path: str) -> str: ...

    def exists(self, path: str) -> bool: ...

    def read_bytes(self, path: str) -> bytes: ...

    def fingerprint(self, path: str) -> FileFingerprint: ...

    def exclusive_create(self, path: str, *, mode: int) -> None: ...

    def chmod(self, path: str, mode: int) -> None: ...

    def fsync_file(self, path: str) -> None: ...

    def fsync_directory(self, path: str) -> None: ...

    def atomic_replace(self, source: str, destination: str) -> None: ...

    def remove(self, path: str, *, missing_ok: bool = False) -> None: ...

    def copy_exclusive(self, source: str, destination: str, *, mode: int) -> None: ...

    def same_volume(self, first: str, second: str) -> bool: ...

    def atomic_replace_supported(self, path: str) -> bool: ...


class OsPublishFilesystem:
    """Local-filesystem implementation; never falls back to delete then rename."""

    def canonicalize(self, path: str) -> str:
        resolved = os.path.abspath(path)
        if os.name != "nt":
            return os.path.realpath(resolved)
        if resolved.startswith("\\\\?\\"):
            return os.path.realpath(resolved)
        if len(resolved) < 240:
            return os.path.realpath(resolved)
        if resolved.startswith("\\\\"):
            resolved = "\\\\?\\UNC\\" + resolved[2:]
        else:
            resolved = "\\\\?\\" + resolved
        return os.path.realpath(resolved)

    def exists(self, path: str) -> bool:
        return Path(path).exists()

    def read_bytes(self, path: str) -> bytes:
        return Path(path).read_bytes()

    def fingerprint(self, path: str) -> FileFingerprint:
        target = Path(path)
        if not target.exists():
            return FileFingerprint.missing()
        digest = hashlib.sha256()
        size = 0
        with target.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
        mode = stat.S_IMODE(target.stat().st_mode)
        return FileFingerprint(True, digest.hexdigest(), size, mode)

    def exclusive_create(self, path: str, *, mode: int) -> None:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode)
        os.close(descriptor)

    def chmod(self, path: str, mode: int) -> None:
        os.chmod(path, mode)

    def fsync_file(self, path: str) -> None:
        with Path(path).open("r+b") as stream:
            os.fsync(stream.fileno())

    def fsync_directory(self, path: str) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def atomic_replace(self, source: str, destination: str) -> None:
        os.replace(source, destination)

    def remove(self, path: str, *, missing_ok: bool = False) -> None:
        Path(path).unlink(missing_ok=missing_ok)

    def copy_exclusive(self, source: str, destination: str, *, mode: int) -> None:
        descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode)
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as output, Path(source).open("rb") as input_stream:
                descriptor = -1
                while chunk := input_stream.read(1024 * 1024):
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def same_volume(self, first: str, second: str) -> bool:
        if os.name == "nt":
            first_drive = os.path.splitdrive(_without_extended_prefix(first))[0]
            second_drive = os.path.splitdrive(_without_extended_prefix(second))[0]
            return first_drive.casefold() == second_drive.casefold()
        first_path = Path(first).resolve(strict=False)
        second_path = Path(second).resolve(strict=False)
        first_device = first_path.parent.stat().st_dev
        second_parent = second_path if second_path.is_dir() else second_path.parent
        return first_device == second_parent.stat().st_dev

    def atomic_replace_supported(self, path: str) -> bool:
        normalized = _without_extended_prefix(str(path))
        if os.name == "nt" and normalized.startswith(("\\\\", "//")):
            return False
        return True


def _without_extended_prefix(path: str) -> str:
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path
