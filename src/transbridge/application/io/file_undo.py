"""Explicit-file undo receipts for trusted publication adapters.

Construct with application-authorized absolute targets, never model-supplied
receipt paths. Hold the publication's output lock across capture/publication,
and across preflight/apply. Receipts are JSON serializable, but must be stored in
trusted application storage together with their authorized target list.

This restores bytes and permission bits, not timestamps, ACLs or external side
effects. It never removes directories. Multi-file undo is deliberately not
atomic: partial failures report exactly which files were restored. External
processes that ignore the output lock can still race an OS replace/unlink;
portable filesystems do not offer a compare-and-replace primitive here.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import stat
import tempfile
from uuid import uuid4


class FileUndoError(ValueError):
    """Unsafe targets, invalid receipts, or changed publication evidence."""


def _absolute(value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise FileUndoError("undo paths must be absolute and must not contain '..'")
    return path


def _check_path(path: Path) -> None:
    for component in (*reversed(path.parents), path):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise FileUndoError(f"links/reparse points are not valid undo targets: {component}")
        if component != path and not stat.S_ISDIR(info.st_mode):
            raise FileUndoError(f"undo parent is not a directory: {component}")


def _fingerprint(path: Path) -> dict:
    _check_path(path)
    try:
        before = path.stat()
    except FileNotFoundError:
        return {"exists": False}
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise FileUndoError(f"undo requires a regular file without hard links: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    after = path.stat()

    def signature(value):
        return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_mode

    if signature(before) != signature(after):
        raise FileUndoError(f"file changed while reading undo evidence: {path}")
    return {
        "exists": True,
        "sha256": digest.hexdigest(),
        "device": after.st_dev,
        "inode": after.st_ino,
        "size": after.st_size,
        "mtime_ns": after.st_mtime_ns,
        "mode": stat.S_IMODE(after.st_mode),
    }


def _parents(path: Path) -> list[dict]:
    _check_path(path)
    result = []
    for parent in path.parents:
        try:
            info = parent.stat()
        except FileNotFoundError:
            result.append({"path": str(parent), "exists": False})
            continue
        result.append({"path": str(parent), "device": info.st_dev, "inode": info.st_ino})
    return result


class FileUndoAdapter:
    """Capture before/after publication; reverse only explicitly bound files.

    ``backup_root`` must be an application-owned storage directory outside Git
    metadata and outside every target. Backups are retained after undo for audit
    and retry analysis; their lifetime is the owning application's responsibility.
    """

    def __init__(self, targets: Iterable[str | Path], *, backup_root: str | Path) -> None:
        self.targets = tuple(_absolute(value) for value in targets)
        self.backup_root = _absolute(backup_root)
        if not self.targets or len(set(self.targets)) != len(self.targets):
            raise FileUndoError("undo needs a nonempty, unique explicit file list")
        if ".git" in {part.casefold() for part in self.backup_root.parts}:
            raise FileUndoError("undo backups must not live in Git metadata")
        _check_path(self.backup_root)
        for path in self.targets:
            _check_path(path)
            if path == self.backup_root or self.backup_root in path.parents or path in self.backup_root.parents:
                raise FileUndoError("undo targets and backup storage must not overlap")
        self.backup_root.mkdir(parents=True, exist_ok=True)
        _check_path(self.backup_root)
        self._backup_identity = _parents(self.backup_root / "identity")

    def capture_before(self) -> dict:
        """Back up all existing files before the caller starts any publication."""
        self._check_backup_root()
        receipt = {"schema_version": 1, "operation_id": uuid4().hex, "files": []}
        for path in self.targets:
            before = _fingerprint(path)
            backup = None
            if before["exists"]:
                backup = f"{uuid4().hex}.bak"
                destination = self.backup_root / backup
                with destination.open("xb") as stream:
                    destination.chmod(0o600)
                    with path.open("rb") as source:
                        for chunk in iter(lambda: source.read(1024 * 1024), b""):
                            stream.write(chunk)
                    stream.flush()
                    os.fsync(stream.fileno())
                if _fingerprint(path) != before or _fingerprint(destination)["sha256"] != before["sha256"]:
                    raise FileUndoError(f"file changed while creating undo backup: {path}")
            receipt["files"].append({"path": str(path), "before": before, "backup": backup})
        return receipt

    def capture_after(self, receipt: Mapping) -> dict:
        """Seal hashes and identities after publication, including partial output."""
        rows = self._rows(receipt)
        result = deepcopy(dict(receipt))
        for path, row in zip(self.targets, result["files"], strict=True):
            row["after"] = _fingerprint(path)
            row["parents"] = _parents(path)
        for row in rows:
            self._check_backup(row)
        return result

    def preflight(self, receipt: Mapping) -> list[dict]:
        """Return all detected conflicts without changing any target."""
        rows = self._rows(receipt)
        errors = []
        for path, row in zip(self.targets, rows, strict=True):
            try:
                self._verify(path, row)
            except (OSError, FileUndoError) as error:
                errors.append({"path": str(path), "error": str(error)})
        return errors

    def apply(self, receipt: Mapping) -> dict:
        """Reject all preflight conflicts; report partial progress on later failure."""
        rows = self._rows(receipt)
        errors = self.preflight(receipt)
        pending = [str(path) for path in self.targets]
        restored = []
        if errors:
            return {"status": "conflict", "restored": restored, "remaining": pending, "errors": errors}
        for path, row in zip(self.targets, rows, strict=True):
            try:
                self._verify(path, row)
                self._restore(path, row)
            except (OSError, FileUndoError) as error:
                return {
                    "status": "partial" if restored else "failed",
                    "restored": restored,
                    "remaining": pending,
                    "errors": [{"path": str(path), "error": str(error)}],
                }
            restored.append(pending.pop(0))
        return {"status": "completed", "restored": restored, "remaining": [], "errors": []}

    def _rows(self, receipt: Mapping) -> list:
        if not isinstance(receipt, Mapping) or receipt.get("schema_version") != 1:
            raise FileUndoError("unsupported file undo receipt")
        rows = receipt.get("files")
        if (
            not isinstance(rows, list)
            or not all(isinstance(row, dict) for row in rows)
            or [row.get("path") for row in rows] != [str(path) for path in self.targets]
        ):
            raise FileUndoError("receipt targets do not match application-authorized files")
        for row in rows:
            before = row.get("before")
            if not isinstance(before, dict) or type(before.get("exists")) is not bool:
                raise FileUndoError("invalid before-file evidence")
        return rows

    def _check_backup_root(self) -> None:
        if _parents(self.backup_root / "identity") != self._backup_identity:
            raise FileUndoError("backup storage identity changed")

    def _check_backup(self, row: dict) -> Path | None:
        self._check_backup_root()
        name = row.get("backup")
        if not row["before"]["exists"]:
            if name is not None:
                raise FileUndoError("absent original file must not have a backup")
            return None
        if not isinstance(name, str) or len(name) != 36 or not name.endswith(".bak"):
            raise FileUndoError("invalid undo backup name")
        if any(character not in "0123456789abcdef" for character in name[:32]):
            raise FileUndoError("invalid undo backup identity")
        backup = self.backup_root / name
        actual = _fingerprint(backup)
        if not actual["exists"] or actual["sha256"] != row["before"].get("sha256"):
            raise FileUndoError("undo backup is missing or corrupt")
        if type(row["before"].get("mode")) is not int or not 0 <= row["before"]["mode"] <= 0o7777:
            raise FileUndoError("invalid original file mode")
        return backup

    def _verify(self, path: Path, row: dict) -> None:
        self._check_backup(row)
        if "after" not in row or "parents" not in row:
            raise FileUndoError("file undo receipt is not sealed after publication")
        if _fingerprint(path) != row["after"] or _parents(path) != row["parents"]:
            raise FileUndoError("output or its parent identity changed since publication")

    def _restore(self, path: Path, row: dict) -> None:
        backup = self._check_backup(row)
        if row["before"] == row["after"]:
            return
        if backup is None:
            if row["after"]["exists"]:
                path.unlink()
            return
        descriptor, temporary = tempfile.mkstemp(prefix=".transbridge-undo-", dir=path.parent)
        staged = Path(temporary)
        try:
            with os.fdopen(descriptor, "wb") as stream, backup.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    stream.write(chunk)
                stream.flush()
                os.fsync(stream.fileno())
            staged.chmod(row["before"]["mode"])
            self._verify(path, row)
            os.replace(staged, path)
        finally:
            staged.unlink(missing_ok=True)
