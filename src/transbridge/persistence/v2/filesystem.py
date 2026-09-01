"""Injectable filesystem boundary and root-confined path derivation."""

from __future__ import annotations

import ctypes
import hashlib
import os
from pathlib import Path
from typing import Protocol, runtime_checkable

from .ids import EntityKind, EntityRef, ProjectRef
from .models import AtomicWriteError, BackupVerificationError, PathBoundaryError


@runtime_checkable
class PersistenceFilesystemPort(Protocol):
    """All persistence disk effects cross this fault-injectable boundary."""

    def canonicalize(self, path: str) -> str: ...

    def exists(self, path: str) -> bool: ...

    def read_bytes(self, path: str) -> bytes: ...

    def list_files(self, directory: str) -> tuple[str, ...]: ...

    def list_tree_files(self, directory: str) -> tuple[str, ...]: ...

    def make_dirs(self, path: str) -> None: ...

    def write_bytes(self, path: str, data: bytes) -> None: ...

    def replace(self, source: str, destination: str) -> None: ...

    def replace_durable(self, source: str, destination: str) -> None: ...

    def remove(self, path: str, *, missing_ok: bool = False) -> None: ...

    def remove_empty_tree(self, directory: str) -> None: ...


FilesystemPort = PersistenceFilesystemPort


