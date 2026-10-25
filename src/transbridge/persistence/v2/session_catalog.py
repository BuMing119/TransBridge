"""Atomic V2 Session catalog used by the SessionList projection."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import logging
import os
from threading import RLock

from .filesystem import OsPersistenceFilesystem, PersistenceFilesystemPort
from .ids import ProjectId, SessionId, VariantId
from .session_write_lease import persistence_write_lease

_logger = logging.getLogger(__name__)
_LOCKS_GUARD = RLock()
_LOCKS: dict[tuple[object, str], object] = {}


class _DamagedCatalog(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SessionCatalogEntry:
    session_id: str
    name: str
    last_active_at: str
    message_count: int
    recovery: str
    project_id: str | None = None
    variant_id: str | None = None

    def __post_init__(self) -> None:
        SessionId(self.session_id)
        if not isinstance(self.name, str) or not self.name.strip() or not isinstance(self.last_active_at, str):
            raise ValueError("Session catalog name and timestamp must be strings")
        if not self.last_active_at:
            raise ValueError("Session catalog name and timestamp must not be empty")
        if type(self.message_count) is not int or self.message_count < 0:
            raise ValueError("Session catalog message count must not be negative")
        if self.project_id is not None:
            ProjectId(self.project_id)
        if self.variant_id is not None:
            VariantId(self.variant_id)
            if self.project_id is None:
                raise ValueError("Session catalog Variant scope requires a Project scope")


class SessionCatalogRepository:
    SCHEMA_VERSION = 1

    def __init__(self, root: str, filesystem: PersistenceFilesystemPort) -> None:
        self._filesystem = filesystem
        self._root = filesystem.canonicalize(root)
        self._path = self._guard(os.path.join(self._root, "session-catalog.json"))
        family = OsPersistenceFilesystem if isinstance(filesystem, OsPersistenceFilesystem) else id(filesystem)
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault((family, os.path.normcase(self._root)), RLock())

    def list(self) -> tuple[SessionCatalogEntry, ...]:
        with self._lock:
            raw = self._read_bytes()
            try:
                entries = self._decode(raw)
            except _DamagedCatalog:
                entries = self._recover()
            return tuple(sorted(entries, key=lambda item: item.last_active_at, reverse=True))

    def upsert(self, entry: SessionCatalogEntry) -> None:
        with self._lock, self._lease():
            entries = {item.session_id: item for item in self.list()}
            entries[entry.session_id] = entry
            self._write(tuple(entries.values()))

    def remove(self, session_id: str) -> None:
        SessionId(session_id)
        with self._lock, self._lease():
            entries = tuple(item for item in self.list() if item.session_id != session_id)
            self._write(entries)

    def _read_bytes(self) -> bytes | None:
        return self._filesystem.read_bytes(self._path) if self._filesystem.exists(self._path) else None

    def _decode(self, raw: bytes | None) -> tuple[SessionCatalogEntry, ...]:
        if raw is None:
            raise _DamagedCatalog("Session catalog is missing")
        try:
            document = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _DamagedCatalog("Session catalog is invalid") from exc
        if not isinstance(document, dict):
            raise _DamagedCatalog("Session catalog structure is invalid")
        version = document.get("schema_version")
        if type(version) is int and version > self.SCHEMA_VERSION:
            raise RuntimeError("Session catalog schema is unsupported and remains read-only")
        if type(version) is not int or version != self.SCHEMA_VERSION or not isinstance(document.get("sessions"), list):
            raise _DamagedCatalog("Session catalog structure is invalid")
        try:
            entries = tuple(SessionCatalogEntry(**value) for value in document["sessions"])
            if len({entry.session_id for entry in entries}) != len(entries):
                raise ValueError("Session catalog identities must be unique")
            return entries
        except (TypeError, ValueError) as exc:
            raise _DamagedCatalog("Session catalog contains invalid rows") from exc

    def _lease(self):
        return persistence_write_lease(self._root, "session-catalog", self._filesystem, "SESSION_CATALOG_BUSY")

    def _recover(self) -> tuple[SessionCatalogEntry, ...]:
        from .session_catalog_recovery import recover_catalog_rows

        with self._lease():
            raw = self._read_bytes()
            try:
                return self._decode(raw)
            except _DamagedCatalog:
                pass
            entries = tuple(SessionCatalogEntry(**row) for row in recover_catalog_rows(self._root, self._filesystem))
            if raw is None and not entries:
                return ()
            if raw is not None:
                self._preserve_damaged_index(raw)
            if self._read_bytes() != raw:
                raise RuntimeError("Session catalog changed during recovery")
            self._write(entries)
            _logger.warning("Rebuilt Session catalog from %d conversation records", len(entries))
            return entries

    def _preserve_damaged_index(self, raw: bytes) -> None:
        digest = hashlib.sha256(raw).hexdigest()
        backup = self._guard(os.path.join(self._root, "backups", "session-catalog", f"{digest}.json"))
        if not self._filesystem.exists(backup):
            stage = self._guard(os.path.join(self._root, ".staging", f"catalog-backup-{digest}.tmp"))
            self._filesystem.make_dirs(os.path.dirname(backup))
            self._filesystem.make_dirs(os.path.dirname(stage))
            self._filesystem.remove(stage, missing_ok=True)
            try:
                self._filesystem.write_bytes(stage, raw)
                if self._filesystem.read_bytes(stage) != raw:
                    raise OSError("Session catalog recovery backup staging verification failed")
                self._filesystem.replace_durable(stage, backup)
            finally:
                self._filesystem.remove(stage, missing_ok=True)
        if self._filesystem.read_bytes(backup) != raw:
            raise OSError("Session catalog recovery backup verification failed")

    def _write(self, entries: tuple[SessionCatalogEntry, ...]) -> None:
        payload = json.dumps(
            {
                "schema_version": self.SCHEMA_VERSION,
                "sessions": [asdict(item) for item in sorted(entries, key=lambda value: value.session_id)],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        token = hashlib.sha256(payload).hexdigest()
        stage = self._guard(os.path.join(self._root, ".staging", f"session-catalog-{token}.tmp"))
        self._filesystem.make_dirs(os.path.dirname(self._path))
        self._filesystem.make_dirs(os.path.dirname(stage))
        self._filesystem.remove(stage, missing_ok=True)
        try:
            self._filesystem.write_bytes(stage, payload)
            if self._filesystem.read_bytes(stage) != payload:
                raise OSError("Session catalog staging verification failed")
            self._filesystem.replace(stage, self._path)
        except Exception:
            self._filesystem.remove(stage, missing_ok=True)
            raise

    def _guard(self, path: str) -> str:
        canonical = self._filesystem.canonicalize(path)
        try:
            common = os.path.commonpath((self._root, canonical))
        except ValueError as exc:
            raise ValueError("Session catalog path is on a different root") from exc
        if os.path.normcase(common) != os.path.normcase(self._root):
            raise ValueError("Session catalog path escapes persistence root")
        return canonical


__all__ = ["SessionCatalogEntry", "SessionCatalogRepository"]
