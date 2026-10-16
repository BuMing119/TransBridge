"""Immutable build storage usable inside a guarded draft transaction."""

from __future__ import annotations

from collections.abc import Callable
import sqlite3

from transbridge.application.terminology.errors import DigestCollisionError, RepositoryConflictError
from transbridge.application.terminology.models import BuildResult, BuildResultRef

from .codec import dumps


class BuildStore:
    def __init__(self, connection: sqlite3.Connection, project_id: str, decode: Callable) -> None:
        self._connection = connection
        self._project_id = project_id
        self._decode = decode

    def put(self, result: BuildResult) -> BuildResultRef:
        if result.project_id != self._project_id:
            raise RepositoryConflictError("build belongs to another Project database")
        row = self._connection.execute(
            "SELECT payload_json FROM builds WHERE build_key = ?", (result.ref.build_key,)
        ).fetchone()
        if row is not None:
            existing = self._decode(str(row["payload_json"]), BuildResult)
            if existing != result:
                raise DigestCollisionError(result.ref.build_key)
            return existing.ref
        self._connection.execute(
            "INSERT INTO builds(build_key, content_digest, project_id, variant_id, payload_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (result.ref.build_key, result.ref.content_digest, result.project_id, result.variant_id, dumps(result)),
        )
        for table, items, identity in (
            ("build_evidence", result.evidence, "evidence_id"),
            ("build_candidates", result.candidates, "candidate_id"),
            ("build_conflicts", result.conflicts, "conflict_group_id"),
        ):
            self._connection.executemany(
                f"INSERT INTO {table}(build_key, stable_id, payload_json) VALUES (?, ?, ?)",
                ((result.ref.build_key, getattr(item, identity), dumps(item)) for item in items),
            )
        return result.ref