def _fsync_directory(path: str) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class OsPersistenceFilesystem:
    """Local adapter. Repository tests use injected fakes, never real project data."""

    def canonicalize(self, path: str) -> str:
        return os.path.realpath(os.path.abspath(path))

    def exists(self, path: str) -> bool:
        return Path(path).exists()

    def read_bytes(self, path: str) -> bytes:
        return Path(path).read_bytes()

    def list_files(self, directory: str) -> tuple[str, ...]:
        try:
            entries = Path(directory).iterdir()
            files = (self.canonicalize(str(entry)) for entry in entries if entry.is_file())
            return tuple(sorted(files, key=os.path.normcase))
        except FileNotFoundError:
            return ()

    def list_tree_files(self, directory: str) -> tuple[str, ...]:
        root = Path(directory)
        if not root.exists():
            return ()
        canonical_root = self.canonicalize(str(root))
        files: list[str] = []
        for current_root, directory_names, file_names in os.walk(root, followlinks=False):
            canonical_current = self.canonicalize(current_root)
            try:
                common = os.path.commonpath((canonical_root, canonical_current))
            except ValueError as exc:
                raise OSError("Project-owned directory escaped its authorized root") from exc
            if os.path.normcase(common) != os.path.normcase(canonical_root):
                raise OSError("Project-owned directory escaped its authorized root")
            for name in tuple(directory_names):
                path = os.path.join(current_root, name)
                if os.path.islink(path):
                    raise OSError("Project-owned directory contains a symbolic link")
            for name in file_names:
                path = os.path.join(current_root, name)
                if os.path.islink(path):
                    raise OSError("Project-owned directory contains a symbolic link")
                files.append(self.canonicalize(path))
        return tuple(sorted(files, key=os.path.normcase))

    def make_dirs(self, path: str) -> None:
        Path(path).mkdir(parents=True, exist_ok=True)

    def write_bytes(self, path: str, data: bytes) -> None:
        with Path(path).open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())

    def replace(self, source: str, destination: str) -> None:
        os.replace(source, destination)

    def replace_durable(self, source: str, destination: str) -> None:
        """Replace a recovery boundary and flush its directory entry."""

        if os.name == "nt":
            move_file = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
            move_file.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32)
            move_file.restype = ctypes.c_int
            movefile_replace_existing = 0x00000001
            movefile_write_through = 0x00000008
            if not move_file(
                source,
                destination,
                movefile_replace_existing | movefile_write_through,
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            return

        os.replace(source, destination)
        source_parent = os.path.dirname(source)
        destination_parent = os.path.dirname(destination)
        _fsync_directory(destination_parent)
        if os.path.normcase(source_parent) != os.path.normcase(destination_parent):
            _fsync_directory(source_parent)

    def remove(self, path: str, *, missing_ok: bool = False) -> None:
        Path(path).unlink(missing_ok=missing_ok)

    def remove_empty_tree(self, directory: str) -> None:
        root = Path(directory)
        if not root.exists():
            return
        for current_root, _directory_names, _file_names in os.walk(root, topdown=False, followlinks=False):
            Path(current_root).rmdir()


class RepositoryPaths:
    def __init__(self, root: str, filesystem: PersistenceFilesystemPort) -> None:
        if not os.path.isabs(root):
            raise PathBoundaryError("persistence root must be absolute")
        self._filesystem = filesystem
        self.root = filesystem.canonicalize(root)

    def record(self, ref: EntityRef) -> str:
        return self._path(*_scope(ref), f"{ref.identity.encoded}.json")

    def backup(self, ref: EntityRef, digest: str, version: int) -> str:
        return self._path("backups", *_scope(ref), ref.identity.encoded, f"{digest}.v{version}.json")

    def quarantine_payload(self, ref: EntityRef, digest: str) -> str:
        return self._path("quarantine", *_scope(ref), f"{ref.identity.encoded}-{digest}.json")

    def quarantine_report(self, ref: EntityRef, digest: str) -> str:
        return self._path("quarantine", *_scope(ref), f"{ref.identity.encoded}-{digest}.report.json")

    def staging(self, ref: EntityRef, token: str, purpose: str) -> str:
        # Staging is internal: keep UUID identities and tokens out of nested filenames
        # so ordinary Windows data roots do not exceed MAX_PATH during a save.
        identity = "\0".join((*_scope(ref), ref.identity.value, purpose, token))
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return self._path(".staging", f"{digest}.tmp")

    def project_terminology(self, ref: ProjectRef) -> str:
        """Locate Project-owned terminology assets without exposing root joins to UI code."""

        if not isinstance(ref, ProjectRef):
            raise TypeError("terminology assets require a Project reference")
        return self._path("projects", ref.identity.encoded, "terminology")

    def project_data(self, ref: ProjectRef) -> str:
        """Return the fixed, identity-owned Project directory.

        This path is deliberately independent of every external source location
        stored in the Project document.
        """

        if not isinstance(ref, ProjectRef):
            raise TypeError("Project-owned data requires a Project reference")
        return self._path("projects", ref.identity.encoded)

    def project_backup_data(self, ref: ProjectRef) -> str:
        """Return the fixed backup tree owned by one Project identity."""

        if not isinstance(ref, ProjectRef):
            raise TypeError("Project backup data requires a Project reference")
        return self._path("backups", "projects", ref.identity.encoded)

    def guard(self, path: str) -> str:
        canonical = self._filesystem.canonicalize(path)
        try:
            common = os.path.commonpath((self.root, canonical))
        except ValueError as exc:
            raise PathBoundaryError("persistence path is on a different root") from exc
        if os.path.normcase(common) != os.path.normcase(self.root):
            raise PathBoundaryError("persistence path escapes its authorized root")
        return canonical

    def _path(self, *parts: str) -> str:
        return self.guard(os.path.join(self.root, *parts))


def staging_replace(
    filesystem: PersistenceFilesystemPort,
    paths: RepositoryPaths,
    ref: EntityRef,
    destination: str,
    data: bytes,
    *,
    token: str,
    purpose: str,
) -> None:
    destination = paths.guard(destination)
    stage = paths.staging(ref, token, purpose)
    filesystem.make_dirs(os.path.dirname(stage))
    filesystem.make_dirs(os.path.dirname(destination))
    try:
        filesystem.remove(stage, missing_ok=True)
        filesystem.write_bytes(stage, data)
        if filesystem.read_bytes(stage) != data:
            raise AtomicWriteError("staging verification failed")
        filesystem.replace(stage, destination)
    except Exception as exc:
        try:
            filesystem.remove(stage, missing_ok=True)
        except Exception:
            pass
        if isinstance(exc, AtomicWriteError):
            raise
        raise AtomicWriteError(f"atomic replace failed during {purpose}") from exc


def verified_copy(
    filesystem: PersistenceFilesystemPort,
    paths: RepositoryPaths,
    ref: EntityRef,
    destination: str,
    data: bytes,
    *,
    digest: str,
    purpose: str,
) -> bool:
    destination = paths.guard(destination)
    if filesystem.exists(destination):
        if filesystem.read_bytes(destination) != data:
            raise BackupVerificationError(f"existing {purpose} does not match the source hash")
        return False
    staging_replace(
        filesystem,
        paths,
        ref,
        destination,
        data,
        token=digest,
        purpose=purpose,
    )
    try:
        if filesystem.read_bytes(destination) != data:
            raise BackupVerificationError(f"{purpose} verification failed")
    except Exception:
        try:
            filesystem.remove(destination, missing_ok=True)
        except Exception:
            pass
        raise
    return True


def _scope(ref: EntityRef) -> tuple[str, ...]:
    if ref.kind is EntityKind.PROJECT:
        return ("projects",)
    if ref.kind is EntityKind.VARIANT:
        return ("projects", ref.project_id.encoded, "variants")
    return ("sessions",)


__all__ = [
    "FilesystemPort",
    "OsPersistenceFilesystem",
    "PersistenceFilesystemPort",
    "RepositoryPaths",
    "staging_replace",
    "verified_copy",
]
