"""Atomic V2 Session catalog used by the SessionList projection."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from threading import RLock

from .filesystem import PersistenceFilesystemPort
from .ids import SessionId


@dataclass(frozen=True, slots=True)
class SessionCatalogEntry:
    session_id: str
    name: str
    last_active_at: str
    message_count: int
    recovery: str

    def __post_init__(self) -> None:
        SessionId(self.session_id)
        if not self.name.strip() or not self.last_active_at:
            raise ValueError("Session catalog name and timestamp must not be empty")
        if self.message_count < 0:
            raise ValueError("Session catalog message count must not be negative")


class SessionCatalogRepository:
    SCHEMA_VERSION = 1

    def __init__(self, root: str, filesystem: PersistenceFilesystemPort) -> None:
        self._filesystem = filesystem
        self._root = filesystem.canonicalize(root)
        self._path = self._guard(os.path.join(self._root, "session-catalog.json"))
        self._lock = RLock()

    def list(self) -> tuple[SessionCatalogEntry, ...]:
        with self._lock:
            if not self._filesystem.exists(self._path):
                return ()
            document = self._read()
            entries = tuple(SessionCatalogEntry(**value) for value in document["sessions"])
            return tuple(sorted(entries, key=lambda item: item.last_active_at, reverse=True))

    def upsert(self, entry: SessionCatalogEntry) -> None:
        with self._lock:
            entries = {item.session_id: item for item in self.list()}
            entries[entry.session_id] = entry
            self._write(tuple(entries.values()))

    def remove(self, session_id: str) -> None:
        SessionId(session_id)
        with self._lock:
            entries = tuple(item for item in self.list() if item.session_id != session_id)
            self._write(entries)

    def _read(self) -> dict:
        try:
            document = json.loads(self._filesystem.read_bytes(self._path))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Session catalog is invalid and remains read-only") from exc
        if not isinstance(document, dict) or not isinstance(document.get("sessions"), list):
            raise RuntimeError("Session catalog structure is invalid and remains read-only")
        version = document.get("schema_version")
        if version != self.SCHEMA_VERSION:
            raise RuntimeError("Session catalog schema is unsupported and remains read-only")
        return document

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
