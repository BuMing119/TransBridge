# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Operations on one project database."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager, nullcontext
import json
import os
from pathlib import Path
import sqlite3
from tempfile import NamedTemporaryFile

from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.projects.storage_contracts import (
    ExtractedSource,
    ProjectReadToken,
    ProjectSummary,
    SaveBatch,
    SaveReceipt,
    SourceInfo,
    StoredEntry,
)
from transbridge.filesystem_paths import native_path
from transbridge.persistence.v2.ids import ProjectId, VariantId

from . import reads
from .commits import commit_changes, read_revisions
from .connection import project_connection
from .entry_codec import DEFAULT_STATE_JOIN, VISIBLE_STATE, unpack_text
from .format import ProjectStorageConflict, ProjectStoreError
from .receipts import decode_receipt, receipt_row
from .writes import insert_project


class ProjectStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def create(
        self,
        project_id: ProjectId,
        name: str,
        variant_id: VariantId,
        variant_name: str,
        sources: Iterable[ExtractedSource] = (),
    ) -> ProjectSummary:
        def populate(connection: sqlite3.Connection) -> ProjectSummary:
            insert_project(connection, project_id, name, variant_id, variant_name, tuple(sources))
            return reads.read_summary(connection)

        return publish_project(self.path, populate)

    def summary(self) -> ProjectSummary:
        with project_connection(self.path, check_schema=True) as connection:
            return reads.read_summary(connection)

    def sources(self) -> tuple[SourceInfo, ...]:
        with project_connection(self.path) as connection:
            return reads.read_sources(connection)

    def read_page(
        self,
        variant_id: VariantId,
        *,
        namespace: SourceNamespace | None = None,
        offset: int = 0,
        limit: int = 100,
        token: ProjectReadToken | None = None,
        include_removed: bool = False,
        reading_order: bool = False,
    ) -> tuple[StoredEntry, ...]:
        if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 1000:
            raise ValueError("project pages require a non-negative offset and 1–1000 entries")
        with project_connection(self.path) as connection:
            if token is not None and token != _read_token(connection, variant_id):
                raise ProjectStorageConflict("project or Variant changed while reading its entry range")
            return reads.read_page(
                connection,
                variant_id,
                namespace,
                offset,
                limit,
                include_removed=include_removed,
                reading_order=reading_order,
            )

    def read_token(self, variant_id: VariantId) -> ProjectReadToken:
        with project_connection(self.path) as connection:
            return _read_token(connection, variant_id)

    def iter_entries(
        self,
        variant_id: VariantId,
        *,
        namespace: SourceNamespace | None = None,
        token: ProjectReadToken | None = None,
        include_removed: bool = False,
        reading_order: bool = False,
    ):
        token = token or self.read_token(variant_id)
        with project_connection(self.path) as connection:
            if token != _read_token(connection, variant_id):
                raise ProjectStorageConflict("project or Variant changed before reading its entry range")
            namespaces = (
                tuple(
                    SourceNamespace(row[0])
                    for row in connection.execute("SELECT namespace FROM project_source ORDER BY ordinal")
                )
                if namespace is None
                else (namespace,)
            )
        for source_namespace in namespaces:
            after = -1
            while True:
                with project_connection(self.path) as connection:
                    if token != _read_token(connection, variant_id):
                        raise ProjectStorageConflict("project or Variant changed while reading its entry range")
                    page, after = reads.read_cursor_page(
                        connection,
                        variant_id,
                        source_namespace,
                        after,
                        include_removed=include_removed,
                        reading_order=reading_order,
                    )
                yield from page
                if len(page) < 1000:
                    break

    def iter_source_entries(self, variant_id: VariantId, namespace: SourceNamespace, *, include_removed=False):
        with project_connection(self.path) as connection:
            yield from reads.iter_source_entries(connection, variant_id, namespace, include_removed=include_removed)

    def scope_statistics(self, token: ProjectReadToken, namespace: SourceNamespace, changes=()):
        """Read grouped facts and reconcile sparse uncommitted fields against the same revision."""
        with project_connection(self.path) as connection:
            if token != _read_token(connection, token.variant_id):
                raise ProjectStorageConflict("project or Variant changed while counting source entries")
            facts = Counter()
            for row in connection.execute(
                f"""
                SELECT e.context, COALESCE(vs.stage, ds.stage, 0) AS stage,
                    (COALESCE(vs.stage, ds.stage, 0) >= 1
                        AND COALESCE(vs.translation, ds.translation, '') <> '') AS translated,
                    COALESCE(vs.labels, ds.labels, '[]') AS labels, COUNT(*) AS count
                FROM project_source AS ps JOIN source_entry AS e ON e.source_id = ps.source_id
                LEFT JOIN entry_state AS vs ON vs.variant_id = ? AND vs.entry_id = e.id
                {DEFAULT_STATE_JOIN}
                WHERE ps.namespace = ? AND {VISIBLE_STATE}
                GROUP BY 1, 2, 3, 4
                """,
                (token.variant_id.value, namespace.value),
            ):
                context = "" if row["context"] is None else unpack_text(row["context"])
                facts[context, row["stage"], bool(row["translated"]), tuple(json.loads(row["labels"]))] += row["count"]
            relevant = {entry.entry_key: entry for entry in changes if entry.entry_key.namespace == namespace}
            for item in reads.read_entries(connection, token.variant_id, relevant):
                for state, delta in ((item.state, -1), (relevant[item.state.entry_key], 1)):
                    if not state.tombstone:
                        stage = state.stage.value
                        facts[
                            item.snapshot.context or "", stage, bool(stage >= 1 and state.translation), state.labels
                        ] += delta
            return +facts

    def read_entries(
        self, variant_id: VariantId, keys: Iterable[EntryKey], *, token: ProjectReadToken | None = None
    ) -> tuple[StoredEntry, ...]:
        with project_connection(self.path) as connection:
            if token is not None and token != _read_token(connection, variant_id):
                raise ProjectStorageConflict("project or Variant changed while reading its entries")
            return reads.read_entries(connection, variant_id, keys)

    def commit(self, batch: SaveBatch) -> SaveReceipt:
        if not batch.changes and batch.operation_id is None:
            with project_connection(self.path) as connection:
                project_revision, variant_revision = read_revisions(connection, batch)
                return SaveReceipt(
                    batch.project_id,
                    batch.variant_id,
                    project_revision,
                    variant_revision,
                    capture_token=batch.capture_token,
                )
        with project_connection(self.path, write=True) as connection:
            return commit_changes(connection, batch)

    def receipt(self, operation_id: str) -> SaveReceipt | None:
        with project_connection(self.path) as connection:
            row = receipt_row(connection, operation_id)
            return None if row is None else decode_receipt(row)


