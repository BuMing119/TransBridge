"""SQLite persistence for base-game localization terminology profiles."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
import json
import sqlite3
from threading import RLock
from typing import Any

from transbridge.application.terminology_profiles.models import (
    ProfileEntryOverride,
    ProfileOccurrenceBinding,
    ProfileState,
    ProfileTermMapping,
    PublishedTerminologyProfile,
    TerminologyProfile,
    TerminologyProfileContent,
    TerminologyProfileSelection,
)

_CODEC_VERSION = 1


class SqliteTerminologyProfileRepository:
    """Implements the profile repository protocol on an initialized connection."""

    def __init__(self, connection: sqlite3.Connection, *, lock=None) -> None:
        self._connection = connection
        self._lock = lock or RLock()
        connection.execute("PRAGMA foreign_keys = ON")
        if int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
            raise ValueError("terminology profile storage requires SQLite foreign keys")

    def list_profiles(self, project_id: str, *, include_archived: bool = False) -> tuple[TerminologyProfile, ...]:
        sql = (
            "SELECT profile_id, project_id, name, state, draft_revision, draft_json, "
            "latest_published_revision, created_at, updated_at FROM terminology_profiles WHERE project_id = ?"
        )
        parameters: tuple[object, ...] = (project_id,)
        if not include_archived:
            sql += " AND state = ?"
            parameters += (ProfileState.ACTIVE.value,)
        sql += " ORDER BY name COLLATE NOCASE, profile_id"
        with self._lock:
            rows = self._connection.execute(sql, parameters).fetchall()
        return tuple(_profile_from_row(row) for row in rows)

    def get_profile(self, profile_id: str) -> TerminologyProfile | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT profile_id, project_id, name, state, draft_revision, draft_json, "
                "latest_published_revision, created_at, updated_at FROM terminology_profiles WHERE profile_id = ?",
                (profile_id,),
            ).fetchone()
        return None if row is None else _profile_from_row(row)

    def insert_profile(self, profile: TerminologyProfile) -> None:
        try:
            with self._lock, self._write_transaction():
                self._connection.execute(
                    "INSERT INTO terminology_profiles("
                    "profile_id, project_id, name, state, draft_revision, draft_json, "
                    "latest_published_revision, created_at, updated_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        profile.profile_id,
                        profile.project_id,
                        profile.name,
                        profile.state.value,
                        profile.draft_revision,
                        _dump_content(profile.draft),
                        profile.latest_published_revision,
                        profile.created_at,
                        profile.updated_at,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("profile already exists or violates storage constraints") from exc

    def rename_profile(self, profile_id: str, name: str, *, updated_at: str) -> TerminologyProfile:
        try:
            with self._lock, self._write_transaction():
                profile = self._require_profile(profile_id)
                updated = replace(profile, name=name, updated_at=updated_at)
                self._connection.execute(
                    "UPDATE terminology_profiles SET name = ?, updated_at = ? WHERE profile_id = ?",
                    (updated.name, updated.updated_at, profile_id),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("profile name already exists") from exc
        return updated

    def save_draft(
        self,
        profile_id: str,
        content: TerminologyProfileContent,
        *,
        expected_revision: int,
        updated_at: str,
    ) -> TerminologyProfile:
        with self._lock, self._write_transaction():
            current = self._require_profile(profile_id)
            if current.draft_revision != expected_revision:
                raise ValueError("stale draft revision")
            updated = replace(
                current,
                draft=content,
                draft_revision=expected_revision + 1,
                updated_at=updated_at,
            )
            cursor = self._connection.execute(
                "UPDATE terminology_profiles SET draft_revision = ?, draft_json = ?, updated_at = ? "
                "WHERE profile_id = ? AND draft_revision = ?",
                (
                    updated.draft_revision,
                    _dump_content(updated.draft),
                    updated.updated_at,
                    profile_id,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("stale draft revision")
        return updated

    def set_archived(self, profile_id: str, *, archived: bool, updated_at: str) -> TerminologyProfile:
        with self._lock, self._write_transaction():
            current = self._require_profile(profile_id)
            updated = replace(
                current,
                state=ProfileState.ARCHIVED if archived else ProfileState.ACTIVE,
                updated_at=updated_at,
            )
            self._connection.execute(
                "UPDATE terminology_profiles SET state = ?, updated_at = ? WHERE profile_id = ?",
                (updated.state.value, updated.updated_at, profile_id),
            )
            if archived:
                self._connection.execute(
                    "DELETE FROM terminology_profile_selections WHERE profile_id = ?",
                    (profile_id,),
                )
        return updated

    def insert_published(
        self,
        revision: PublishedTerminologyProfile,
        *,
        expected_draft_revision: int,
    ) -> None:
        try:
            with self._lock, self._write_transaction():
                profile = self._require_profile(revision.profile_id)
                expected = (profile.latest_published_revision or 0) + 1
                if revision.revision != expected:
                    raise ValueError("published revision already exists or is not next")
                if revision.project_id != profile.project_id:
                    raise ValueError("published revision belongs to another Project")
                if profile.state is ProfileState.ARCHIVED:
                    raise ValueError("archived profile cannot be published")
                if (
                    profile.draft_revision != expected_draft_revision
                    or profile.draft.content_digest != revision.content_digest
                ):
                    raise ValueError("profile draft changed before publish")
                self._connection.execute(
                    "INSERT INTO terminology_profile_revisions("
                    "profile_id, project_id, revision, name, content_digest, content_json, published_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        revision.profile_id,
                        revision.project_id,
                        revision.revision,
                        revision.name,
                        revision.content_digest,
                        _dump_content(revision.content),
                        revision.published_at,
                    ),
                )
                cursor = self._connection.execute(
                    "UPDATE terminology_profiles SET latest_published_revision = ?, updated_at = ? "
                    "WHERE profile_id = ? AND latest_published_revision IS ?",
                    (
                        revision.revision,
                        revision.published_at,
                        revision.profile_id,
                        profile.latest_published_revision,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ValueError("published revision changed concurrently")
                self._connection.execute(
                    "UPDATE terminology_profile_selections SET revision = ?, selected_at = ? WHERE profile_id = ?",
                    (revision.revision, revision.published_at, revision.profile_id),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("published revision already exists or violates storage constraints") from exc

    def get_published(self, profile_id: str, revision: int) -> PublishedTerminologyProfile | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT profile_id, project_id, revision, name, content_digest, content_json, published_at "
                "FROM terminology_profile_revisions WHERE profile_id = ? AND revision = ?",
                (profile_id, revision),
            ).fetchone()
        return None if row is None else _published_from_row(row)

    def get_selection(self, project_id: str, variant_id: str) -> TerminologyProfileSelection | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT project_id, variant_id, profile_id, revision, selected_at "
                "FROM terminology_profile_selections WHERE project_id = ? AND variant_id = ?",
                (project_id, variant_id),
            ).fetchone()
        return None if row is None else _selection_from_row(row)

    def set_selection(self, selection: TerminologyProfileSelection) -> None:
        try:
            with self._lock, self._write_transaction():
                row = self._connection.execute(
                    "SELECT revisions.project_id, profiles.state, profiles.latest_published_revision "
                    "FROM terminology_profile_revisions AS revisions "
                    "JOIN terminology_profiles AS profiles ON profiles.profile_id = revisions.profile_id "
                    "WHERE revisions.profile_id = ? AND revisions.revision = ?",
                    (selection.profile_id, selection.revision),
                ).fetchone()
                if (
                    row is None
                    or str(row[0]) != selection.project_id
                    or str(row[1]) != ProfileState.ACTIVE.value
                    or int(row[2]) != selection.revision
                ):
                    raise ValueError("selected profile revision is unavailable")
                self._connection.execute(
                    "INSERT INTO terminology_profile_selections("
                    "project_id, variant_id, profile_id, revision, selected_at"
                    ") VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(project_id, variant_id) DO UPDATE SET "
                    "profile_id = excluded.profile_id, revision = excluded.revision, "
                    "selected_at = excluded.selected_at",
                    (
                        selection.project_id,
                        selection.variant_id,
                        selection.profile_id,
                        selection.revision,
                        selection.selected_at,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("selected profile revision is unavailable") from exc

    def clear_selection(self, project_id: str, variant_id: str) -> None:
        with self._lock, self._write_transaction():
            self._connection.execute(
                "DELETE FROM terminology_profile_selections WHERE project_id = ? AND variant_id = ?",
                (project_id, variant_id),
            )

    def clear_profile_selections(self, profile_id: str) -> None:
        with self._lock, self._write_transaction():
            self._connection.execute(
                "DELETE FROM terminology_profile_selections WHERE profile_id = ?",
                (profile_id,),
            )

    def _require_profile(self, profile_id: str) -> TerminologyProfile:
        row = self._connection.execute(
            "SELECT profile_id, project_id, name, state, draft_revision, draft_json, "
            "latest_published_revision, created_at, updated_at FROM terminology_profiles WHERE profile_id = ?",
            (profile_id,),
        ).fetchone()
        if row is None:
            raise ValueError("profile not found")
        return _profile_from_row(row)

    @contextmanager
    def _write_transaction(self) -> Iterator[None]:
        if int(self._connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
            raise ValueError("terminology profile storage requires SQLite foreign keys")
        nested = self._connection.in_transaction
        if nested:
            self._connection.execute("SAVEPOINT terminology_profile_write")
        else:
            self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            if nested:
                self._connection.execute("ROLLBACK TO terminology_profile_write")
                self._connection.execute("RELEASE terminology_profile_write")
            else:
                self._connection.rollback()
            raise
        else:
            if nested:
                self._connection.execute("RELEASE terminology_profile_write")
            else:
                self._connection.commit()


def _profile_from_row(row: sqlite3.Row | tuple[Any, ...]) -> TerminologyProfile:
    return TerminologyProfile(
        profile_id=str(row[0]),
        project_id=str(row[1]),
        name=str(row[2]),
        state=ProfileState(str(row[3])),
        draft_revision=_integer(row[4], "draft revision"),
        draft=_load_content(str(row[5])),
        latest_published_revision=None if row[6] is None else _integer(row[6], "published revision"),
        created_at=str(row[7]),
        updated_at=str(row[8]),
    )


def _published_from_row(row: sqlite3.Row | tuple[Any, ...]) -> PublishedTerminologyProfile:
    return PublishedTerminologyProfile(
        profile_id=str(row[0]),
        project_id=str(row[1]),
        revision=_integer(row[2], "published revision"),
        name=str(row[3]),
        content_digest=str(row[4]),
        content=_load_content(str(row[5])),
        published_at=str(row[6]),
    )


def _selection_from_row(row: sqlite3.Row | tuple[Any, ...]) -> TerminologyProfileSelection:
    return TerminologyProfileSelection(
        project_id=str(row[0]),
        variant_id=str(row[1]),
        profile_id=str(row[2]),
        revision=_integer(row[3], "selected revision"),
        selected_at=str(row[4]),
    )


def _dump_content(content: TerminologyProfileContent) -> str:
    payload = {
        "schema_version": _CODEC_VERSION,
        "value": {
            "bindings": [
                {
                    "end": item.end,
                    "entry_key": item.entry_key,
                    "expected_text": item.expected_text,
                    "start": item.start,
                    "term_key": item.term_key,
                }
                for item in content.bindings
            ],
            "mappings": [
                {
                    "base_translation": item.base_translation,
                    "original": item.original,
                    "plugin_id": item.plugin_id,
                    "scope_kind": item.scope_kind,
                    "translation": item.translation,
                }
                for item in content.mappings
            ],
            "overrides": [{"entry_key": item.entry_key, "translation": item.translation} for item in content.overrides],
        },
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _load_content(payload_json: str) -> TerminologyProfileContent:
    try:
        payload = json.loads(payload_json)
        _exact_keys(payload, {"schema_version", "value"}, "profile payload")
        if _integer(payload["schema_version"], "schema version") != _CODEC_VERSION:
            raise ValueError("unsupported terminology profile payload schema")
        value = payload["value"]
        _exact_keys(value, {"bindings", "mappings", "overrides"}, "profile content")
        mappings = tuple(_decode_mapping(item) for item in _list(value["mappings"], "mappings"))
        overrides = tuple(_decode_override(item) for item in _list(value["overrides"], "overrides"))
        bindings = tuple(_decode_binding(item) for item in _list(value["bindings"], "bindings"))
        return TerminologyProfileContent(mappings=mappings, overrides=overrides, bindings=bindings)
    except (AttributeError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid terminology profile payload") from exc


def _decode_mapping(value: Any) -> ProfileTermMapping:
    _exact_keys(
        value,
        {"base_translation", "original", "plugin_id", "scope_kind", "translation"},
        "profile mapping",
    )
    plugin_id = value["plugin_id"]
    if plugin_id is not None:
        plugin_id = _string(plugin_id, "plugin ID")
    return ProfileTermMapping(
        original=_string(value["original"], "term original"),
        translation=_string(value["translation"], "profile translation"),
        base_translation=_string(value["base_translation"], "base translation", allow_empty=True),
        scope_kind=_string(value["scope_kind"], "scope kind"),
        plugin_id=plugin_id,
    )


def _decode_override(value: Any) -> ProfileEntryOverride:
    _exact_keys(value, {"entry_key", "translation"}, "profile override")
    return ProfileEntryOverride(
        entry_key=_string(value["entry_key"], "entry key"),
        translation=_string(value["translation"], "override translation"),
    )


def _decode_binding(value: Any) -> ProfileOccurrenceBinding:
    _exact_keys(value, {"end", "entry_key", "expected_text", "start", "term_key"}, "profile binding")
    return ProfileOccurrenceBinding(
        entry_key=_string(value["entry_key"], "entry key"),
        term_key=_string(value["term_key"], "term key"),
        start=_integer(value["start"], "binding start"),
        end=_integer(value["end"], "binding end"),
        expected_text=_string(value["expected_text"], "binding expected text"),
    )


def _exact_keys(value: Any, expected: set[str], label: str) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(f"{label} has unexpected fields")


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    return value


def _string(value: Any, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValueError(f"{label} must be a string")
    return value


def _integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


__all__ = ["SqliteTerminologyProfileRepository"]
