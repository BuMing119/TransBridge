"""Authoritative source add/remove commands for an active Project/Variant."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, replace
import os

from transbridge.application.contracts import (
    DomainError,
    ErrorCategory,
    OperationOutcome,
    OperationResult,
    RequestContext,
)
from transbridge.persistence.v2.baselines import BaselineRegistry
from transbridge.persistence.v2.ids import VariantId, VariantRef
from transbridge.persistence.v2.models import ProjectDto, SchemaEnvelope
from transbridge.persistence.v2.variant import SourceBaseline, VariantSnapshot

from .lifecycle import ProjectLifecycleService
from .provisioning import (
    PreparedSourceHydration,
    ProjectSourcePreparationPort,
    ProjectSourceRequest,
)
from .source_content import folded_plugin_pairs, source_content_identity
from .source_registry import (
    SourceKind,
    SourceRegistrySnapshot,
    migrate_legacy_source_registry,
    normalize_source_location,
)

_INITIAL_ENTRY_STATES_OPTION = "__transbridge_initial_entry_states_v1"


@dataclass(frozen=True, slots=True)
class SourceMutationResult:
    source_id: str
    location: str
    hydration: PreparedSourceHydration | None
    project_revision: int
    variant_revision: int


class ProjectSourceMutationService:
    """Prepare one source, then atomically publish Project/Variant working state."""

    def __init__(
        self,
        lifecycle: ProjectLifecycleService,
        baselines: BaselineRegistry,
        preparer: ProjectSourcePreparationPort,
    ) -> None:
        self._lifecycle = lifecycle
        self._baselines = baselines
        self._preparer = preparer

    def add_source(
        self,
        request: ProjectSourceRequest,
        context: RequestContext,
        *,
        expected_project_revision: int | None = None,
        expected_variant_revision: int | None = None,
        expected_variant_ref: VariantRef | None = None,
    ) -> OperationResult[SourceMutationResult]:
        active = self._lifecycle.active
        if active is None or active.variant is None or active.formal_variant_ref is None:
            return _failed("ACTIVE_VARIANT_REQUIRED", "请先打开一个工程版本。", context)
        if expected_variant_ref is not None and expected_variant_ref != active.formal_variant_ref:
            return _conflict("ACTIVE_VARIANT_IDENTITY_CHANGED", "解析期间活动工程版本已变化。", context)
        if expected_project_revision is not None and expected_project_revision != active.project.envelope.revision:
            return _conflict("ACTIVE_PROJECT_REVISION_CHANGED", "解析期间活动工程已发生修改。", context)
        if expected_variant_revision is not None and expected_variant_revision != active.variant.revision:
            return _conflict("ACTIVE_VARIANT_REVISION_CHANGED", "解析期间活动版本已发生修改。", context)
        expected_project_revision = (
            active.project.envelope.revision if expected_project_revision is None else expected_project_revision
        )
        expected_variant_revision = (
            active.variant.revision if expected_variant_revision is None else expected_variant_revision
        )
        try:
            source_request, initial_states = _split_initial_entry_states(request)
            prepared = self._preparer.prepare_source(source_request, context, role="primary", common_options=())
            prepared = _apply_initial_entry_states(prepared, initial_states)
            registry = _source_registry(active.project)
            registration = migrate_legacy_source_registry(
                active.project_ref.identity.value,
                (prepared.to_dict(),),
            ).sources[0]
            if any(
                item.source_id == registration.source_id
                or os.path.normcase(item.location) == os.path.normcase(registration.location)
                for item in registry.sources
            ):
                raise DomainError(ErrorCategory.CONFLICT, "PROJECT_SOURCE_DUPLICATE", "该工程来源已经存在。")

            snapshot = active.variant.snapshot()
            if any(item.namespace == prepared.baseline.fingerprint.namespace for item in snapshot.source_fingerprints):
                raise DomainError(
                    ErrorCategory.CONFLICT,
                    "PROJECT_SOURCE_IDENTITY_DUPLICATE",
                    "该来源与工程中已有来源的内容身份重复。",
                )
            existing_keys = {entry.entry_key for entry in snapshot.entries}
            if any(entry.entry_key in existing_keys for entry in prepared.baseline.entries):
                raise DomainError(
                    ErrorCategory.CONFLICT,
                    "PROJECT_SOURCE_ENTRY_DUPLICATE",
                    "该来源包含与工程中已有来源重复的条目身份。",
                )

            next_registry = SourceRegistrySnapshot(
                (*registry.sources, registration),
                registry.relations,
                registry.diagnostics,
            )
            project = _project_with_registry(active.project, next_registry)
            variant = VariantSnapshot(
                snapshot.ref,
                (*snapshot.source_fingerprints, prepared.baseline.fingerprint),
                (*snapshot.entries, *prepared.baseline.entries),
                snapshot.revision + 1,
                snapshot.label_library,
            )
            baselines = (
                *self._baselines.provide(active.project, active.formal_variant_ref, context),
                prepared.baseline,
            )
            variant_refs = _variant_refs(project)
            committed = self._lifecycle.commit_active_content(
                project,
                variant,
                context,
                expected_project_revision=expected_project_revision,
                expected_variant_revision=expected_variant_revision,
                before_publish=lambda: self._baselines.replace_many(
                    active.project_ref,
                    variant_refs,
                    baselines,
                    allow_empty=False,
                ),
            )
            if committed.outcome is not OperationOutcome.COMPLETED:
                return OperationResult(
                    committed.outcome,
                    diagnostics=committed.diagnostics,
                    counts=committed.counts,
                    run_id=committed.run_id,
                )
            return OperationResult.completed(
                SourceMutationResult(
                    registration.source_id,
                    registration.location,
                    prepared.hydration,
                    project.envelope.revision,
                    variant.revision,
                ),
                diagnostics=committed.diagnostics,
                run_id=context.run_id,
            )
        except Exception as exc:  # noqa: BLE001
            return OperationResult.from_exception(exc, run_id=context.run_id)

    def remove_source(
        self,
        locator: str,
        context: RequestContext,
    ) -> OperationResult[SourceMutationResult]:
        active = self._lifecycle.active
        if active is None or active.variant is None or active.formal_variant_ref is None:
            return _failed("ACTIVE_VARIANT_REQUIRED", "请先打开一个工程版本。", context)
        expected_project_revision = active.project.envelope.revision
        expected_variant_revision = active.variant.revision
        try:
            registry = _source_registry(active.project)
            normalized = normalize_source_location(locator)
            matches = tuple(
                source
                for source in registry.sources
                if source.source_id == locator or os.path.normcase(source.location) == os.path.normcase(normalized)
            )
            if len(matches) != 1:
                raise DomainError(
                    ErrorCategory.INPUT,
                    "PROJECT_SOURCE_NOT_FOUND" if not matches else "PROJECT_SOURCE_AMBIGUOUS",
                    "当前工程中没有唯一匹配的来源。",
                )
            removed = matches[0]
            pairs = folded_plugin_pairs(
                tuple(source.to_dict() for source in registry.sources),
                tuple(relation.to_dict() for relation in registry.relations),
            )
            removed_source_ids = {
                removed.source_id,
                *(pair.translation["source_id"] for pair in pairs if pair.primary["source_id"] == removed.source_id),
            }
            folded_import = any(pair.translation["source_id"] == removed.source_id for pair in pairs)
            content_identity = source_content_identity(removed.to_dict())
            current_baselines = self._baselines.provide(active.project, active.formal_variant_ref, context)
            baseline_matches = tuple(
                baseline
                for baseline in current_baselines
                if not folded_import
                and removed.fingerprint is not None
                and baseline.fingerprint.sha256 == removed.fingerprint
                and (content_identity is None or baseline.fingerprint.namespace.value == content_identity)
            )
            if len(baseline_matches) > 1 or (
                removed.kind is SourceKind.PLUGIN and not folded_import and len(baseline_matches) != 1
            ):
                raise DomainError(
                    ErrorCategory.CONFLICT,
                    "PROJECT_SOURCE_BASELINE_AMBIGUOUS",
                    "无法把该工程来源唯一映射到活动版本基线。",
                )

            removed_namespaces = {item.fingerprint.namespace for item in baseline_matches}
            removed_identities = {namespace.value for namespace in removed_namespaces}
            if any(
                source.source_id not in removed_source_ids
                and source_content_identity(source.to_dict()) in removed_identities
                for source in registry.sources
            ):
                raise DomainError(
                    ErrorCategory.CONFLICT,
                    "PROJECT_SOURCE_DEPENDENCY_AMBIGUOUS",
                    "该内容仍有关联来源，无法安全确定移除范围。请先检查原版与汉化来源的配对关系。",
                )
            variant_refs = _variant_refs(active.project)
            if removed_namespaces and len(variant_refs) > 1:
                raise DomainError(
                    ErrorCategory.PREREQUISITE,
                    "PROJECT_SOURCE_MULTI_VARIANT_MIGRATION_REQUIRED",
                    "当前工程包含多个版本；换源前需要先完成全部版本迁移，已拒绝产生无法打开的版本。",
                )
            next_registry = SourceRegistrySnapshot(
                tuple(source for source in registry.sources if source.source_id not in removed_source_ids),
                tuple(
                    relation
                    for relation in registry.relations
                    if not removed_source_ids.intersection({relation.from_source_id, relation.to_source_id})
                ),
                tuple(item for item in registry.diagnostics if item[1] not in removed_source_ids),
            )
            project = _project_with_registry(active.project, next_registry)
            snapshot = active.variant.snapshot()
            changed_variant = bool(removed_namespaces)
            variant = VariantSnapshot(
                snapshot.ref,
                tuple(item for item in snapshot.source_fingerprints if item.namespace not in removed_namespaces),
                tuple(entry for entry in snapshot.entries if entry.entry_key.namespace not in removed_namespaces),
                snapshot.revision + (1 if changed_variant else 0),
                snapshot.label_library,
            )
            baselines = tuple(
                item for item in current_baselines if item.fingerprint.namespace not in removed_namespaces
            )
            if not baselines and next_registry.sources:
                raise DomainError(
                    ErrorCategory.CONFLICT,
                    "PROJECT_SOURCE_BASELINE_REQUIRED",
                    "移除后剩余来源没有可用的翻译基线，工程内容未改变。请先检查来源关联。",
                )
            committed = self._lifecycle.commit_active_content(
                project,
                variant,
                context,
                expected_project_revision=expected_project_revision,
                expected_variant_revision=expected_variant_revision,
                before_publish=lambda: self._baselines.replace_many(
                    active.project_ref,
                    variant_refs,
                    baselines,
                    allow_empty=not next_registry.sources,
                ),
            )
            if committed.outcome is not OperationOutcome.COMPLETED:
                return OperationResult(
                    committed.outcome,
                    diagnostics=committed.diagnostics,
                    counts=committed.counts,
                    run_id=committed.run_id,
                )
            return OperationResult.completed(
                SourceMutationResult(
                    removed.source_id,
                    removed.location,
                    None,
                    project.envelope.revision,
                    variant.revision,
                ),
                diagnostics=committed.diagnostics,
                run_id=context.run_id,
            )
        except Exception as exc:  # noqa: BLE001
            return OperationResult.from_exception(exc, run_id=context.run_id)


def _source_registry(project: ProjectDto) -> SourceRegistrySnapshot:
    data = project.envelope.data
    try:
        return SourceRegistrySnapshot.from_project_data(data)
    except (KeyError, TypeError, ValueError):
        return migrate_legacy_source_registry(project.envelope.identity, data.get("sources", ()))


def _project_with_registry(project: ProjectDto, registry: SourceRegistrySnapshot) -> ProjectDto:
    envelope = project.envelope
    data = deepcopy(envelope.data)
    data.update(registry.to_project_data())
    return ProjectDto(
        SchemaEnvelope(
            envelope.schema_version,
            envelope.entity_type,
            envelope.identity,
            envelope.revision + 1,
            data,
        )
    )


def _variant_refs(project: ProjectDto) -> tuple[VariantRef, ...]:
    project_id = project.envelope.identity
    from transbridge.persistence.v2.ids import ProjectId

    owner = ProjectId(project_id)
    return tuple(VariantRef(VariantId(str(value)), owner) for value in project.envelope.data["variant_ids"])


def source_request_with_initial_entry_states(
    request: ProjectSourceRequest,
    states: Mapping[str, tuple[str, int]],
) -> ProjectSourceRequest:
    """Attach parsed translations to the source command without exposing a second commit."""

    if any(key == _INITIAL_ENTRY_STATES_OPTION for key, _value in request.options):
        raise ValueError("source request already contains initial entry states")
    payload = [
        {"local_key": str(local_key), "translation": str(translation), "stage": int(stage)}
        for local_key, (translation, stage) in sorted(states.items())
    ]
    return ProjectSourceRequest(
        request.location,
        request.format_hint,
        request.expected_fingerprint,
        (*request.options, (_INITIAL_ENTRY_STATES_OPTION, payload)),
    )


def _split_initial_entry_states(
    request: ProjectSourceRequest,
) -> tuple[ProjectSourceRequest, dict[str, tuple[str, int]]]:
    options = dict(request.options)
    payload = options.pop(_INITIAL_ENTRY_STATES_OPTION, ())
    if not isinstance(payload, (list, tuple)):
        raise ValueError("initial source entry states must be a list")
    states: dict[str, tuple[str, int]] = {}
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("initial source entry state must be an object")
        local_key = str(item.get("local_key", ""))
        if not local_key or local_key in states:
            raise ValueError("initial source entry state keys must be unique and non-empty")
        states[local_key] = (str(item.get("translation", "")), int(item.get("stage", 0)))
    sanitized = ProjectSourceRequest(
        request.location,
        request.format_hint,
        request.expected_fingerprint,
        tuple(options.items()),
    )
    return sanitized, states


def _apply_initial_entry_states(prepared, states: Mapping[str, tuple[str, int]]):
    if not states:
        return prepared

    def apply(entry):
        state = states.get(entry.entry_key.local_key)
        current_stage = entry.stage.value if hasattr(entry.stage, "value") else int(entry.stage)
        if state is None or state == (entry.translation, current_stage):
            return entry
        return replace(
            entry,
            translation=state[0],
            stage=state[1],
            revision=entry.revision.next(),
        )

    baseline = SourceBaseline(
        prepared.baseline.fingerprint,
        tuple(apply(entry) for entry in prepared.baseline.entries),
    )
    hydration = prepared.hydration
    if hydration is not None:
        hydration = replace(hydration, entries=tuple(apply(entry) for entry in hydration.entries))
    return replace(prepared, baseline=baseline, hydration=hydration)


def _failed[T](code: str, message: str, context: RequestContext) -> OperationResult[T]:
    return OperationResult.failed(
        DomainError(ErrorCategory.PREREQUISITE, code, message),
        run_id=context.run_id,
    )


def _conflict[T](code: str, message: str, context: RequestContext) -> OperationResult[T]:
    return OperationResult.failed(
        DomainError(ErrorCategory.CONFLICT, code, message),
        run_id=context.run_id,
    )


__all__ = [
    "ProjectSourceMutationService",
    "SourceMutationResult",
    "source_request_with_initial_entry_states",
]
