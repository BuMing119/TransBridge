"""Detached Variant catalog preparation; all writes use lifecycle activation."""

from dataclasses import replace
import hashlib

from transbridge.application.contracts import DomainError, ErrorCategory, OperationResult
from transbridge.persistence.v2.ids import VariantId, VariantRef
from transbridge.persistence.v2.models import LoadedRecord
from transbridge.persistence.v2.variant import VariantAggregate, VariantSnapshot

from .catalog import project_with_added_variant, project_without_variant, variant_catalog
from .models import ActiveProject, LifecycleActivation, project_with_active_variant


class VariantCatalogCommands:
    def __init__(self, lifecycle, projects, variants, baselines, id_factory):
        self._lifecycle = lifecycle
        self._projects = projects
        self._variants = variants
        self._baselines = baselines
        self._id_factory = id_factory

    def create(self, name, context, *, copy_active=False):
        descriptor = None

        def prepare(active):
            nonlocal descriptor
            project_record, source_record = self._check_active(active, context)
            baselines = self._baselines.provide(active.project, active.formal_variant_ref, context)
            new_ref = self._new_ref(active)
            source = active.variant.snapshot()
            snapshot = VariantSnapshot(
                new_ref,
                source.source_fingerprints if copy_active else tuple(item.fingerprint for item in baselines),
                source.entries if copy_active else tuple(entry for item in baselines for entry in item.entries),
                0,
                source.label_library if copy_active else (),
            )
            project = project_with_active_variant(project_with_added_variant(active.project, new_ref, name), new_ref)
            candidate = ActiveProject(
                project,
                VariantAggregate(snapshot),
                new_ref,
                active.persisted_project_revision,
                None,
                source_project_hash=project_record.source_hash,
            )
            activation = replace(
                LifecycleActivation.capture(active, candidate),
                candidate_baselines=baselines,
                checked_variants=(
                    (active.formal_variant_ref, source_record.value.envelope.revision, source_record.source_hash),
                ),
            )
            descriptor = next(item for item in variant_catalog(project) if item.variant_id == new_ref.identity.value)
            return candidate, activation

        result = self._lifecycle.commit_catalog_change(prepare, context)
        return self._result(result, descriptor, context)

    def delete(self, variant_id, context):
        descriptor = None

        def prepare(active):
            nonlocal descriptor
            project_record, source_record = self._check_active(active, context)
            catalog = variant_catalog(active.project)
            descriptor = next((item for item in catalog if item.variant_id == variant_id), None)
            if descriptor is None:
                raise DomainError(ErrorCategory.INPUT, "VARIANT_NOT_FOUND", "指定版本不存在。")
            if len(catalog) <= 1:
                raise DomainError(ErrorCategory.PREREQUISITE, "LAST_VARIANT_REQUIRED", "项目必须至少保留一个版本。")
            removed_ref = VariantRef(VariantId(variant_id), active.project_ref.identity)
            removed = self._loaded(self._variants.load(removed_ref))
            retained_ref = active.formal_variant_ref
            if retained_ref == removed_ref:
                retained_ref = VariantRef(
                    VariantId(next(item.variant_id for item in catalog if item.variant_id != variant_id)),
                    active.project_ref.identity,
                )
            from .models import TransitionTarget

            candidate = self._lifecycle.load_catalog_candidate(
                TransitionTarget(active.project_ref, retained_ref), context
            )
            if candidate.source_project_hash != project_record.source_hash:
                raise DomainError(ErrorCategory.CONFLICT, "PROJECT_CATALOG_STALE", "工程已变化，请重新打开。")
            project = project_without_variant(project_with_active_variant(active.project, retained_ref), variant_id)
            candidate = replace(candidate, project=project)
            activation = replace(
                LifecycleActivation.capture(active, candidate),
                removed_variant=(removed_ref, removed.value.envelope.revision, removed.source_hash),
                checked_variants=(
                    (active.formal_variant_ref, source_record.value.envelope.revision, source_record.source_hash),
                ),
            )
            return candidate, activation

        result = self._lifecycle.commit_catalog_change(prepare, context)
        return self._result(result, descriptor, context)

    def _check_active(self, active, context):
        if any(value is None for value in (self._projects, self._variants, self._baselines, self._id_factory)):
            raise DomainError(ErrorCategory.PREREQUISITE, "VARIANT_CATALOG_UNAVAILABLE", "版本目录服务未配置。")
        if active is None or active.variant is None or active.formal_variant_ref is None:
            raise DomainError(ErrorCategory.PREREQUISITE, "ACTIVE_VARIANT_REQUIRED", "请先打开一个项目版本。")
        if context.project_id is not None and context.project_id != active.project_ref.identity.value:
            raise DomainError(ErrorCategory.PERMISSION, "PROJECT_CONTEXT_MISMATCH", "请求的工程与活动工程不同。")
        if context.variant_id is not None and context.variant_id != active.formal_variant_ref.identity.value:
            raise DomainError(ErrorCategory.PERMISSION, "VARIANT_CONTEXT_MISMATCH", "请求的版本与活动版本不同。")
        if active.dirty:
            raise DomainError(ErrorCategory.PREREQUISITE, "ACTIVE_SAVE_REQUIRED", "请先保存当前版本。")
        project = self._loaded(self._projects.load(active.project_ref))
        variant = self._loaded(self._variants.load(active.formal_variant_ref))
        if (
            (project.value.envelope.revision, variant.value.envelope.revision)
            != (active.persisted_project_revision, active.persisted_variant_revision)
            or project.value != active.project
            or variant.value != active.variant.snapshot().to_dto()
        ):
            raise DomainError(
                ErrorCategory.CONFLICT, "PROJECT_CATALOG_STALE", "项目版本目录已被其他操作修改，请重新打开项目。"
            )
        return project, variant

    def _new_ref(self, active):
        existing = {item.variant_id for item in variant_catalog(active.project)}
        for _ in range(8):
            raw = str(self._id_factory())
            identity = f"variant-{raw}"
            if len(identity) > 64:
                identity = f"variant-{hashlib.sha256(raw.encode()).hexdigest()[:24]}"
            ref = VariantRef(VariantId(identity), active.project_ref.identity)
            if ref.identity.value not in existing:
                return ref
        raise RuntimeError("unable to allocate a unique Variant ID")

    @staticmethod
    def _loaded(record):
        if not isinstance(record, LoadedRecord):
            raise DomainError(ErrorCategory.PREREQUISITE, "PROJECT_RECORD_UNAVAILABLE", "项目记录不可用。")
        return record

    @staticmethod
    def _result(result, descriptor, context):
        if not result.is_success:
            return result
        return OperationResult.completed(descriptor.to_dict(), diagnostics=result.diagnostics, run_id=context.run_id)
