# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Read current SQLite project facts through the application lifecycle types."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from typing import Any

from transbridge.application.projects.models import ActiveProject, TransitionTarget
from transbridge.application.projects.provisioning_models import PreparedSourceHydration
from transbridge.application.projects.source_content import authoritative_baseline_sources, source_content_identity
from transbridge.application.projects.source_extraction import reconstructed_source_snapshot
from transbridge.application.projects.source_fields import parse_options, source_name
from transbridge.application.projects.translation_templates import XML_FORMATS
from transbridge.persistence.v2.ids import ProjectId, ProjectRef, VariantRef
from transbridge.persistence.v2.models import SCHEMA_VERSION, LoadedRecord, ProjectDto, SchemaEnvelope, VariantDto
from transbridge.persistence.v2.variant import SourceBaseline, SourceFingerprint, VariantAggregate, VariantSnapshot

from .connection import project_connection
from .entry_codec import encode_json
from .format import PROJECT_ROOT, ProjectStoreError
from .paths import project_path
from .reads import read_baseline_states, read_format_records, read_page, read_sources, read_summary, require_variant
from .repository import ProjectStore


@dataclass(frozen=True, slots=True)
class CandidateRecords:
    active: ActiveProject
    baselines: tuple[SourceBaseline, ...]
    hydrations: tuple[PreparedSourceHydration, ...]
    paged_read: Any = None


class WorkspaceProjects:
    def __init__(self, root: str | Path) -> None:
        self.root = str(Path(root).resolve())
        self.directory = Path(self.root) / PROJECT_ROOT

    def path_for(self, ref: ProjectRef) -> str:
        return str(project_path(self.root, ref.identity))

    def store_for(self, project_id: ProjectId) -> ProjectStore:
        path = project_path(self.root, project_id)
        if path.resolve() != path or self.directory.resolve() != self.directory:
            raise ProjectStoreError("project storage path must remain inside the workspace")
        return ProjectStore(path)

    def exists(self, ref: ProjectRef) -> bool:
        try:
            return self.store_for(ref.identity).summary().identity == ref.identity
        except ProjectStoreError:
            return False

    def read_snapshot(self, ref: ProjectRef) -> ProjectDto:
        with project_connection(self.store_for(ref.identity).path) as connection:
            return read_project(connection, ref)

    def load(self, ref: ProjectRef) -> LoadedRecord:
        project = self.read_snapshot(ref)
        return LoadedRecord(ref, project, revision_marker(project.envelope.revision))

    read_snapshot_record = load

    def candidate(self, target: TransitionTarget) -> CandidateRecords:
        if target.project_ref is None:
            raise ValueError("candidate must identify a project")
        if target.snapshot_ref is not None:
            raise ValueError("historical snapshots are restored through the snapshot command")
        with project_connection(self.store_for(target.project_ref.identity).path) as connection:
            project = read_project(connection, target.project_ref)
            if target.variant_ref is None:
                return CandidateRecords(ActiveProject(project, None, None, project.envelope.revision, None), (), ())
            snapshot, grouped = read_variant(connection, target.variant_ref)
            sources = read_sources(connection)
            baselines = read_baselines(connection, sources)
            native = {
                item.namespace: read_format_records(connection, item.namespace)
                for item in sources
                if item.format_id in XML_FORMATS
            }
        source_ids = registered_source_ids(project)
        hydrations = tuple(
            PreparedSourceHydration(
                f"project-source:{info.name}",
                info.fingerprint,
                info.format_id,
                reconstructed_source_snapshot(info, grouped[info.namespace], native.get(info.namespace, ())),
                tuple(item.snapshot for item in grouped[info.namespace]),
                source_ids.get(info.namespace.value),
                reference_location="",
            )
            for info in sources
        )
        active = ActiveProject(
            project,
            VariantAggregate(snapshot),
            target.variant_ref,
            project.envelope.revision,
            snapshot.revision,
            source_project_hash=revision_marker(project.envelope.revision),
            source_variant_hash=revision_marker(snapshot.revision),
            source_variant_revision=snapshot.revision,
        )
        return CandidateRecords(active, baselines, hydrations)


