# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""SQLite-backed workbench translation libraries."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from contextlib import closing, contextmanager
import json
from pathlib import Path
import sqlite3
from typing import TYPE_CHECKING, Protocol

from transbridge.application.io.identity import EntryKey
from transbridge.application.io.stage_policy import DEFAULT_STAGE_POLICY
from transbridge.config.paths import get_data_dir
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.filesystem_paths import native_path, sqlite_uri

from .library_matching import LibraryMatcher, LibraryRecord, locate_entry

if TYPE_CHECKING:
    from transbridge.application.terminology_profiles.dictionary_projection import DictionaryTextProjection


class ProjectionFactory(Protocol):
    def __call__(self, *, to_default: bool) -> DictionaryTextProjection | None: ...


_FILENAME = "libraries.sqlite3"
_APPLICATION_ID = 0x54424C42  # TBLB
_SCHEMA_VERSION = 3
_DEFAULT_GAME = "skyrimse"
_DEFAULT_SOURCE = "en"
_DEFAULT_TARGET = "zh"
_SCHEMA = (
    """
    CREATE TABLE main_libraries (
        game TEXT NOT NULL, source_locale TEXT NOT NULL, target_locale TEXT NOT NULL,
        name TEXT NOT NULL CHECK (name <> ''), active INTEGER NOT NULL DEFAULT 0 CHECK (active IN (0, 1)),
        PRIMARY KEY (game, source_locale, target_locale, name)
    ) WITHOUT ROWID
    """,
    """
    CREATE UNIQUE INDEX active_main_library
    ON main_libraries (game, source_locale, target_locale) WHERE active = 1
    """,
    """
    CREATE TABLE library_records (
        game TEXT NOT NULL, source_locale TEXT NOT NULL, target_locale TEXT NOT NULL, library_name TEXT NOT NULL,
        locator TEXT NOT NULL, qualifier TEXT NOT NULL, original TEXT NOT NULL, translation TEXT NOT NULL,
        source_mod TEXT NOT NULL, source_mod_key TEXT NOT NULL, record_type TEXT NOT NULL, field TEXT NOT NULL,
        source_key TEXT NOT NULL, native_form_id TEXT NOT NULL, editor_id TEXT NOT NULL,
        PRIMARY KEY (game, source_locale, target_locale, library_name, locator, qualifier, source_mod_key)
    ) WITHOUT ROWID
    """,
)
_CREATE_SAVE_CHECKPOINTS = """
    CREATE TABLE saved_source_checkpoints (
        game TEXT NOT NULL, source_locale TEXT NOT NULL, target_locale TEXT NOT NULL,
        project_id TEXT NOT NULL, variant_id TEXT NOT NULL, source_id TEXT NOT NULL, mod_id TEXT NOT NULL,
        project_revision INTEGER, revision INTEGER NOT NULL, locator_collisions INTEGER NOT NULL,
        projection_identity TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (game, source_locale, target_locale, project_id, variant_id, source_id, mod_id)
    ) WITHOUT ROWID
"""
_SELECT_SAVE_CHECKPOINT = """
    SELECT project_revision, revision, locator_collisions, projection_identity FROM saved_source_checkpoints
    WHERE (game, source_locale, target_locale, project_id, variant_id, source_id, mod_id) = (?, ?, ?, ?, ?, ?, ?)
"""
_UPSERT_SAVE_CHECKPOINT = """
    INSERT INTO saved_source_checkpoints VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT (game, source_locale, target_locale, project_id, variant_id, source_id, mod_id)
    DO UPDATE SET project_revision = excluded.project_revision, revision = excluded.revision,
                  locator_collisions = excluded.locator_collisions, projection_identity = excluded.projection_identity
"""
_UPSERT_RECORD = """
    INSERT INTO library_records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT (game, source_locale, target_locale, library_name, locator, qualifier, source_mod_key)
    DO UPDATE SET original = excluded.original, translation = excluded.translation, source_mod = excluded.source_mod,
                  record_type = excluded.record_type, field = excluded.field, source_key = excluded.source_key,
                  native_form_id = excluded.native_form_id, editor_id = excluded.editor_id
    WHERE (original, translation, source_mod, record_type, field, source_key, native_form_id, editor_id) <>
          (excluded.original, excluded.translation, excluded.source_mod, excluded.record_type, excluded.field,
           excluded.source_key, excluded.native_form_id, excluded.editor_id)
"""
_SELECT_ACTIVE = """
    SELECT name FROM main_libraries WHERE (game, source_locale, target_locale) = (?, ?, ?) AND active = 1
"""

