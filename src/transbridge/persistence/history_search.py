"""SQLite-backed, atomically replaceable history-search projection."""

from __future__ import annotations

from contextlib import closing
from dataclasses import asdict
import json
import os
from pathlib import Path
import sqlite3
from threading import RLock
from uuid import uuid4

from transbridge.application.history_search.models import (
    HistoryDiagnostic,
    HistoryEntryKind,
    HistoryQuery,
    HistorySearchHit,
    HistorySearchPage,
    HistorySearchScope,
    HistorySearchScopeKind,
    HistorySourceRef,
    HistorySourceType,
    IndexStatus,
    SourceRecord,
    normalize_search_text,
)

_SCHEMA_VERSION = "2"
_FETCH_LIMIT = 5000


class SqliteHistorySearchIndex:
    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path).resolve(strict=False)
        self._lock = RLock()

    def replace(
        self,
        records: tuple[SourceRecord, ...],
        diagnostics: tuple[HistoryDiagnostic, ...],
        *,
        built_at: str,
        cancellation=None,
    ) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        try:
            connection = sqlite3.connect(temporary)
            try:
                self._create_schema(connection)
                batch = []
                for record in records:
                    _raise_if_cancelled(cancellation)
                    batch.append(self._record_row(record))
                    if len(batch) >= 500:
                        self._insert_records(connection, batch)
                        batch.clear()
                if batch:
                    self._insert_records(connection, batch)
                connection.executemany(
                    "INSERT INTO search_scopes(kind, scope_id, label) VALUES (?, ?, ?)",
                    _scope_rows(records),
                )
                connection.executemany(
                    "INSERT INTO diagnostics(code, message, source) VALUES (?, ?, ?)",
                    ((item.code, item.message, item.source) for item in diagnostics),
                )
                connection.executemany(
                    "INSERT INTO metadata(key, value) VALUES (?, ?)",
                    (
                        ("schema_version", _SCHEMA_VERSION),
                        ("built_at", built_at),
                        ("record_count", str(len(records))),
                    ),
                )
                connection.commit()
            finally:
                connection.close()
            _raise_if_cancelled(cancellation)
            with self._lock:
                os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def query(self, request: HistoryQuery) -> HistorySearchPage:
        keyword = normalize_search_text(request.keyword)
        if not self.path.exists():
            return HistorySearchPage((), 0)
        clauses: list[str] = []
        parameters: list[object] = []
        if keyword:
            pattern = f"%{_escape_like(keyword)}%"
            clauses.append("(normalized_original LIKE ? ESCAPE '\\' OR normalized_translation LIKE ? ESCAPE '\\')")
            parameters.extend((pattern, pattern))
        if request.kind is not None:
            clauses.append("kind = ?")
            parameters.append(request.kind.value)
        if request.scope is not None:
            column = "project_id" if request.scope.kind is HistorySearchScopeKind.PROJECT else "dictionary_id"
            clauses.append(f"{column} = ?")
            parameters.append(request.scope.scope_id)
        where_clause = " WHERE " + " AND ".join(clauses) if clauses else ""
        sql = (
            "SELECT kind, original, translation, source_locale, target_locale, scope_key, status, source_json "
            "FROM records" + where_clause + " ORDER BY normalized_original, normalized_translation, source_json LIMIT ?"
        )
        parameters.append(_FETCH_LIMIT)
        with self._lock, closing(sqlite3.connect(self.path)) as connection:
            rows = connection.execute(sql, parameters).fetchall()
        hits = _merge_rows(rows)
        page_items = hits[request.offset : request.offset + request.limit]
        return HistorySearchPage(tuple(page_items), len(hits), len(rows) == _FETCH_LIMIT)

    def scopes(self) -> tuple[HistorySearchScope, ...]:
        if not self.path.exists():
            return ()
        try:
            with self._lock, closing(sqlite3.connect(self.path)) as connection:
                rows = connection.execute(
                    "SELECT kind, scope_id, label FROM search_scopes ORDER BY kind DESC, label COLLATE NOCASE, scope_id"
                ).fetchall()
            return tuple(
                HistorySearchScope(HistorySearchScopeKind(kind), scope_id, label) for kind, scope_id, label in rows
            )
        except (OSError, sqlite3.Error, TypeError, ValueError):
            return ()

    def status(self) -> IndexStatus:
        if not self.path.exists():
            return IndexStatus(False)
        try:
            with self._lock, closing(sqlite3.connect(self.path)) as connection:
                metadata = dict(connection.execute("SELECT key, value FROM metadata").fetchall())
                diagnostics = tuple(
                    HistoryDiagnostic(str(code), str(message), str(source))
                    for code, message, source in connection.execute(
                        "SELECT code, message, source FROM diagnostics ORDER BY rowid"
                    )
                )
            if metadata.get("schema_version") != _SCHEMA_VERSION:
                return IndexStatus(False)
            return IndexStatus(
                True,
                int(metadata.get("record_count", "0")),
                metadata.get("built_at"),
                diagnostics,
            )
        except (OSError, sqlite3.Error, TypeError, ValueError):
            return IndexStatus(False)

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            PRAGMA journal_mode = DELETE;
            CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE diagnostics(code TEXT NOT NULL, message TEXT NOT NULL, source TEXT NOT NULL);
            CREATE TABLE search_scopes(
                kind TEXT NOT NULL,
                scope_id TEXT NOT NULL,
                label TEXT NOT NULL,
                PRIMARY KEY(kind, scope_id)
            );
            CREATE TABLE records(
                kind TEXT NOT NULL,
                original TEXT NOT NULL,
                translation TEXT NOT NULL,
                normalized_original TEXT NOT NULL,
                normalized_translation TEXT NOT NULL,
                source_locale TEXT NOT NULL,
                target_locale TEXT NOT NULL,
                scope_key TEXT NOT NULL,
                status TEXT NOT NULL,
                project_id TEXT NOT NULL,
                dictionary_id TEXT NOT NULL,
                source_json TEXT NOT NULL
            );
            CREATE INDEX records_original_idx ON records(normalized_original);
            CREATE INDEX records_translation_idx ON records(normalized_translation);
            CREATE INDEX records_kind_idx ON records(kind);
            CREATE INDEX records_project_idx ON records(project_id);
            CREATE INDEX records_dictionary_idx ON records(dictionary_id);
            """
        )

    @staticmethod
    def _record_row(record: SourceRecord) -> tuple[str, ...]:
        return (
            record.kind.value,
            record.original,
            record.translation,
            record.normalized_original,
            record.normalized_translation,
            record.source_locale,
            record.target_locale,
            record.scope_key,
            record.status,
            record.source.project_id or "",
            record.source.dictionary_id or "",
            json.dumps(asdict(record.source), ensure_ascii=False, sort_keys=True),
        )

    @staticmethod
    def _insert_records(connection: sqlite3.Connection, rows: list[tuple[str, ...]]) -> None:
        connection.executemany("INSERT INTO records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)


def _scope_rows(records: tuple[SourceRecord, ...]) -> tuple[tuple[str, str, str], ...]:
    scopes: dict[tuple[str, str], str] = {}
    for record in records:
        source = record.source
        candidates = (
            (HistorySearchScopeKind.PROJECT, source.project_id, source.project_name),
            (HistorySearchScopeKind.DICTIONARY, source.dictionary_id, source.dictionary_id),
        )
        for kind, scope_id, preferred_label in candidates:
            if not scope_id:
                continue
            label = preferred_label or scope_id
            key = (kind.value, scope_id)
            existing = scopes.get(key)
            if existing is None or label.casefold() < existing.casefold():
                scopes[key] = label
    return tuple((kind, scope_id, label) for (kind, scope_id), label in sorted(scopes.items()))


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _merge_rows(rows: list[tuple]) -> list[HistorySearchHit]:
    locale_candidates: dict[tuple[str, ...], set[tuple[str, str]]] = {}
    normalized_rows = []
    for row in rows:
        kind, original, translation, source_locale, target_locale, scope_key, status, source_json = row
        normalized_original = normalize_search_text(str(original))
        normalized_translation = normalize_search_text(str(translation))
        locale = (normalize_search_text(str(source_locale)), normalize_search_text(str(target_locale)))
        base_key = (str(kind), normalized_original, normalized_translation, str(scope_key))
        locale_key = (str(kind), normalized_original, str(scope_key))
        if locale != ("", ""):
            locale_candidates.setdefault(locale_key, set()).add(locale)
        normalized_rows.append((base_key, locale_key, locale, original, translation, status, source_json))

    grouped: dict[tuple[str, ...], list[tuple]] = {}
    for base_key, locale_key, locale, original, translation, status, source_json in normalized_rows:
        if locale == ("", "") and len(locale_candidates.get(locale_key, ())) == 1:
            locale = next(iter(locale_candidates[locale_key]))
        key = (
            base_key[0],
            base_key[1],
            base_key[2],
            locale[0],
            locale[1],
            base_key[3],
        )
        grouped.setdefault(key, []).append((original, translation, status, source_json))
    translations_by_original: dict[tuple[str, ...], set[str]] = {}
    for key in grouped:
        conflict_key = (key[0], key[1], key[3], key[4], key[5])
        translations_by_original.setdefault(conflict_key, set()).add(key[2])
    conflict_keys = {key for key, translations in translations_by_original.items() if len(translations) > 1}
    hits: list[HistorySearchHit] = []
    for key, values in grouped.items():
        first = values[0]
        unique_sources = {
            (source.source_type.value, source.source_id): source
            for source in (_decode_source(item[3]) for item in values)
        }
        sources = tuple(sorted(unique_sources.values(), key=lambda item: item.label.casefold()))
        statuses = tuple(dict.fromkeys(str(item[2]) for item in values if str(item[2]).strip()))
        hits.append(
            HistorySearchHit(
                HistoryEntryKind(key[0]),
                str(first[0]),
                str(first[1]),
                key[3],
                key[4],
                key[5],
                " / ".join(statuses),
                sources,
                (key[0], key[1], key[3], key[4], key[5]) in conflict_keys,
            )
        )
    hits.sort(
        key=lambda item: (
            normalize_search_text(item.original),
            item.kind.value,
            normalize_search_text(item.translation),
        )
    )
    return hits


def _decode_source(payload: str) -> HistorySourceRef:
    data = json.loads(payload)
    data["source_type"] = HistorySourceType(data["source_type"])
    data["details"] = tuple(tuple(item) for item in data.get("details", ()))
    return HistorySourceRef(**data)


def _raise_if_cancelled(cancellation) -> None:
    if cancellation is None:
        return
    method = getattr(cancellation, "raise_if_cancelled", None)
    if callable(method):
        method()


__all__ = ["SqliteHistorySearchIndex"]