def publish_project[T](
    path: Path,
    populate: Callable[[sqlite3.Connection], T],
    *,
    publication: AbstractContextManager[None] | None = None,
) -> T:
    path = native_path(path)
    if path.exists():
        raise ProjectStorageConflict("project database already exists")
    try:
        staging_directory = path.parent if publication is None else path.parent.parent
        staging_directory.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(prefix=".project-stage-", dir=staging_directory, delete=False) as stage:
            staging_path = Path(stage.name)
        try:
            with project_connection(staging_path, write=True, create=True) as connection:
                result = populate(connection)
            with publication if publication is not None else nullcontext():
                path.parent.mkdir(parents=True, exist_ok=True)
                os.link(staging_path, path)
        finally:
            staging_path.unlink(missing_ok=True)
    except FileExistsError as exc:
        raise ProjectStorageConflict("project database already exists") from exc
    except OSError as exc:
        raise ProjectStoreError(f"Cannot publish project database: {exc}") from exc
    return result


def _read_token(connection: sqlite3.Connection, variant_id: VariantId) -> ProjectReadToken:
    row = connection.execute("SELECT identity, revision FROM project").fetchone()
    variant = reads.require_variant(connection, variant_id)
    return ProjectReadToken(ProjectId(row["identity"]), variant_id, row["revision"], variant["revision"])
