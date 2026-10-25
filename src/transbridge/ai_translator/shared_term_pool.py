"""Transactional generated terminology shared by one Project/Variant/language."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile
from threading import RLock

from transbridge.application.terminology.identity import normalize_original, normalize_translation
from transbridge.persistence.v2.filesystem import OsPersistenceFilesystem
from transbridge.persistence.v2.models import ReadOnlyWriteRefused
from transbridge.persistence.v2.session_write_lease import persistence_write_lease

from .shared_term_snapshot import SharedTermSnapshot, SharedTermSnapshots
from .term_formats import TermEntry, term_entries_from_data, term_entry_to_canonical_dict
from .term_publication_conflict import TermPublicationConflict
from .term_validation import valid_term_pair

type GeneratedTerms = list[tuple[str, str, str, str]]
type TermAuthority = Callable[[str], tuple[TermEntry | None, bool]]


@dataclass(frozen=True, slots=True)
class SharedTermChange:
    revision: int
    term: str
    translation: str
    plugin_id: str | None = None


@dataclass(frozen=True, slots=True)
class SharedTermConflict:
    revision: int
    term: str
    accepted_translation: str
    proposed_translation: str
    plugin_id: str | None
    source: str
    reason: str


class SharedTermPool:
    """Persist before publication; reject conflicting automatic replacements.

    This is a task-owned object, shared by all of its source managers. It does
    not edit the published terminology version or import per-plugin databases.
    A second task changing the same file is detected instead of overwritten.
    """

    def __init__(
        self,
        local_project_id: str,
        local_variant_id: str,
        target_language: str,
        *,
        path: str | Path | None = None,
    ) -> None:
        if not all(value.strip() for value in (local_project_id, local_variant_id, target_language)):
            raise ValueError("shared terminology requires Project, Variant and target language")
        self.identity = (local_project_id, local_variant_id, target_language)
        if path is None:
            from transbridge.config.paths import get_data_dir

            key = hashlib.sha256(json.dumps(self.identity, ensure_ascii=False).encode()).hexdigest()
            path = Path(get_data_dir()) / "ai_translator" / "project_terms" / f"{key}.json"
        self._path = str(Path(path).resolve())
        self._lock = RLock()
        self._entries: list[TermEntry] = []
        self._conflicts: list[SharedTermConflict] = []
        self._publication_conflicts: dict[tuple[str | None, str, str, str], SharedTermConflict] = {}
        self._revision = 0
        self._snapshots: SharedTermSnapshots | None = None
        self._persisted: bytes | None = None
        self._loaded = False
        self.load()

    @property
    def revision(self) -> int:
        with self._lock:
            return self._revision

    def load(self) -> None:
        """Restore once; subsequent calls detect unacknowledged external edits."""
        with self._lock, self._lease():
            raw = self._read_bytes()
            if self._loaded:
                if raw != self._persisted:
                    raise TermPublicationConflict("共享术语文件已被其他任务修改，拒绝覆盖外部修改")
                return
            if raw is not None:
                try:
                    payload = json.loads(raw)
                    if tuple(payload["identity"]) != self.identity:
                        raise ValueError("shared terminology belongs to another Project/Variant/language")
                    revision = payload["revision"]
                    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
                        raise ValueError("invalid shared terminology revision")
                    entries = term_entries_from_data(payload["terms"], source=None)
                    if len(entries) != len(payload["terms"]):
                        raise ValueError("invalid shared terminology record")
                    keys = set()
                    for entry in entries:
                        metadata = entry.metadata
                        plugin = metadata.get("shared_plugin_id")
                        term_revision = metadata.get("shared_revision")
                        key = (plugin, normalize_original(entry.term))
                        if (
                            not valid_term_pair(entry.term, entry.translation)
                            or (plugin is not None and (not isinstance(plugin, str) or not plugin.strip()))
                            or not isinstance(term_revision, int)
                            or not 0 < term_revision <= revision
                            or key in keys
                        ):
                            raise ValueError("invalid shared terminology scope or revision")
                        keys.add(key)
                    conflicts = [SharedTermConflict(**item) for item in payload.get("conflicts", [])]
                    if any(not 0 < item.revision <= revision for item in conflicts):
                        raise ValueError("invalid shared terminology conflict revision")
                except (ValueError, TypeError, KeyError) as exc:
                    raise ValueError(f"无法恢复共享术语库 {self._path}: {exc}") from exc
                self._entries, self._conflicts, self._revision = entries, conflicts, revision
            self._persisted, self._loaded = raw, True

    def as_list(self) -> list[TermEntry]:
        """All persisted scopes for ownership validation; use snapshot for lookup."""
        with self._lock:
            return deepcopy(self._entries)

    def snapshot(self, plugin_id: str | None = None) -> SharedTermSnapshot:
        with self._lock:
            if self._snapshots is None:
                self._snapshots = SharedTermSnapshots(self._revision, self._entries)
            return self._snapshots.snapshot(plugin_id)

    def changes_since(self, revision: int) -> tuple[SharedTermChange, ...]:
        with self._lock:
            return tuple(
                SharedTermChange(
                    entry.metadata["shared_revision"],
                    entry.term,
                    entry.translation,
                    entry.metadata.get("shared_plugin_id"),
                )
                for entry in self._entries
                if entry.metadata["shared_revision"] > revision
            )

    def conflicts_since(self, revision: int) -> tuple[SharedTermConflict, ...]:
        with self._lock:
            return tuple(
                item for item in (*self._conflicts, *self._publication_conflicts.values()) if item.revision > revision
            )

    def record_publication_conflict(self, terms: GeneratedTerms, *, plugin_id: str | None = None) -> None:
        """Keep rejected automatic writes in task diagnostics without touching disk.

        The next revision identifies an attempted publication after the current
        committed baseline. It never changes the pool's revision or snapshots.
        """
        with self._lock:
            for term, translation, source, _context in terms:
                if not valid_term_pair(term, translation):
                    continue
                key = (plugin_id, normalize_original(term), normalize_translation(translation), source)
                self._publication_conflicts.setdefault(
                    key,
                    SharedTermConflict(
                        self._revision + 1,
                        term,
                        "",
                        translation,
                        plugin_id,
                        source,
                        "external-write-conflict",
                    ),
                )

    def publish(
        self,
        terms: GeneratedTerms,
        *,
        authority: TermAuthority | None = None,
        plugin_id: str | None = None,
        context_plugin_id: str | None = None,
        acknowledge: Callable[[GeneratedTerms], None] | None = None,
    ) -> GeneratedTerms:
        """Called by GeneratedTermWriter after its task gate has been acquired.

        ``plugin_id`` is an explicitly requested generated scope. The separate
        context ID protects published plugin exceptions during project writes.
        Readers cannot see staged values until persistence and acknowledgement
        both succeed. Conflicts are diagnostics, never automatic replacements.
        """
        if plugin_id is not None and not plugin_id.strip():
            raise ValueError("generated plugin scope must be absent or non-empty")
        with self._lock, self._publication_lease():
            self.load()
            revision = self._revision + 1
            staged = deepcopy(self._entries)
            conflicts = list(self._conflicts)
            accepted: GeneratedTerms = []
            existing = {(item.metadata.get("shared_plugin_id"), normalize_original(item.term)): item for item in staged}
            for term, translation, source, context in terms:
                if not valid_term_pair(term, translation):
                    continue
                authoritative, blocked = authority(term) if authority is not None else (None, False)
                current = existing.get((plugin_id, normalize_original(term)))
                reason = "published-term" if authoritative is not None or blocked else "generated-term"
                selected = authoritative if authoritative is not None else current
                if blocked or selected is not None:
                    if not blocked and normalize_translation(selected.translation) == normalize_translation(
                        translation
                    ):
                        continue
                    conflict = SharedTermConflict(
                        revision,
                        term,
                        "" if selected is None else selected.translation,
                        translation,
                        context_plugin_id if reason == "published-term" else plugin_id,
                        source,
                        "published-term-blocked" if blocked else reason,
                    )
                    if not any(asdict(item) | {"revision": revision} == asdict(conflict) for item in conflicts):
                        conflicts.append(conflict)
                    continue
                entry = TermEntry(
                    term,
                    translation,
                    source,
                    context,
                    created_at=datetime.now().isoformat(timespec="seconds"),
                    metadata={"shared_revision": revision, "shared_plugin_id": plugin_id},
                )
                existing[(plugin_id, normalize_original(term))] = entry
                staged.append(entry)
                accepted.append((term, translation, source, context))
            if not accepted and conflicts == self._conflicts:
                return []
            payload = json.dumps(
                {
                    "identity": self.identity,
                    "revision": revision,
                    "terms": [term_entry_to_canonical_dict(item) for item in staged],
                    "conflicts": [asdict(item) for item in conflicts],
                },
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8")
            previous = self._entries, self._conflicts, self._revision, self._persisted
            previous_snapshots = self._snapshots
            self.load()
            self._write_bytes(payload)
            self._entries, self._conflicts, self._revision, self._persisted = staged, conflicts, revision, payload
            self._snapshots = None
            try:
                if self._read_bytes() != payload:
                    raise TermPublicationConflict("共享术语保存期间文件被外部修改，未发布本次新增术语")
                if acknowledge is not None:
                    acknowledge(accepted)
                if self._read_bytes() != payload:
                    raise TermPublicationConflict("共享术语确认期间文件被外部修改，未发布本次新增术语")
            except Exception:
                self._entries, self._conflicts, self._revision, self._persisted = previous
                self._snapshots = previous_snapshots
                # An external editor's bytes must never be rolled back by us.
                if self._read_bytes() == payload:
                    if previous[3] is None:
                        Path(self._path).unlink()
                    else:
                        self._write_bytes(previous[3])
                raise
            return accepted

    @contextmanager
    def _publication_lease(self):
        try:
            with self._lease():
                yield
        except ReadOnlyWriteRefused as exc:
            if exc.code != "SHARED_TERMINOLOGY_BUSY":
                raise
            raise TermPublicationConflict("共享术语库正在被其他任务写入，本次新增术语未写入") from exc

    def _lease(self):
        # The same canonical file must use the same persistent lock inode,
        # including across independent task objects and OS processes. Hold it
        # through acknowledgement/rollback so initial readers see committed data.
        key = hashlib.sha256(os.path.normcase(self._path).encode()).hexdigest()
        return persistence_write_lease(
            str(Path(self._path).parent),
            f"shared-terms-{key}",
            OsPersistenceFilesystem(),
            "SHARED_TERMINOLOGY_BUSY",
        )

    def _read_bytes(self) -> bytes | None:
        try:
            return Path(self._path).read_bytes()
        except FileNotFoundError:
            return None

    def _write_bytes(self, payload: bytes) -> None:
        path = Path(self._path)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