class WorkspaceVariants:
    def __init__(self, projects: WorkspaceProjects) -> None:
        self.projects = projects
        self.root = projects.root

    def path_for(self, ref: VariantRef) -> str:
        return self.projects.path_for(ProjectRef(ref.project_id))

    def exists(self, ref: VariantRef) -> bool:
        try:
            with project_connection(self.projects.store_for(ref.project_id).path) as connection:
                require_variant(connection, ref.identity)
                return True
        except ProjectStoreError:
            return False

    def read_snapshot(self, ref: VariantRef) -> VariantDto:
        with project_connection(self.projects.store_for(ref.project_id).path) as connection:
            snapshot, _ = read_variant(connection, ref)
        return snapshot.to_dto()

    def load(self, ref: VariantRef) -> LoadedRecord:
        variant = self.read_snapshot(ref)
        return LoadedRecord(ref, variant, revision_marker(variant.envelope.revision))

    read_snapshot_record = load


def read_project(connection: sqlite3.Connection, ref: ProjectRef) -> ProjectDto:
    summary = read_summary(connection)
    if summary.identity != ref.identity:
        raise ProjectStoreError("project identity differs from its storage path")
    metadata = json.loads(connection.execute("SELECT settings FROM project").fetchone()[0])
    data = {
        "sources": [],
        "source_relations": [],
        **metadata,
        "name": summary.name,
        "variant_ids": [variant.identity.value for variant in summary.variants],
        "variant_names": {variant.identity.value: variant.name for variant in summary.variants},
        "active_variant_id": summary.active_variant.value,
    }
    return ProjectDto(SchemaEnvelope(SCHEMA_VERSION, ref.kind, ref.identity.value, summary.revision, data))


def read_variant(connection: sqlite3.Connection, ref: VariantRef) -> tuple[VariantSnapshot, dict[Any, Any]]:
    row = require_variant(connection, ref.identity)
    grouped = defaultdict(list)
    entries = read_page(connection, ref.identity, None, 0, -1, include_removed=True)
    for entry in entries:
        grouped[entry.snapshot.entry_key.namespace].append(entry)
    sources = read_sources(connection)
    return (
        VariantSnapshot(
            ref,
            tuple(SourceFingerprint(source.namespace, source.fingerprint) for source in sources),
            tuple(entry.state for entry in entries),
            row["revision"],
            tuple(json.loads(row["label_library"]).items()),
        ),
        {source.namespace: tuple(grouped[source.namespace]) for source in sources},
    )


def read_baselines(connection: sqlite3.Connection, sources=None) -> tuple[SourceBaseline, ...]:
    sources = read_sources(connection, native_fields=False) if sources is None else sources
    return tuple(
        SourceBaseline(
            SourceFingerprint(source.namespace, source.fingerprint),
            read_baseline_states(connection, source.namespace),
        )
        for source in sources
    )


def registered_source_ids(project: ProjectDto) -> dict[str | None, str]:
    data = project.envelope.data
    sources = authoritative_baseline_sources(tuple(data["sources"]), tuple(data.get("source_relations", ())))
    return {source_content_identity(item): item["source_id"] for item in sources}


def project_settings(project: ProjectDto) -> str:
    data = project.envelope.data
    settings = {
        key: data[key] for key in ("source_relations", "source_registry_diagnostics", "remote_bindings") if key in data
    }
    settings["sources"] = [_source_registration(source) for source in data.get("sources", ())]
    return encode_json(settings)


def _source_registration(source: dict[str, Any]) -> dict[str, Any]:
    fields = {
        key: source[key]
        for key in (
            "source_id",
            "enabled",
            "format_id",
            "kind",
            "bilingual_capability",
            "fingerprint",
            "plugin_scope",
        )
        if key in source
    }
    fields.update(
        display_name=source_name(source.get("display_name") or source.get("location") or source["source_id"]),
        location="",
        content_state="translation",
        format_options=parse_options((source.get("format_options") or {}).items()),
        legacy={
            key: value
            for key, value in (source.get("legacy") or {}).items()
            if key in {"namespace", "source_id", "role"}
        },
    )
    return fields


def revision_marker(revision: int) -> str:
    return f"revision:{revision}"


class CandidateLoader:
    def __init__(self, load: Callable[[TransitionTarget], CandidateRecords]) -> None:
        self._load = load
        self._prepared: ContextVar[CandidateRecords | None] = ContextVar("prepared_project_candidate", default=None)

    def prepare_candidate(self, target: TransitionTarget, _context) -> ActiveProject:
        prepared = self._prepared.get()
        if (
            prepared is not None
            and target.snapshot_ref is None
            and prepared.active.project_ref == target.project_ref
            and prepared.active.formal_variant_ref == target.variant_ref
        ):
            return prepared.active
        return self._load(target).active

    @contextmanager
    def use(self, prepared: CandidateRecords):
        token = self._prepared.set(prepared)
        try:
            yield
        finally:
            self._prepared.reset(token)