_Checkpoint = Callable[[], None]
_Commit = Callable[[_Checkpoint], object]
_EntryKeys = tuple[EntryKey, ...]
_BrowseRow = tuple[str, str, str]
_HistoryRow = tuple[str, str, str, str, str, str, str, str]


class DictionaryLibraryError(RuntimeError):
    """A stored dictionary library could not be read or written."""


def _noop() -> None:
    pass


def _immediate(mutation: _Checkpoint) -> None:
    mutation()


def default_library_dir() -> Path:
    return Path(get_data_dir()) / "translation_memory"


def _token(value: str, label: str) -> str:
    token = value.strip()
    if not token:
        raise ValueError(f"{label} must not be empty")
    return token


@contextmanager
def _database(path: Path, *, write: bool = False) -> Iterator[sqlite3.Connection | None]:
    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
    elif not path.exists():
        yield None
        return
    try:
        mode = "rwc" if write else "ro"
        with closing(sqlite3.connect(sqlite_uri(path, mode=mode), uri=True, isolation_level=None, timeout=30)) as db:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            application_id = db.execute("PRAGMA application_id").fetchone()[0]
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if (application_id, version) == (0, 0) and not db.execute("SELECT 1 FROM sqlite_schema LIMIT 1").fetchone():
                if not write:
                    yield None
                    return
                for sql in _SCHEMA:
                    db.execute(sql)
                db.execute(f"PRAGMA application_id = {_APPLICATION_ID}")
                version = 1
            elif application_id != _APPLICATION_ID or version not in (1, 2, _SCHEMA_VERSION):
                raise DictionaryLibraryError(f"不支持的翻译字典库格式：{path}")
            if write and version < _SCHEMA_VERSION:
                if version == 1:
                    db.execute(_CREATE_SAVE_CHECKPOINTS)
                else:
                    db.execute(
                        "ALTER TABLE saved_source_checkpoints ADD COLUMN projection_identity TEXT NOT NULL DEFAULT ''"
                    )
                db.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            yield db
    except sqlite3.Error as exc:
        raise DictionaryLibraryError(f"无法访问翻译字典库 {path}：{exc}") from exc


def _collect_records(
    entries: Iterable[TranslationEntry],
    mod_id: str,
    checkpoint: _Checkpoint = _noop,
    *,
    detect_collisions: bool = False,
    projection: DictionaryTextProjection | None = None,
) -> tuple[dict[tuple[str, str], LibraryRecord], bool]:
    records: dict[tuple[str, str], LibraryRecord] = {}
    seen: set[tuple[str, str]] | None = set() if detect_collisions else None
    collisions = False
    checkpoint()
    for entry in entries:
        checkpoint()
        if not entry.original.strip():
            continue
        eligible = bool(entry.translation.strip()) and DEFAULT_STAGE_POLICY.allows_tm_write(
            entry.stage, entry.translation, original=entry.original
        )
        if not eligible and seen is None:
            continue
        location = locate_entry(entry, mod_id)
        key = location.locator, entry.identity.original or ""
        if seen is not None:
            if key in seen:
                collisions = True
                seen = None
            else:
                seen.add(key)
        if eligible:
            translation = entry.translation
            if projection is not None:
                translation = projection.translate(entry, translation)
                if not translation:
                    # This eligible row wins over an earlier alias in source order.
                    # Preserve the stored sentence if its winning text cannot be projected.
                    records.pop(key, None)
                    continue
            records[key] = LibraryRecord.from_entry(entry, mod_id, location=location, translation=translation)
    checkpoint()
    return records, collisions


