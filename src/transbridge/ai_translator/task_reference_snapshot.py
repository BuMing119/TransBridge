"""Immutable task reference inputs, with an optional atomic recovery sidecar."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from threading import RLock

from transbridge.application.translation.task_history import atomic_json

from .shared_term_snapshot import FrozenTermEntry
from .term_formats import term_entries_from_data, term_entry_to_canonical_dict


class TaskReferenceSnapshotError(ValueError):
    """A task-owned durable snapshot must not fall back to an unrelated cache."""


def _digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class TaskReferenceSnapshot:
    records: tuple[FrozenTermEntry, ...]
    load_log: tuple[tuple[str, int, str | None], ...] = ()


class TaskReferenceStore:
    """Capture each input once; shared generated terms are deliberately excluded.

    The caller owns the task recovery lease before invoking any capture. Reads
    may happen during background launch preparation without publishing a file.
    """

    def __init__(self, task_id: str, *, root: Path | None = None, identity=()):
        self.task_id = task_id
        self.identity = tuple(identity)
        self.path = None if root is None else Path(root) / f"{sha256(task_id.encode()).hexdigest()}.json"
        self._lock = RLock()
        self._snapshots = {}
        self._dynamic_databases = {}
        self._published = None
        self.restored = False
        if self.path is not None:
            self._restore()

    @property
    def published(self):
        # No consumer receives the stored mutable JSON tree.
        return None if self._published is None else json.loads(json.dumps(self._published))

    def capture_published(self, value):
        with self._lock:
            if self._published is None:
                self._published = json.loads(json.dumps(value))
                try:
                    self._save()
                except Exception:
                    self._published = None
                    raise

    def load_or_capture(self, source: str, loader) -> TaskReferenceSnapshot:
        with self._lock:
            existing = self._snapshots.get(source)
            if existing is not None:
                return existing
            entries, log = loader()
            snapshot = TaskReferenceSnapshot(
                tuple(FrozenTermEntry.capture(entry) for entry in entries), tuple(tuple(item) for item in log)
            )
            self._snapshots[source] = snapshot
            try:
                self._save()
            except Exception:
                del self._snapshots[source]
                raise
            return snapshot

    def dynamic_database(self, source, create):
        """Keep task-owned generated updates visible without rereading external edits."""
        with self._lock:
            database = create()
            key = str(Path(database._path).resolve())
            if key in self._dynamic_databases:
                return self._dynamic_databases[key]

            def capture():
                database.load()
                return database.as_list(), ()

            snapshot = self.load_or_capture("input:dynamic:" + source, capture)
            database._entries = [entry.mutable_copy() for entry in snapshot.records]
            self._dynamic_databases[key] = database
            return database

    def _save(self):
        if self.path is None:
            return
        payload = {
            "schema": 1,
            "task_id": self.task_id,
            "identity": list(self.identity),
            "published": self._published,
            "sources": {
                source: {
                    "entries": [term_entry_to_canonical_dict(entry.mutable_copy()) for entry in snapshot.records],
                    "load_log": snapshot.load_log,
                }
                for source, snapshot in self._snapshots.items()
            },
        }
        try:
            atomic_json(self.path, {"payload": payload, "sha256": _digest(payload)})
        except (OSError, TypeError, ValueError) as exc:
            raise TaskReferenceSnapshotError(f"无法保存任务参考快照：{self.path.name}：{exc}") from exc

    def _restore(self):
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            raise TaskReferenceSnapshotError(f"无法读取任务参考快照：{self.path.name}：{exc}") from exc
        try:
            payload = value["payload"]
            if value["sha256"] != _digest(payload):
                raise ValueError("校验和不匹配")
            if (
                payload["schema"] != 1
                or payload["task_id"] != self.task_id
                or tuple(payload["identity"]) != self.identity
            ):
                raise ValueError("任务参考快照身份不匹配")
            for source, row in payload["sources"].items():
                entries = term_entries_from_data(row["entries"], source=None)
                if len(entries) != len(row["entries"]):
                    raise ValueError("任务参考快照包含无效术语")
                self._snapshots[source] = TaskReferenceSnapshot(
                    tuple(FrozenTermEntry.capture(entry) for entry in entries),
                    tuple(tuple(item) for item in row["load_log"]),
                )
            self._published = payload["published"]
            self.restored = True
        except (KeyError, TypeError, ValueError) as exc:
            raise TaskReferenceSnapshotError(f"任务参考快照损坏，原文件已保留：{self.path.name}：{exc}") from exc


def load_manager_references(manager):
    """Reuse a source's merged baseline without rereading local/remote inputs."""
    store = getattr(manager, "_reference_store", None)
    if store is None:
        return manager._load_all_with_metadata()

    def capture():
        entries = manager._load_all_with_metadata()
        return entries, manager._load_log

    snapshot = store.load_or_capture("merged:" + manager._reference_source, capture)
    manager._load_log = list(snapshot.load_log)
    return [entry.mutable_copy() for entry in snapshot.records]


def load_reference_source(manager, source, loader):
    """Freeze the chosen input, including fallback data and its diagnostic."""

    def capture():
        try:
            entries = loader()
            manager._save_source_cache(source, entries)
            diagnostic = None
        except TaskReferenceSnapshotError:
            raise
        except Exception as exc:
            cached = manager._load_source_cache(source)
            entries = cached if cached is not None else []
            diagnostic = f"from cache (source failed: {exc})" if cached is not None else str(exc)
        return entries, ((source, len(entries), diagnostic),)

    store = getattr(manager, "_reference_store", None)
    if store is None or (source == "dynamic" and manager._shared_term_pool is None):
        # get_dynamic_db captures the input and owns the task's mutable overlay.
        entries, log = capture()
        return entries, log[0][2]
    key = "input:" + source
    if source == "dynamic":
        key += ":" + manager._reference_source
    # Durable snapshot failures are outside the source/cache fallback boundary.
    snapshot = store.load_or_capture(key, capture)
    diagnostic = snapshot.load_log[0][2] if snapshot.load_log else None
    return [entry.mutable_copy() for entry in snapshot.records], diagnostic
