"""Commit source revisions and their recovery points across all versions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from transbridge.application.contracts import DomainError, ErrorCategory
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
from .reads import require_variant
from .records import read_project, read_variant
from .repository import ProjectStore
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

    def previous(self, ref: ProjectRef, source) -> PreparedProjectSource:
        summary = self.projects.store_for(ref.identity).summary()
        target = TransitionTarget(ref, VariantRef(summary.active_variant, ref.identity))
        prepared = self.projects.candidate(target)
        namespace = source_content_identity(source.to_dict())
        baselines = [item for item in prepared.baselines if item.fingerprint.namespace.value == namespace]
        hydrations = [item for item in prepared.hydrations if item.source_id == source.source_id]
        if len(baselines) != 1 or len(hydrations) != 1:
            raise ProjectStorageConflict("current source revision has no unambiguous translation facts")
        return PreparedProjectSource(
            tuple({**source.to_dict(), "location": hydrations[0].location}.items()),
            baselines[0],
            hydration=hydrations[0],
        )

    def commit(
        self,
        expected: SourceUpdateRead,
        project: ProjectDto,
        variants: tuple[VariantSnapshot, ...],
        transaction_id: str,
        *,
        source: PreparedProjectSource,
        replaces_namespace=None,
        backup_metadata=None,
    ) -> str:
        ref = ProjectRef(ProjectId(project.envelope.identity))
        old_by_ref = {item.ref: item for item in expected.variants}
        if {item.ref for item in variants} != old_by_ref.keys():
            raise ProjectStorageConflict("the source update must preserve all declared Variants")
        with project_connection(self.projects.store_for(ref.identity).path, write=True) as connection:
            _verify_project(connection, project, expected.project.envelope.revision)
            for index, old in enumerate(expected.variants):
                if require_variant(connection, old.ref.identity)["revision"] != old.revision:
                    raise ProjectStorageConflict("a Variant changed before the source update")
                write_snapshot(
                    connection,
                    LifecycleSnapshot(ref, old.ref, old, f"更新来源前 · {backup_metadata['source']}"),
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
                    source_additions=(extract_source(source),),
                ),
                replacements=(
                    {}
                    if replaces_namespace is None
                    else {replaces_namespace.value: source.baseline.fingerprint.namespace.value}
                ),
            )
            _write_project(connection, project)
            for variant in variants:
                write_variant(connection, variant, variant.entries, old_by_ref[variant.ref].revision)
        return f"project-snapshot:{transaction_id}"