class DictionaryLibrary:
    """A personal library and named main libraries for one game and language pair."""

    def __init__(
        self,
        base_dir: Path | None = None,
        *,
        game: str = _DEFAULT_GAME,
        source_locale: str = _DEFAULT_SOURCE,
        target_locale: str = _DEFAULT_TARGET,
        projection_factory: ProjectionFactory | None = None,
    ) -> None:
        self.base_dir = native_path(base_dir if base_dir is not None else default_library_dir())
        self.game = _token(game, "game").casefold()
        self.source_locale = _token(source_locale, "source locale").casefold()
        self.target_locale = _token(target_locale, "target locale").casefold()
        self._scope = self.game, self.source_locale, self.target_locale
        self._projection_factory = projection_factory
        self._path = self.base_dir / _FILENAME

    def text_projection(self, *, to_default: bool) -> DictionaryTextProjection | None:
        """Resolve the pinned terminology once per write or apply operation."""
        return self._projection_factory(to_default=to_default) if self._projection_factory is not None else None

    def list_mains(self) -> list[str]:
        with _database(self._path) as db:
            if db is None:
                return []
            return [
                name
                for (name,) in db.execute(
                    "SELECT name FROM main_libraries WHERE (game, source_locale, target_locale) = (?, ?, ?) "
                    "ORDER BY name COLLATE NOCASE, name",
                    self._scope,
                )
            ]

    def active_main(self) -> str:
        with _database(self._path) as db:
            row = db.execute(_SELECT_ACTIVE, self._scope).fetchone() if db is not None else None
            return row[0] if row is not None else ""

    def select_main(self, name: str, *, commit: _Commit = _immediate) -> None:
        name = name.strip()
        if not name and not self._path.exists():
            return
        with _database(self._path, write=True) as db:
            assert db is not None
            query_scope = "WHERE (game, source_locale, target_locale) = (?, ?, ?)"
            db.execute("UPDATE main_libraries SET active = 0 " + query_scope + " AND active = 1", self._scope)
            if name:
                selected = db.execute(
                    "UPDATE main_libraries SET active = 1 " + query_scope + " AND name = ?", (*self._scope, name)
                )
                if not selected.rowcount:
                    raise ValueError(f"找不到主字典库：{name}")
            commit(db.commit)

    def save(
        self,
        entries: Iterable[TranslationEntry],
        mod_id: str,
        *,
        main: str = "",
        create_main: bool = False,
        checkpoint: _Checkpoint = _noop,
        commit: _Commit = _immediate,
    ) -> int:
        """Upsert eligible rows and report how many committed records changed."""
        mod_id = _token(mod_id, "mod id")
        main = main.strip()
        if create_main and not main:
            raise ValueError("主字典库名称不能为空。")
        records, _ = _collect_records(entries, mod_id, checkpoint, projection=self.text_projection(to_default=True))
        if not records and not main:
            commit(checkpoint)
            return 0

        with _database(self._path, write=True) as db:
            assert db is not None
            checkpoint()
            if main:
                inserted = db.execute(
                    "INSERT INTO main_libraries VALUES (?, ?, ?, ?, 0) ON CONFLICT DO NOTHING", (*self._scope, main)
                )
                if create_main and not inserted.rowcount:
                    raise ValueError(f"主字典库已存在：{main}")
            changed = self._upsert_records(db, main, records.values(), checkpoint)
            committed = False

            def publish() -> None:
                nonlocal committed
                checkpoint()
                db.commit()
                committed = True

            checkpoint()
            commit(publish)
            return changed if committed else 0

    def save_revision(
        self,
        *,
        project_id: str,
        variant_id: str,
        source_id: str,
        mod_id: str,
        revision: int,
        changes_since: Callable[[int], _EntryKeys | None],
        read: Callable[[_EntryKeys | None], Iterable[TranslationEntry]],
        project_revision: int | None = None,
    ) -> int:
        """Publish the saved source to the personal library and advance its checkpoint atomically.

        The callbacks must be bound to the same durable receipt. A None key selection requests all source rows,
        including rows currently ineligible for dictionary writes. Source identity must cover the locator metadata.
        """
        mod_id = _token(mod_id, "mod id")
        scope = (
            *self._scope,
            _token(project_id, "project id"),
            _token(variant_id, "variant id"),
            _token(source_id, "source id"),
            mod_id,
        )
        projection = self.text_projection(to_default=True)
        projection_id = json.dumps(projection.checkpoint_identity, ensure_ascii=False) if projection is not None else ""
        with _database(self._path, write=True) as db:
            assert db is not None
            previous = db.execute(_SELECT_SAVE_CHECKPOINT, scope).fetchone()
            keys = None
            collisions = False
            if previous is not None and previous[0] == project_revision and previous[3] == projection_id:
                _, saved_revision, collisions, _ = previous
                if revision == saved_revision:
                    return 0
                if revision > saved_revision:
                    keys = changes_since(saved_revision)
                # Colliding source rows need full source order to pick the last eligible row.
                if keys and collisions:
                    keys = None

            changed = 0
            if keys is None or keys:
                records, collisions = _collect_records(
                    read(keys), mod_id, detect_collisions=keys is None, projection=projection
                )
                changed = self._upsert_records(db, "", records.values())
            db.execute(_UPSERT_SAVE_CHECKPOINT, (*scope, project_revision, revision, collisions, projection_id))
            db.commit()
            return changed

    def _upsert_records(
        self, db: sqlite3.Connection, main: str, records: Iterable[LibraryRecord], checkpoint: _Checkpoint = _noop
    ) -> int:
        def rows():
            for record in records:
                checkpoint()
                yield (*self._scope, main, *record)

        before = db.total_changes
        db.executemany(_UPSERT_RECORD, rows())
        return db.total_changes - before

    def matcher(self) -> LibraryMatcher:
        """Index the active libraries once for an entire set of lookups."""
        matcher = LibraryMatcher()
        personal, main = self._active_records()
        for record in personal:
            matcher.add(record)
        for record in main:
            matcher.add(record, main=True)
        return matcher

    def browse(self) -> Iterator[_BrowseRow]:
        personal, main = self._active_records()
        for record in personal:
            yield record.original, record.translation, record.source_mod
        for record in main:
            yield record.original, record.translation, record.source_mod

    def lookup_rows(self) -> Iterator[_BrowseRow]:
        """Read bilingual examples across this partition, including unselected main libraries."""
        with _database(self._path) as db:
            if db is not None:
                yield from db.execute(
                    "SELECT original, translation, source_mod FROM library_records "
                    "WHERE (game, source_locale, target_locale) = (?, ?, ?) "
                    "ORDER BY library_name, locator, qualifier, source_mod_key",
                    self._scope,
                )

    def _active_records(self) -> tuple[list[LibraryRecord], list[LibraryRecord]]:
        personal, main = [], []
        with _database(self._path) as db:
            if db is not None:
                for name, *record in db.execute(
                    "SELECT library_name, locator, qualifier, original, translation, source_mod, source_mod_key, "
                    "record_type, field, source_key, native_form_id, editor_id "
                    "FROM library_records WHERE (game, source_locale, target_locale) = (?, ?, ?) "
                    "AND (library_name = '' OR library_name = (" + _SELECT_ACTIVE + ")) "
                    "ORDER BY locator, qualifier, source_mod_key",
                    (*self._scope, *self._scope),
                ):
                    (main if name else personal).append(LibraryRecord(*record))
        return personal, main


def iter_library_history(base_dir: Path | None = None) -> Iterator[_HistoryRow]:
    """Read every saved record across games, language pairs and named libraries."""
    with _database(native_path(base_dir if base_dir is not None else default_library_dir()) / _FILENAME) as db:
        if db is not None:
            for game, source, target, name, locator, qualifier, mod, mod_key, original, translation in db.execute(
                "SELECT game, source_locale, target_locale, library_name, locator, qualifier, source_mod, "
                "source_mod_key, original, translation FROM library_records "
                "ORDER BY game, source_locale, target_locale, library_name, locator, qualifier, source_mod_key"
            ):
                record_id = json.dumps([locator, qualifier, mod_key], ensure_ascii=False, separators=(",", ":"))
                yield game, source, target, name, record_id, mod, original, translation
