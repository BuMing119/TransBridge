"""GUI command facade over the authoritative Project lifecycle service."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
from typing import Any

from transbridge.application.contracts import (
    Diagnostic,
    DiagnosticSeverity,
    DomainError,
    ErrorCategory,
    OperationOutcome,
    OperationResult,
    RequestContext,
)
from transbridge.application.io.identity import EntryKey
from transbridge.persistence.v2.baselines import LegacyIdentityRegistry
from transbridge.persistence.v2.ids import ProjectRef, VariantId, VariantRef
from transbridge.persistence.v2.models import LoadedRecord, ProjectDto
from transbridge.persistence.v2.variant import VariantChangeSet, VariantSnapshot

from .catalog import project_with_added_variant, project_without_variant, variant_catalog
from .lifecycle import ProjectLifecycleService
from .models import DirtyDecision, TransitionTarget
from .provisioning import ProjectProvisioningRequest, ProjectProvisioningService, ProjectSourceRequest
from .source_commands import ProjectSourceMutationService, SourceMutationResult
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
        legacy_identities: LegacyIdentityRegistry,
        projection_rebuild: Callable[[], None] | None = None,
        *,
        projects=None,
        variants=None,
        baselines=None,
        id_factory: Callable[[], str] | None = None,
        provisioning: ProjectProvisioningService | None = None,
        source_mutations: ProjectSourceMutationService | None = None,
    ) -> None:
        self._lifecycle = lifecycle
        self._legacy_identities = legacy_identities
        self._projection_rebuild = projection_rebuild
        self._projects = projects
        self._variants = variants
        self._baselines = baselines
        self._id_factory = id_factory
        self._provisioning = provisioning
        self._source_mutations = source_mutations

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
    ) -> OperationResult[dict | None]:
        prepared = self._lifecycle.prepare_transition(
            TransitionTarget(project_ref, variant_ref),
            context,
            dirty_decision=dirty_decision,
        )
        if prepared.outcome is not OperationOutcome.COMPLETED or prepared.value is None:
            return prepared
        return self._lifecycle.commit_transition(prepared.value["token"], context)

    def switch_legacy(
        self,
        legacy_project_key: str,
        legacy_variant_name: str,
        context: RequestContext,
        *,
        dirty_decision: DirtyDecision | None = None,
    ) -> OperationResult[dict | None]:
        try:
            project_ref, variant_ref = self._legacy_identities.resolve(
                legacy_project_key,
                legacy_variant_name,
            )
        except Exception as exc:  # DomainError is preserved by OperationResult
            return OperationResult.from_exception(exc, run_id=context.run_id)
        return self.switch_v2(
            project_ref,
            variant_ref,
            context,
            dirty_decision=dirty_decision,
        )

    def save(self, context: RequestContext) -> OperationResult[dict | None]:
        result = self._lifecycle.save_active(context)
        if result.is_success and self._projection_rebuild is not None:
            self._projection_rebuild()
        return result

    def save_snapshot(self, name: str, context: RequestContext) -> OperationResult[dict[str, Any]]:
        """Persist one read-only snapshot of the active formal Variant."""

        return self._lifecycle.save_snapshot(name, context)

    def create_variant(
        self,
        name: str,
        context: RequestContext,
        *,
        copy_active: bool = False,
    ) -> OperationResult[dict[str, Any]]:
        """Create a named Variant, then activate it through the lifecycle boundary."""

        try:
            active = self._catalog_active()
            if active.dirty:
                raise DomainError(
                    ErrorCategory.PREREQUISITE,
                    "ACTIVE_SAVE_REQUIRED",
                    "请先保存当前版本，再创建或复制版本。",
                )
            assert active.variant is not None and active.formal_variant_ref is not None
            baselines = self._baselines.provide(active.project, active.formal_variant_ref, context)
            new_ref = self._new_variant_ref(active.project_ref)
            if copy_active:
                source = active.variant.snapshot()
                snapshot = VariantSnapshot(
                    new_ref,
                    source.source_fingerprints,
                    source.entries,
                    0,
                    source.label_library,
                )
            else:
                snapshot = VariantSnapshot(
                    new_ref,
                    tuple(item.fingerprint for item in baselines),
                    tuple(entry for item in baselines for entry in item.entries),
                )
            updated_project = project_with_added_variant(active.project, new_ref, name)
            self._variants.save(new_ref, snapshot.to_dto())
            try:
                self._projects.save(active.project_ref, updated_project)
            except Exception:
                self._variants.delete(new_ref)
                raise
            self._baselines.register(
                active.project_ref,
                new_ref,
                baselines,
                allow_empty=not active.project.envelope.data.get("sources"),
            )
            switched = self.switch_v2(active.project_ref, new_ref, context)
            if not switched.is_success:
                return switched
            descriptor = next(
                item for item in variant_catalog(updated_project) if item.variant_id == new_ref.identity.value
            )
            return OperationResult.completed(descriptor.to_dict(), run_id=context.run_id)
        except Exception as exc:  # noqa: BLE001
            return OperationResult.from_exception(exc, run_id=context.run_id)

    def delete_variant(self, variant_id: str, context: RequestContext) -> OperationResult[dict[str, Any]]:
        """Remove a Variant from the Project catalog, retaining one active Variant."""

        try:
            active = self._catalog_active()
            if active.dirty:
                raise DomainError(
                    ErrorCategory.PREREQUISITE,
                    "ACTIVE_SAVE_REQUIRED",
                    "请先保存当前版本，再删除版本。",
                )
            catalog = variant_catalog(active.project)
            target = next((item for item in catalog if item.variant_id == variant_id), None)
            if target is None:
                raise DomainError(ErrorCategory.INPUT, "VARIANT_NOT_FOUND", "指定版本不存在。")
            if len(catalog) <= 1:
                raise DomainError(
                    ErrorCategory.PREREQUISITE,
                    "LAST_VARIANT_REQUIRED",
                    "项目必须至少保留一个版本。",
                )
            target_ref = VariantRef(VariantId(variant_id), active.project_ref.identity)
            if active.formal_variant_ref == target_ref:
                replacement = next(item for item in catalog if item.variant_id != variant_id)
                replacement_ref = VariantRef(VariantId(replacement.variant_id), active.project_ref.identity)
                switched = self.switch_v2(active.project_ref, replacement_ref, context)
                if not switched.is_success:
                    return switched
                active = self._catalog_active()

            updated_project = project_without_variant(active.project, variant_id)
            self._projects.save(active.project_ref, updated_project)
            assert active.formal_variant_ref is not None
            refreshed = self.switch_v2(active.project_ref, active.formal_variant_ref, context)
            if not refreshed.is_success:
                return refreshed
            self._baselines.remove(active.project_ref, target_ref)
            diagnostics: tuple[Diagnostic, ...] = ()
            try:
                self._variants.delete(target_ref)
            except Exception:  # The catalog is authoritative; an orphan is safe but should be reported.
                diagnostics = (
                    Diagnostic(
                        "VARIANT_RECORD_CLEANUP_FAILED",
                        "版本已从项目中删除，但未能清理孤立的数据文件。",
                        DiagnosticSeverity.WARNING,
                    ),
                )
            return OperationResult.completed(
                {"id": target.variant_id, "name": target.name},
                diagnostics=diagnostics,
                run_id=context.run_id,
            )
        except Exception as exc:  # noqa: BLE001
            return OperationResult.from_exception(exc, run_id=context.run_id)

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
    ) -> OperationResult[dict[str, Any]]:
        """Commit a complete set of projected translation/stage changes once."""

        return self._commit_variant(
            context,
            update_entries=lambda entries: patch_entry_states(entries, states),
            expected_project_revision=expected_project_revision,
            expected_variant_revision=expected_variant_revision,
            expected_variant_ref=expected_variant_ref,
        )

    def replace_entry_records(
        self,
        patches: Mapping[EntryKey, EntryStatePatch],
        context: RequestContext,
        *,
        expected_project_revision: int | None = None,
        expected_variant_revision: int | None = None,
        expected_variant_ref: VariantRef | None = None,
    ) -> OperationResult[dict[str, Any]]:
        """Commit all Variant-owned state for existing entries."""

        return self._commit_variant(
            context,
            update_entries=lambda entries: patch_entry_records(entries, patches),
            expected_project_revision=expected_project_revision,
            expected_variant_revision=expected_variant_revision,
            expected_variant_ref=expected_variant_ref,
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
            entries = update_entries(snapshot.entries)
            next_label_library = snapshot.label_library if label_library is None else label_library
            if entries == snapshot.entries and next_label_library == snapshot.label_library:
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
            return self._lifecycle.commit_active_variant(
                change_set,
                context,
                expected_project_revision=(
                    project_revision if expected_project_revision is None else expected_project_revision
                ),
            )
        except Exception as exc:  # noqa: BLE001
            return OperationResult.from_exception(exc, run_id=context.run_id)

    def _catalog_active(self):
        if any(value is None for value in (self._projects, self._variants, self._baselines, self._id_factory)):
            raise DomainError(
                ErrorCategory.PREREQUISITE,
                "VARIANT_CATALOG_UNAVAILABLE",
                "版本目录服务未配置。",
            )
        active = self._lifecycle.active
        if active is None or active.variant is None or active.formal_variant_ref is None:
            raise DomainError(ErrorCategory.PREREQUISITE, "ACTIVE_VARIANT_REQUIRED", "请先打开一个项目版本。")
        loaded = self._projects.load(active.project_ref)
        if not isinstance(loaded, LoadedRecord) or not isinstance(loaded.value, ProjectDto):
            raise DomainError(ErrorCategory.PREREQUISITE, "PROJECT_RECORD_UNAVAILABLE", "项目记录不可用。")
        if loaded.value.envelope.revision != active.persisted_project_revision:
            raise DomainError(
                ErrorCategory.CONFLICT,
                "PROJECT_CATALOG_STALE",
                "项目版本目录已被其他操作修改，请重新打开项目。",
            )
        return active

    def _new_variant_ref(self, project_ref: ProjectRef) -> VariantRef:
        existing = {item.variant_id for item in variant_catalog(self._lifecycle.active.project)}
        for _ in range(8):
            raw = str(self._id_factory())
            candidate = f"variant-{raw}"
            if len(candidate) > 64:
                candidate = f"variant-{hashlib.sha256(raw.encode()).hexdigest()[:24]}"
            ref = VariantRef(VariantId(candidate), project_ref.identity)
            if ref.identity.value not in existing:
                return ref
        raise RuntimeError("unable to allocate a unique Variant ID")


__all__ = ["GuiProjectCommandFacade"]
