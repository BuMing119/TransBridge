"""Prepare and activate current project records without reading original input files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from transbridge.application.contracts import DomainError, ErrorCategory, OperationResult
from transbridge.application.projects.models import DirtyDecision, TransitionTarget
from transbridge.persistence.project_recovery import validate_recovery_context
from transbridge.persistence.v2.ids import ProjectId, ProjectRef, VariantId, VariantRef

from .catalog import ActiveProjectPreference
from .format import PROJECT_FILENAME, UnsupportedProjectFormat
from .records import CandidateLoader, CandidateRecords, WorkspaceProjects


@dataclass(frozen=True, slots=True)
class PreparedStoredProject:
    records: CandidateRecords
    variant_refs: tuple[VariantRef, ...]
    name: str
    sources: tuple[dict, ...]
    recovery: None = None
    missing_inputs: tuple = ()

    @property
    def project_ref(self):
        return self.records.active.project_ref

    @property
    def variant_ref(self):
        return self.records.active.formal_variant_ref

    @property
    def hydrations(self):
        return self.records.hydrations


class StoredProjectOpener:
    supports_paged_workbench = True

    def __init__(
        self,
        projects: WorkspaceProjects,
        active: ActiveProjectPreference,
        loader: CandidateLoader,
        baselines,
        commands,
        publisher,
    ) -> None:
        self._projects = projects
        self._active = active
        self._loader = loader
        self._baselines = baselines
        self._commands = commands
        self._publisher = publisher

    @property
    def directory(self) -> str:
        return str(self._projects.directory)

    @property
    def has_active_reference(self) -> bool:
        return self._active.path.is_file()

    def open_path(self, path, context, *, dirty_decision=None):
        if dirty_decision is DirtyDecision.CANCEL:
            return OperationResult.cancelled(run_id=context.run_id)
        prepared = self.prepare_path(path, context)
        if not prepared.is_success or prepared.value is None:
            return prepared
        return self.activate(prepared.value, context, dirty_decision=dirty_decision)

    def prepare_path(self, path, context, *, target_variant_id=None, paged=False):
        try:
            selected = Path(path).resolve()
            if selected.name != PROJECT_FILENAME:
                raise UnsupportedProjectFormat("请选择当前格式的 project.sqlite3 文件。")
            if selected.parent.parent != self._projects.directory:
                raise DomainError(
                    ErrorCategory.PERMISSION, "PROJECT_RECORD_OUTSIDE_REPOSITORY", "所选项目不属于当前数据目录。"
                )
            from .repository import ProjectStore

            summary = ProjectStore(selected).summary()
            ref = ProjectRef(summary.identity)
            if selected != Path(self._projects.path_for(ref)):
                raise UnsupportedProjectFormat("项目身份与项目目录不匹配。")
            variant_id = VariantId(target_variant_id) if target_variant_id is not None else summary.active_variant
            variant = VariantRef(variant_id, ref.identity)
            validate_recovery_context(variant, context)
            target = TransitionTarget(ref, variant)
            if paged:
                from .paged_reads import read_workbench_candidate

                records = read_workbench_candidate(self._projects, target)
            else:
                records = self._projects.candidate(target)
            data = records.active.project.envelope.data
            return OperationResult.completed(
                PreparedStoredProject(
                    records,
                    tuple(VariantRef(VariantId(value), ref.identity) for value in data["variant_ids"]),
                    data["name"],
                    tuple(data["sources"]),
                ),
                run_id=context.run_id,
            )
        except UnsupportedProjectFormat as exc:
            error = DomainError(ErrorCategory.INPUT, "PROJECT_FORMAT_UNSUPPORTED", str(exc), cause=exc)
            return OperationResult.failed(error, run_id=context.run_id)
        except Exception as exc:
            return OperationResult.from_exception(exc, run_id=context.run_id)

    def prepare_identity(self, project_id, variant_id, context, *, paged=False):
        try:
            ref = ProjectRef(ProjectId(project_id))
            return self.prepare_path(self._projects.path_for(ref), context, target_variant_id=variant_id, paged=paged)
        except Exception as exc:
            return OperationResult.from_exception(exc, run_id=context.run_id)

    def prepare_active(self, context, *, paged=False):
        try:
            project_id = self._active.read()
            if project_id is None:
                raise DomainError(ErrorCategory.PREREQUISITE, "ACTIVE_PROJECT_REQUIRED", "当前没有可恢复的项目。")
            return self.prepare_path(self._projects.path_for(ProjectRef(project_id)), context, paged=paged)
        except Exception as exc:
            return OperationResult.from_exception(exc, run_id=context.run_id)

    def activate(self, prepared, context, *, dirty_decision=None, prepare_view=None, expected_active_signature=...):
        captured = None
        if prepared.records.paged_read is None:
            captured = self._baselines.capture_catalog(prepared.project_ref, prepared.variant_refs)
            self._baselines.replace_many(
                prepared.project_ref, prepared.variant_refs, prepared.records.baselines, allow_empty=True
            )
        payload = None
        committed = False
        try:

            def prepare_candidate(candidate):
                nonlocal payload
                if prepare_view is not None:
                    payload = prepare_view(self._publisher.prepare(candidate))

            with self._loader.use(prepared.records):
                switched = self._commands.switch_v2(
                    prepared.project_ref,
                    prepared.variant_ref,
                    context,
                    dirty_decision=dirty_decision,
                    prepare_candidate=prepare_candidate,
                    expected_active_signature=expected_active_signature,
                )
            if not switched.is_success:
                return switched
            committed = True
            return OperationResult.completed(
                {
                    "project_id": prepared.project_ref.identity.value,
                    "variant_id": prepared.variant_ref.identity.value,
                    "name": prepared.name,
                    "sources": prepared.sources,
                    "hydrations": prepared.hydrations,
                    "view_payload": payload,
                    "missing_inputs": (),
                },
                diagnostics=switched.diagnostics,
                run_id=context.run_id,
            )
        except Exception as exc:
            return OperationResult.from_exception(exc, run_id=context.run_id)
        finally:
            self._publisher.discard_prepared()
            if not committed and captured is not None:
                self._baselines.restore_catalog(
                    prepared.project_ref, captured, expected_baselines=prepared.records.baselines
                )
