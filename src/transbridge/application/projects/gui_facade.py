"""GUI command facade over the authoritative Project lifecycle service."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from types import EllipsisType
from typing import Any

from transbridge.application.contracts import (
    Diagnostic,
    DomainError,
    ErrorCategory,
    OperationOutcome,
    OperationResult,
    RequestContext,
)
from transbridge.application.io.identity import EntryKey
from transbridge.application.translation.result_merge import ResultConflictPolicy
from transbridge.persistence.v2.ids import ProjectRef, VariantRef
from transbridge.persistence.v2.variant import VariantChangeSet

from .categorized_upload import ProjectCategorizedUploadService
from .durable_variant import DurableVariantCommitService
from .lifecycle import ProjectLifecycleService
from .models import ActiveProject, DirtyDecision, TransitionTarget
from .provisioning import ProjectProvisioningRequest, ProjectProvisioningService, ProjectSourceRequest
from .source_commands import ProjectSourceMutationService, SourceMutationResult
from .transition_preparation import ActiveSignature
from .variant_commands import (
    EntryStatePatch,
    normalize_label_library,
    patch_entry_records,
    patch_entry_states,
    replace_labels as replace_variant_labels,
    update_entry_by_key,
    update_entry_by_local_key,
)


class GuiProjectCommandFacade:
    def __init__(
        self,
        lifecycle: ProjectLifecycleService,
        projection_rebuild: Callable[[], None] | None = None,
        *,
        projects=None,
        variants=None,
        baselines=None,
        id_factory: Callable[[], str] | None = None,
        provisioning: ProjectProvisioningService | None = None,
        source_mutations: ProjectSourceMutationService | None = None,
        categorized_uploads: ProjectCategorizedUploadService | None = None,
        durable_variant_commits: DurableVariantCommitService | None = None,
    ) -> None:
        self._lifecycle = lifecycle
        self._projection_rebuild = projection_rebuild
        self._projects = projects
        self._variants = variants
        self._baselines = baselines
        self._id_factory = id_factory
        self._provisioning = provisioning
        self._source_mutations = source_mutations
        self._categorized_uploads = categorized_uploads
        self._durable_variant_commits = durable_variant_commits

    def categorized_upload_snapshot(self, context, *, endpoint: str, remote_project_id: int, source_ids=None):
        return self._categorized_upload_service().snapshot(
            context, endpoint=endpoint, remote_project_id=remote_project_id, source_ids=source_ids
        )

    def categorized_upload_current(self, snapshot, context):
        return self._categorized_upload_service().current(snapshot, context)

    def file_sources_snapshot(self, context, *, endpoint, remote_project_id, source_ids=None):
        """Read selected file sources independently, retaining non-download references."""
        return self._categorized_upload_service().snapshot(
            context,
            endpoint=endpoint,
            remote_project_id=remote_project_id,
            source_ids=source_ids,
            tolerate_failures=True,
            include_external_refs=True,
        )

    def file_sources_catalog(self, context):
        """List current registered plugins and entry counts without source I/O."""
        return self._categorized_upload_service().catalog(context)

    def task_result_directory(self, context):
        """Locate the active Project directory for durable task-owned results."""
        active = self._lifecycle.active
        if active is None or self._projects is None:
            raise DomainError(ErrorCategory.PREREQUISITE, "ACTIVE_PROJECT_REQUIRED", "请先打开工程。")
        if context.project_id not in (None, active.project_ref.identity.value) or context.variant_id not in (
            None,
            active.formal_variant_ref.identity.value if active.formal_variant_ref is not None else None,
        ):
            raise DomainError(ErrorCategory.CONFLICT, "PROJECT_CONTEXT_MISMATCH", "任务对应的工程版本已切换。")
        return Path(self._projects.path_for(active.project_ref)).parent

    def _categorized_upload_service(self) -> ProjectCategorizedUploadService:
        if self._categorized_uploads is None:
            raise DomainError(ErrorCategory.PREREQUISITE, "CATEGORIZED_UPLOAD_UNAVAILABLE", "工程分类上传服务未配置。")
        return self._categorized_uploads

    def prepare_create(
        self,
        request: ProjectProvisioningRequest,
        context: RequestContext,
    ) -> OperationResult[dict[str, Any]]:
        if self._provisioning is None:
            return OperationResult.failed(
                DomainError(
                    ErrorCategory.PREREQUISITE,
                    "PROJECT_PROVISIONING_UNAVAILABLE",
                    "建项服务未配置。",
                ),
                run_id=context.run_id,
            )
        result = self._provisioning.prepare(request, context)
        if result.outcome is OperationOutcome.COMPLETED and result.value is not None:
            return OperationResult.completed(
                result.value.to_dict(),
                diagnostics=result.diagnostics,
                run_id=result.run_id,
            )
        return OperationResult(
            result.outcome,
            diagnostics=result.diagnostics,
            counts=result.counts,
            run_id=result.run_id,
        )

    def commit_create(
        self,
        token: str,
        context: RequestContext,
        *,
        request_fingerprint: str | None = None,
    ) -> OperationResult[dict[str, Any]]:
        if self._provisioning is None:
            return OperationResult.failed(
                DomainError(
                    ErrorCategory.PREREQUISITE,
                    "PROJECT_PROVISIONING_UNAVAILABLE",
                    "建项服务未配置。",
                ),
                run_id=context.run_id,
            )
        return self._provisioning.commit(
            token,
            context,
            request_fingerprint=request_fingerprint,
        )

    def discard_create(self, token: str, context: RequestContext) -> OperationResult[None]:
        if self._provisioning is None:
            return OperationResult.failed(
                DomainError(
                    ErrorCategory.PREREQUISITE,
                    "PROJECT_PROVISIONING_UNAVAILABLE",
                    "建项服务未配置。",
                ),
                run_id=context.run_id,
            )
        return self._provisioning.discard(token, context)

    def consume_create_hydration(self, project_id: str, context: RequestContext):
        """Consume the one-shot UI read model produced by S02 prepare."""

        if self._provisioning is None:
            from .provisioning import ProjectProvisioningHydrationResult

            return ProjectProvisioningHydrationResult(
                diagnostics=(
                    Diagnostic(
                        "PROJECT_PROVISIONING_UNAVAILABLE",
                        "建项服务未配置。",
                        category=ErrorCategory.PREREQUISITE,
                    ),
                )
            )
        return self._provisioning.consume_hydration(project_id, context)

    def add_source(
        self,
        request: ProjectSourceRequest,
        context: RequestContext,
        *,
        expected_project_revision: int | None = None,
        expected_variant_revision: int | None = None,
        expected_variant_ref: VariantRef | None = None,
    ) -> OperationResult[SourceMutationResult]:
        if self._source_mutations is None:
            return OperationResult.failed(
                DomainError(
                    ErrorCategory.PREREQUISITE,
                    "PROJECT_SOURCE_MUTATION_UNAVAILABLE",
                    "工程来源变更服务未配置。",
                ),
                run_id=context.run_id,
            )
        return self._source_mutations.add_source(
            request,
            context,
            expected_project_revision=expected_project_revision,
            expected_variant_revision=expected_variant_revision,
            expected_variant_ref=expected_variant_ref,
        )

    def add_prepared_source(self, prepared, context, *, expected_variant_ref):
        if self._source_mutations is None:
            raise ValueError("工程来源变更服务未配置。")
        return self._source_mutations.add_prepared_source(
            prepared,
            context,
            expected_variant_ref=expected_variant_ref,
        )

    def remove_source(self, locator: str, context: RequestContext) -> OperationResult[SourceMutationResult]:
        if self._source_mutations is None:
            return OperationResult.failed(
                DomainError(
                    ErrorCategory.PREREQUISITE,
                    "PROJECT_SOURCE_MUTATION_UNAVAILABLE",
                    "工程来源变更服务未配置。",
                ),
                run_id=context.run_id,
            )
        return self._source_mutations.remove_source(locator, context)

    def create_project(
        self,
        request: ProjectProvisioningRequest,
        context: RequestContext,
    ) -> OperationResult[dict[str, Any]]:
        prepared = self.prepare_create(request, context)
        if prepared.outcome is not OperationOutcome.COMPLETED or prepared.value is None:
            return prepared
        return self.commit_create(
            str(prepared.value["token"]),
            context,
            request_fingerprint=str(prepared.value["request_fingerprint"]),
        )

    def switch_v2(
        self,
        project_ref: ProjectRef,
        variant_ref: VariantRef | None,
        context: RequestContext,
        *,
        dirty_decision: DirtyDecision | None = None,
        prepare_candidate: Callable[[ActiveProject | None], None] | None = None,
        expected_active_signature: ActiveSignature | EllipsisType = Ellipsis,
    ) -> OperationResult[dict | None]:
        prepared = self._lifecycle.prepare_transition(
            TransitionTarget(project_ref, variant_ref),
            context,
            dirty_decision=dirty_decision,
            prepare_candidate=prepare_candidate,
            expected_active_signature=expected_active_signature,
        )
        if prepared.outcome is not OperationOutcome.COMPLETED or prepared.value is None:
            return prepared
        return self._lifecycle.commit_transition(prepared.value["token"], context)

    def save(
        self,
        context: RequestContext,
        *,
        expected_active_signature: ActiveSignature | EllipsisType = ...,
        expected_active_identity: tuple[str, str | None] | EllipsisType = ...,
    ) -> OperationResult[dict | None]:
        options = {"expected_active_signature": expected_active_signature}
        if expected_active_identity is not ...:
            options["expected_active_identity"] = expected_active_identity
        active = self._lifecycle.active
        previous_summary = None if active is None else active.summary()
        result = self._lifecycle.save_active(context, **options)
        if result.is_success and self._projection_rebuild is not None and result.value != previous_summary:
            self._projection_rebuild()
        return result

    def save_snapshot(self, name: str, context: RequestContext) -> OperationResult[dict[str, Any]]:
        """Persist one read-only snapshot of the active formal Variant."""

        return self._lifecycle.save_snapshot(name, context)

    def create_variant(self, name: str, context: RequestContext, *, copy_active: bool = False):
        return self._catalog_commands().create(name, context, copy_active=copy_active)

    def delete_variant(self, variant_id: str, context: RequestContext):
        return self._catalog_commands().delete(variant_id, context)

    def _catalog_commands(self):
        from .catalog_commands import VariantCatalogCommands

        return VariantCatalogCommands(
            self._lifecycle, self._projects, self._variants, self._baselines, self._id_factory
        )

    def update_entry(
        self,
        local_key: str | EntryKey,
        context: RequestContext,
        *,
        translation: str | None = None,
        stage: int | None = None,
    ) -> OperationResult[dict[str, Any]]:
        update = update_entry_by_key if isinstance(local_key, EntryKey) else update_entry_by_local_key
        return self._commit_variant(
            context,
            update_entries=lambda entries: update(entries, local_key, translation=translation, stage=stage),
        )

    def replace_labels(
        self,
        entry_labels: Mapping[EntryKey | str, set[str]],
        label_library: Mapping[str, Mapping[str, Any]],
        context: RequestContext,
        *,
        expected_project_revision: int | None = None,
        expected_variant_revision: int | None = None,
        expected_variant_ref: VariantRef | None = None,
    ) -> OperationResult[dict[str, Any]]:
        return self._commit_variant(
            context,
            update_entries=lambda entries: replace_variant_labels(entries, entry_labels),
            label_library=normalize_label_library(label_library),
            expected_project_revision=expected_project_revision,
            expected_variant_revision=expected_variant_revision,
            expected_variant_ref=expected_variant_ref,
        )

    def replace_entry_states(
        self,
        states: Mapping[EntryKey, tuple[str, int]],
        context: RequestContext,
        *,
        expected_project_revision: int | None = None,
        expected_variant_revision: int | None = None,
        expected_variant_ref: VariantRef | None = None,
        commit_mutation: Callable[[Callable[[], None]], None] | None = None,
        persist: bool = False,
    ) -> OperationResult[dict[str, Any]]:
        """Commit a complete set of projected translation/stage changes once."""

        return self._commit_variant(
            context,
            update_entries=lambda entries: patch_entry_states(entries, states),
            expected_project_revision=expected_project_revision,
            expected_variant_revision=expected_variant_revision,
            expected_variant_ref=expected_variant_ref,
            commit_mutation=commit_mutation,
            persist=persist,
        )

    def merge_entry_states(
        self,
        candidates,
        context,
        *,
        expected_variant_ref,
        policy=ResultConflictPolicy.KEEP_CURRENT,
        commit_mutation=None,
        persist=False,
        before_commit=None,
        policies=None,
        decision_states=None,
        clear_external_scope=None,
    ):
        """Merge completed asynchronous results without relying on launch revisions."""
        from .result_merge import ProjectResultMergeService

        return ProjectResultMergeService(
            self._lifecycle,
            self._categorized_upload_service().result_source_texts,
            self.replace_entry_states,
            self.replace_entry_records,
        ).merge(
            candidates,
            context,
            expected_variant_ref=expected_variant_ref,
            policy=policy,
            commit_mutation=commit_mutation,
            persist=persist,
            before_commit=before_commit,
            policies=policies,
            decision_states=decision_states,
            clear_external_scope=clear_external_scope,
        )

    def replace_entry_records(
        self,
        patches: Mapping[EntryKey, EntryStatePatch],
        context: RequestContext,
        *,
        expected_project_revision: int | None = None,
        expected_variant_revision: int | None = None,
        expected_variant_ref: VariantRef | None = None,
        commit_mutation: Callable[[Callable[[], None]], None] | None = None,
        persist: bool = False,
    ) -> OperationResult[dict[str, Any]]:
        """Commit all Variant-owned state for existing entries."""

        return self._commit_variant(
            context,
            update_entries=lambda entries: patch_entry_records(entries, patches),
            expected_project_revision=expected_project_revision,
            expected_variant_revision=expected_variant_revision,
            expected_variant_ref=expected_variant_ref,
            commit_mutation=commit_mutation,
            persist=persist,
        )

    def _commit_variant(
        self,
        context: RequestContext,
        *,
        update_entries,
        label_library=None,
        expected_project_revision: int | None = None,
        expected_variant_revision: int | None = None,
        expected_variant_ref: VariantRef | None = None,
        commit_mutation: Callable[[Callable[[], None]], None] | None = None,
        persist: bool = False,
    ) -> OperationResult[dict[str, Any]]:
        active = self._lifecycle.active
        if active is None or active.variant is None or active.formal_variant_ref is None:
            return OperationResult.failed(
                DomainError(
                    ErrorCategory.PREREQUISITE,
                    "ACTIVE_VARIANT_REQUIRED",
                    "A V2 Variant must be active before applying a GUI command.",
                ),
                run_id=context.run_id,
            )
        snapshot = active.variant.snapshot()
        project_revision = active.project.envelope.revision
        if expected_variant_ref is not None and expected_variant_ref != snapshot.ref:
            return OperationResult.failed(
                DomainError(
                    ErrorCategory.CONFLICT,
                    "ACTIVE_VARIANT_IDENTITY_CHANGED",
                    "The active Variant changed before the command could commit.",
                ),
                run_id=context.run_id,
            )
        if expected_project_revision is not None and expected_project_revision != project_revision:
            return OperationResult.failed(
                DomainError(
                    ErrorCategory.CONFLICT,
                    "ACTIVE_PROJECT_REVISION_CHANGED",
                    "The active Project changed before the command could commit.",
                ),
                run_id=context.run_id,
            )
        if expected_variant_revision is not None and expected_variant_revision != snapshot.revision:
            return OperationResult.failed(
                DomainError(
                    ErrorCategory.CONFLICT,
                    "ACTIVE_VARIANT_REVISION_CHANGED",
                    "The active Variant changed before the command could commit.",
                ),
                run_id=context.run_id,
            )
        try:
            if persist and self._durable_variant_commits is None:
                raise DomainError(
                    ErrorCategory.PREREQUISITE,
                    "DURABLE_VARIANT_SAVE_UNAVAILABLE",
                    "自动保存服务尚未配置，本次未修改译文；请检查工程保存配置。",
                )
            entries = update_entries(snapshot.entries)
            next_label_library = snapshot.label_library if label_library is None else label_library
            if (
                entries == snapshot.entries
                and next_label_library == snapshot.label_library
                and not (persist and active.dirty)
            ):
                if commit_mutation is not None:
                    return self._lifecycle.commit_active_content(
                        active.project,
                        snapshot,
                        context,
                        expected_project_revision=project_revision,
                        expected_variant_revision=snapshot.revision,
                        commit_mutation=commit_mutation,
                    )
                return OperationResult.completed(
                    {
                        "project_id": active.project.envelope.identity,
                        "project_revision": project_revision,
                        "variant_id": snapshot.ref.identity.value,
                        "revision": snapshot.revision,
                    },
                    run_id=context.run_id,
                )
            change_set = VariantChangeSet(
                snapshot.ref,
                snapshot.revision if expected_variant_revision is None else expected_variant_revision,
                snapshot.source_fingerprints,
                entries,
                next_label_library,
                context.run_id or "",
            )
            if persist:
                return self._durable_variant_commits.commit(
                    change_set,
                    context,
                    expected_project_revision=(
                        project_revision if expected_project_revision is None else expected_project_revision
                    ),
                    commit_mutation=commit_mutation,
                )
            return self._lifecycle.commit_active_variant(
                change_set,
                context,
                expected_project_revision=(
                    project_revision if expected_project_revision is None else expected_project_revision
                ),
                **({"commit_mutation": commit_mutation} if commit_mutation is not None else {}),
            )
        except Exception as exc:  # noqa: BLE001
            return OperationResult.from_exception(exc, run_id=context.run_id)


__all__ = ["GuiProjectCommandFacade"]
