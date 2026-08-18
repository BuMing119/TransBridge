"""Atomic filesystem implementation of the checkpoint port."""

from __future__ import annotations

from collections.abc import Callable
import hashlib
import os
from pathlib import Path
import tempfile
import threading
import time

from .checkpoint import CheckpointExpectation, CheckpointRecord, CheckpointRevisionError

FaultInjector = Callable[[str, Path], None]


class FilesystemCheckpointPort:
    """Persist checkpoints with same-directory temp + fsync + atomic replace."""

    _locks_guard = threading.Lock()
    _locks: dict[str, threading.RLock] = {}
    _latest_revisions: dict[str, int] = {}

    def __init__(self, root: Path | str, *, fault_injector: FaultInjector | None = None) -> None:
        self._root = Path(root)
        self._fault_injector = fault_injector

    def save(self, record: CheckpointRecord) -> None:
        target = self.path_for(record.run_id)
        with self._lock_for(target):
            self._save_locked(record, target)

    def _save_locked(self, record: CheckpointRecord, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = record.to_json_bytes()
        key = self._key_for(target)
        known_revision = self._latest_revisions.get(key)
        if known_revision is not None and known_revision > record.revision:
            raise CheckpointRevisionError(
                "checkpoint_revision_regression",
                f"refusing checkpoint revision {record.revision}; current is {known_revision}",
            )
        if target.exists():
            current = CheckpointRecord.from_json_bytes(target.read_bytes())
            self._latest_revisions[key] = max(current.revision, known_revision or current.revision)
            if current.revision > record.revision:
                raise CheckpointRevisionError(
                    "checkpoint_revision_regression",
                    f"refusing checkpoint revision {record.revision}; current is {current.revision}",
                )
            if current.revision == record.revision:
                if current == record:
                    return
                raise CheckpointRevisionError(
                    "checkpoint_revision_conflict",
                    f"checkpoint revision {record.revision} has conflicting content",
                )
        descriptor = -1
        temporary: Path | None = None
        try:
            self._inject("before_temp_write", target)
            descriptor, raw_path = tempfile.mkstemp(
                prefix=f".{target.name}.",
                suffix=".tmp",
                dir=target.parent,
            )
            temporary = Path(raw_path)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(payload)
                stream.flush()
                self._inject("after_temp_write", temporary)
                os.fsync(stream.fileno())
                self._inject("after_temp_fsync", temporary)

            # Validate exactly what reached disk before replacing the last good file.
            validated = CheckpointRecord.from_json_bytes(temporary.read_bytes())
            if validated != record:
                raise RuntimeError("checkpoint temp validation did not round-trip")
            self._inject("before_replace", target)
            self._replace_with_retry(temporary, target)
            temporary = None
            self._latest_revisions[key] = record.revision
            self._inject("after_replace", target)
            self._fsync_directory(target.parent)
            self._inject("after_directory_fsync", target.parent)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def load(
        self,
        run_id: str,
        *,
        expected: CheckpointExpectation | None = None,
    ) -> CheckpointRecord | None:
        path = self.path_for(run_id)
        with self._lock_for(path):
            self._inject("before_load", path)
            if not path.exists():
                return None
            record = CheckpointRecord.from_json_bytes(path.read_bytes())
            key = self._key_for(path)
            self._latest_revisions[key] = max(
                record.revision,
                self._latest_revisions.get(key, record.revision),
            )
            if record.run_id != run_id:
                # A hash collision or manual file substitution must never cross runs.
                from .checkpoint import CheckpointMismatchError

                raise CheckpointMismatchError(
                    "checkpoint_run_id_mismatch",
                    "checkpoint path content belongs to a different run",
                )
            if expected is not None:
                record.validate(expected)
            self._inject("after_load", path)
            return record

    def delete(self, run_id: str) -> bool:
        path = self.path_for(run_id)
        with self._lock_for(path):
            try:
                path.unlink()
            except FileNotFoundError:
                return False
            self._fsync_directory(path.parent)
            self._latest_revisions.pop(self._key_for(path), None)
            return True

    def path_for(self, run_id: str) -> Path:
        if not run_id or not run_id.strip():
            raise ValueError("run_id must not be empty")
        digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()
        return self._root / digest[:2] / f"{digest}.checkpoint.json"

    def _inject(self, stage: str, path: Path) -> None:
        if self._fault_injector is not None:
            self._fault_injector(stage, path)

    @classmethod
    def _lock_for(cls, path: Path) -> threading.RLock:
        key = cls._key_for(path)
        with cls._locks_guard:
            return cls._locks.setdefault(key, threading.RLock())

    @staticmethod
    def _key_for(path: Path) -> str:
        # Path.resolve() may produce different lexical forms while a Windows
        # parent is being created. The absolute, case-folded path is stable
        # before and after the target exists, so all port instances share one
        # in-process lock and revision high-water mark.
        return os.path.normcase(os.path.abspath(path))

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        try:
            descriptor = os.open(path, flags)
        except OSError:
            # Windows does not allow opening a directory this way. File fsync and
            # os.replace remain the strongest portable durability guarantee.
            return
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _replace_with_retry(source: Path, target: Path) -> None:
        """Tolerate bounded Windows sharing races without weakening atomicity."""
        attempts = 5 if os.name == "nt" else 1
        for attempt in range(attempts):
            try:
                os.replace(source, target)
                return
            except PermissionError as exc:
                winerror = getattr(exc, "winerror", None)
                transient = os.name == "nt" and (winerror in {5, 32} or (winerror is None and exc.errno in {5, 13}))
                if not transient or attempt == attempts - 1:
                    raise
                time.sleep(0.02 * (attempt + 1))
