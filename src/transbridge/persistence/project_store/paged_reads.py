# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Build a bounded initial view and defer complete Variant materialization."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json

from transbridge.application.io.contracts import FormatId, SourceDescriptor, SourceSnapshot
from transbridge.application.projects.models import ActiveProject
from transbridge.application.projects.source_extraction import reconstructed_source_snapshot
from transbridge.application.projects.storage_contracts import ProjectReadToken, SourceInfo, StoredEntry
from transbridge.persistence.v2.deferred_variant_rows import DeferredVariantRows
from transbridge.persistence.v2.variant import SourceFingerprint, VariantAggregate, VariantSnapshot

from .connection import project_connection
from .reads import read_format_records, read_page, read_sources, require_variant
from .records import CandidateRecords, read_project, registered_source_ids, revision_marker

WORKBENCH_PAGE_SIZE = 100


@dataclass(frozen=True, slots=True)
class WorkbenchSource:
    key: str
    info: SourceInfo
    preview: tuple[StoredEntry, ...] = ()
    count: int = 0


class ProjectPagedRead:
    def __init__(self, store, token: ProjectReadToken, sources: tuple[WorkbenchSource, ...]) -> None:
        self.store = store
        self.token = token
        self.sources = sources

    def rows(self, *, namespace=None, offset=0, limit=WORKBENCH_PAGE_SIZE, token=None, reading_order=False):
        token = token or self.token
        return self.store.read_page(
            token.variant_id,
            namespace=namespace,
            offset=offset,
            limit=limit,
            token=token,
            reading_order=reading_order,
        )

    def full_rows(self, *, namespace=None, token=None, include_removed=False, reading_order=False):
        token = token or self.token
        return self.store.iter_entries(
            token.variant_id,
            namespace=namespace,
            token=token,
            include_removed=include_removed,
            reading_order=reading_order,
        )

    def entry(self, key, token=None):
        token = token or self.token
        rows = self.store.read_entries(token.variant_id, (key,), token=token)
        return rows[0] if rows else None

    def authority(self, preview=()) -> DeferredVariantRows:
        def find(keys):
            return (row.state for row in self.store.read_entries(self.token.variant_id, keys, token=self.token))

        def rebase(project_revision, variant_revision, known):
            token = replace(self.token, project_revision=project_revision, variant_revision=variant_revision)
            return ProjectPagedRead(self.store, token, self.sources).authority(known)

        return DeferredVariantRows(
            lambda namespace=None: (item.state for item in self.full_rows(namespace=namespace, include_removed=True)),
            find,
            rebase,
            preview=preview,
        )

    def template(self, source: WorkbenchSource, token: ProjectReadToken) -> SourceSnapshot:
        info = source.info
        with project_connection(self.store.path) as connection:
            project = connection.execute("SELECT identity, revision FROM project").fetchone()
            variant = require_variant(connection, token.variant_id)
            if (project["identity"], project["revision"], variant["revision"]) != (
                token.project_id.value,
                token.project_revision,
                token.variant_revision,
            ):
                from .format import ProjectStorageConflict

                raise ProjectStorageConflict("project changed before reading the requested format")
            (info,) = read_sources(connection, info.namespace)
            if info.format_id is FormatId.PLUGIN_SSE:
                return reconstructed_source_snapshot(info, ())
            rows = read_page(connection, token.variant_id, info.namespace, 0, -1, include_removed=True)
            native = read_format_records(connection, info.namespace)
        return reconstructed_source_snapshot(info, rows, native)


def read_workbench_candidate(projects, target) -> CandidateRecords:
    ref = target.variant_ref
    store = projects.store_for(ref.project_id)
    with project_connection(store.path) as connection:
        project = read_project(connection, target.project_ref)
        variant = require_variant(connection, ref.identity)
        infos = read_sources(connection, native_fields=False)
        counts = {
            row["namespace"]: row["entry_count"] - row["removed"]
            for row in connection.execute(
                """
                SELECT ps.namespace, s.entry_count, (
                    SELECT COUNT(*) FROM entry_state AS state
                    JOIN source_entry AS e ON e.id = state.entry_id AND e.source_id = s.id
                    WHERE state.variant_id = ? AND state.tombstone = 1
                ) AS removed
                FROM project_source AS ps JOIN source AS s ON s.id = ps.source_id
                """,
                (ref.identity.value,),
            )
        }
        registry = registered_source_ids(project)
        sources = tuple(
            WorkbenchSource(
                registry.get(info.namespace.value, info.namespace.value),
                info,
                read_page(
                    connection,
                    ref.identity,
                    info.namespace,
                    0,
                    WORKBENCH_PAGE_SIZE,
                    reading_order=info.format_id is FormatId.PLUGIN_SSE,
                )
                if index == 0
                else (),
                counts[info.namespace.value],
            )
            for index, info in enumerate(infos)
        )
    token = ProjectReadToken(ref.project_id, ref.identity, project.envelope.revision, variant["revision"])
    reader = ProjectPagedRead(store, token, sources)
    fingerprints = tuple(SourceFingerprint(info.namespace, info.fingerprint) for info in infos)
    snapshot = VariantSnapshot(
        ref,
        fingerprints,
        reader.authority(tuple(row.state for source in sources for row in source.preview)),
        token.variant_revision,
        tuple(json.loads(variant["label_library"]).items()),
    )
    active = ActiveProject(
        project,
        VariantAggregate(snapshot),
        ref,
        token.project_revision,
        token.variant_revision,
        source_project_hash=revision_marker(token.project_revision),
        source_variant_hash=revision_marker(token.variant_revision),
        source_variant_revision=token.variant_revision,
    )
    return CandidateRecords(active, (), (), reader)


def describe_source(info: SourceInfo) -> SourceSnapshot:
    if info.format_id is FormatId.PLUGIN_SSE:
        return reconstructed_source_snapshot(info, ())
    return SourceSnapshot(
        SourceDescriptor(f"project-source:{info.namespace.value}", info.name),
        info.format_id,
        info.fingerprint,
        0,
        lease_id="project-translation-content",
        metadata=(("source_namespace", info.namespace.value),),
    )
