# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Commit source revisions and their recovery points across all versions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from transbridge.application.contracts import DomainError, ErrorCategory
from transbridge.application.io.identity import SourceNamespace
from transbridge.application.projects.models import LifecycleSave, LifecycleSnapshot, TransitionTarget
from transbridge.application.projects.provisioning_models import PreparedProjectSource
from transbridge.application.projects.source_content import source_content_identity
from transbridge.application.projects.source_extraction import extract_source
from transbridge.persistence.v2.ids import ProjectId, ProjectRef, VariantId, VariantRef
from transbridge.persistence.v2.models import ProjectDto
from transbridge.persistence.v2.variant import VariantSnapshot

from .connection import project_connection
from .format import PROJECT_FILENAME, ProjectStorageConflict, UnsupportedProjectFormat
from .history import write_snapshot
from .lifecycle import _verify_project, _write_project
from .reads import read_sources, require_variant
from .records import read_project, read_variant
from .repository import ProjectStore
from .source_references import read_reference_path
from .variant_rows import save_sources, write_variant


@dataclass(frozen=True, slots=True)
class SourceUpdateRead:
    project: ProjectDto
    variants: tuple[VariantSnapshot, ...]


class ProjectSourceRevisionStore:
    def __init__(self, projects) -> None:
        self.projects = projects

    def resolve(self, path: str) -> ProjectRef:
        try:
            selected = Path(path).resolve()
            if selected.name != PROJECT_FILENAME or selected.parent.parent != self.projects.directory:
                raise UnsupportedProjectFormat("请选择当前工作区内的项目数据库。")
            ref = ProjectRef(ProjectStore(selected).summary().identity)
            if selected != Path(self.projects.path_for(ref)):
                raise UnsupportedProjectFormat("项目身份与目录不匹配。")
            return ref
        except UnsupportedProjectFormat as exc:
            raise DomainError(ErrorCategory.INPUT, "PROJECT_FORMAT_UNSUPPORTED", str(exc), cause=exc) from exc

    def read(self, ref: ProjectRef) -> SourceUpdateRead:
        with project_connection(self.projects.store_for(ref.identity).path) as connection:
            project = read_project(connection, ref)
            variants = tuple(
                read_variant(connection, VariantRef(VariantId(value), ref.identity))[0]
                for value in project.envelope.data["variant_ids"]
            )
            return SourceUpdateRead(project, variants)

    def read_source_references(self, ref: ProjectRef) -> tuple[ProjectDto, dict[str, str]]:
        with project_connection(self.projects.store_for(ref.identity).path) as connection:
            return read_project(connection, ref), {
                source.namespace.value: source.reference_path
                for source in read_sources(connection, native_fields=False)
            }

    def write_project(self, ref: ProjectRef, project: ProjectDto, expected_revision: int) -> None:
        with project_connection(self.projects.store_for(ref.identity).path, write=True) as connection:
            _verify_project(connection, project, expected_revision)
            _write_project(connection, project)

    def previous_sources(self, ref: ProjectRef, sources) -> tuple[PreparedProjectSource, ...]:
        summary = self.projects.store_for(ref.identity).summary()
        target = TransitionTarget(ref, VariantRef(summary.active_variant, ref.identity))
        prepared = self.projects.candidate(target)
        baselines = {item.fingerprint.namespace.value: item for item in prepared.baselines}
        hydrations = {item.source_id: item for item in prepared.hydrations}
        previous = []
        for source in sources:
            baseline = baselines.get(source_content_identity(source.to_dict()))
            hydration = hydrations.get(source.source_id)
            if baseline is None or hydration is None or baseline.fingerprint.sha256 != source.fingerprint:
                raise ProjectStorageConflict("current source revision has no unambiguous translation facts")
            previous.append(
                PreparedProjectSource(
                    tuple({**source.to_dict(), "location": hydration.location}.items()), baseline, hydration=hydration
                )
            )
        return tuple(previous)

    def commit_batch(
        self,
        expected: SourceUpdateRead,
        project: ProjectDto,
        variants: tuple[VariantSnapshot, ...],
        transaction_id: str,
        *,
        sources: tuple[PreparedProjectSource, ...],
        replaces_namespaces: tuple[SourceNamespace | None, ...],
        expected_reference_paths: tuple[str | None, ...],
        backup_name: str,
    ) -> str:
        ref = ProjectRef(ProjectId(project.envelope.identity))
        old_by_ref = {item.ref: item for item in expected.variants}
        if {item.ref for item in variants} != old_by_ref.keys():
            raise ProjectStorageConflict("the source update must preserve all declared Variants")
        with project_connection(self.projects.store_for(ref.identity).path, write=True) as connection:
            _verify_project(connection, project, expected.project.envelope.revision)
            replacements = {}
            inputs = zip(sources, replaces_namespaces, expected_reference_paths, strict=True)
            for source, namespace, reference in inputs:
                if namespace is not None:
                    if reference is not None:
                        _verify_reference(connection, namespace, reference)
                    replacements[namespace.value] = source.baseline.fingerprint.namespace.value
            for index, old in enumerate(expected.variants):
                if require_variant(connection, old.ref.identity)["revision"] != old.revision:
                    raise ProjectStorageConflict("a Variant changed before the source update")
                write_snapshot(
                    connection,
                    LifecycleSnapshot(ref, old.ref, old, f"更新来源前 · {backup_name}"),
                    f"{transaction_id}-{index}",
                )
            first = variants[0]
            save_sources(
                connection,
                LifecycleSave(
                    project,
                    first.ref,
                    first,
                    expected.project.envelope.revision,
                    old_by_ref[first.ref].revision,
                    source_additions=tuple(extract_source(source) for source in sources),
                ),
                replacements=replacements,
            )
            _write_project(connection, project)
            for variant in variants:
                write_variant(connection, variant, variant.entries, old_by_ref[variant.ref].revision)
        return f"project-snapshot:{transaction_id}"

    def committed_backup(self, project: ProjectDto, variants: tuple[VariantSnapshot, ...], transaction_id: str):
        """Recover a lost commit reply from the backups written in that transaction."""
        ref = ProjectRef(ProjectId(project.envelope.identity))
        with project_connection(self.projects.store_for(ref.identity).path) as connection:
            current = read_project(connection, ref)
            if current.envelope.revision != project.envelope.revision:
                return None
            for index, variant in enumerate(variants):
                backup = connection.execute(
                    "SELECT 1 FROM named_snapshot WHERE identity = ? AND variant_id = ?",
                    (f"{transaction_id}-{index}", variant.ref.identity.value),
                ).fetchone()
                if backup is None or require_variant(connection, variant.ref.identity)["revision"] != variant.revision:
                    return None
        return f"project-snapshot:{transaction_id}"


def _verify_reference(connection, namespace, expected):
    row = connection.execute("SELECT source_id FROM project_source WHERE namespace = ?", (namespace.value,)).fetchone()
    if row is None or read_reference_path(connection, row[0]) != expected:
        raise DomainError(ErrorCategory.CONFLICT, "SOURCE_UPDATE_STALE", "来源路径在预览后发生变化，请重新预览。")
