"""Lightweight profile selection reads and bounded, verified revision reuse."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
import sqlite3
from typing import Any

from transbridge.application.terminology_profiles.models import (
    ProfileState,
    ProfileSummary,
    PublishedTerminologyProfile,
)


class ProfileReadStore:
    """Owned by one repository/connection; never share cached content across projects."""

    def __init__(self, connection: sqlite3.Connection, lock, decode: Callable[[Any], PublishedTerminologyProfile]):
        self._connection = connection
        self._lock = lock
        self._decode = decode
        self._token: object = None
        self._published: OrderedDict[tuple[str, int], PublishedTerminologyProfile] = OrderedDict()

    def token(self) -> tuple[int, int, bool]:
        with self._lock:
            return (
                int(self._connection.execute("PRAGMA data_version").fetchone()[0]),
                self._connection.total_changes,
                self._connection.in_transaction,
            )

    def summaries(self, project_id: str, *, include_archived: bool = False) -> tuple[ProfileSummary, ...]:
        sql = (
            "SELECT profile_id, project_id, name, state, draft_revision, latest_published_revision "
            "FROM terminology_profiles WHERE project_id = ?"
        )
        parameters: tuple[object, ...] = (project_id,)
        if not include_archived:
            sql += " AND state = ?"
            parameters += (ProfileState.ACTIVE.value,)
        sql += " ORDER BY name COLLATE NOCASE, profile_id"
        with self._lock:
            rows = self._connection.execute(sql, parameters).fetchall()
        return tuple(ProfileSummary(row[0], row[1], row[2], ProfileState(row[3]), row[4], row[5]) for row in rows)

    def published(self, profile_id: str, revision: int) -> PublishedTerminologyProfile | None:
        with self._lock:
            for _ in range(3):
                token = self.token()
                if token != self._token:
                    self._published.clear()
                    self._token = token
                key = (profile_id, revision)
                result = self._published.get(key)
                if result is None:
                    row = self._connection.execute(
                        "SELECT profile_id, project_id, revision, name, content_digest, content_json, published_at "
                        "FROM terminology_profile_revisions WHERE profile_id = ? AND revision = ?",
                        key,
                    ).fetchone()
                    result = None if row is None else self._decode(row)
                if self.token() != token:
                    continue
                if result is not None:
                    self._published[key] = result
                    self._published.move_to_end(key)
                    while len(self._published) > 2:
                        self._published.popitem(last=False)
                return result
        raise ValueError("terminology profile changed repeatedly while reading; retry")

    def selected(self, project_id: str, variant_id: str) -> PublishedTerminologyProfile | None:
        with self._lock:
            for _ in range(3):
                token = self.token()
                row = self._connection.execute(
                    "SELECT selections.profile_id, selections.revision FROM terminology_profile_selections selections "
                    "JOIN terminology_profiles profiles ON profiles.profile_id = selections.profile_id "
                    "WHERE selections.project_id = ? AND selections.variant_id = ? "
                    "AND profiles.project_id = ? AND profiles.state = ?",
                    (project_id, variant_id, project_id, ProfileState.ACTIVE.value),
                ).fetchone()
                result = None if row is None else self.published(str(row[0]), int(row[1]))
                if self.token() == token:
                    return result
        raise ValueError("terminology profile selection changed repeatedly while reading; retry")
