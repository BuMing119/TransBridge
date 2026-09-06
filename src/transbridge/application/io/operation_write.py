"""Hydration-safe write planning and TaskRuntime guarded publication."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
from typing import Protocol

from transbridge.application.contracts import (
    Diagnostic,
    DomainError,
    ErrorCategory,
    OperationCounts,
    OperationOutcome,
    OperationResult,
    RequestContext,
)
from transbridge.converter.translation_entry import TranslationEntry

from .catalog import FormatCatalog, default_format_catalog
from .contracts import CapabilityLevel, FormatId, SourceDescriptor, SourceSnapshot, WriteRequest
from .mutation import EntrySnapshot
from .plugin_write import plugin_artifact_paths
from .publish import (
    BackupPolicy,
    ConflictPolicy,
    FileFingerprint,
    FormatAdapterRenderer,
    FormatRoundTripValidator,
    OsPublishFilesystem,
    PublishCoordinator,
    PublishFilesystemPort,
    PublishResult,
    PublishTarget,
)
from .stage_policy import DEFAULT_STAGE_POLICY, StageOperation, StagePolicyPort


@dataclass(frozen=True, slots=True)
class HydratedWriteDraft:
    """The S04 hydration needed to write without a mutable parser/plugin."""

    source_snapshot: SourceSnapshot
    format_id: FormatId
    entries: tuple[EntrySnapshot | TranslationEntry, ...]
    target_path: str
    variant_revision: int
    context: RequestContext
    conflict_policy: ConflictPolicy = ConflictPolicy.FAIL
    backup_policy: BackupPolicy = BackupPolicy.IF_EXISTS
    options: tuple[tuple[str, object], ...] = ()

    def __post_init__(self) -> None:
        if self.source_snapshot.format_id is not self.format_id:
            raise ValueError("hydrated write format must match its source snapshot")
        if not self.entries:
            raise ValueError("hydrated write requires immutable entry input")
        if not self.target_path.strip() or self.variant_revision < 0:
            raise ValueError("hydrated write target/revision is invalid")
        if any(not isinstance(item, (EntrySnapshot, TranslationEntry)) for item in self.entries):
            raise TypeError("hydrated write entries must be EntrySnapshot or TranslationEntry values")
        if len({item[0] for item in self.options}) != len(self.options):
            raise ValueError("hydrated write option keys must be unique")


@dataclass(frozen=True, slots=True)
class WritePreflightCheck:
    code: str
    passed: bool
    message: str
    warning: bool = False


class FrozenWriteEntryProjection(Protocol):
    @property
    def identity(self) -> str: ...

    @property
    def metadata(self) -> tuple[tuple[str, str], ...]: ...

    def project_entries(
        self,
        entries: tuple[TranslationEntry, ...],
    ) -> tuple[tuple[TranslationEntry, ...], tuple[object, ...]]: ...


class WriteEntryProjectionSource(Protocol):
    def freeze(self, project_id: str, variant_id: str) -> FrozenWriteEntryProjection: ...


_UNSET_PROJECTION = object()


@dataclass(frozen=True, slots=True)
class HydratedWritePreflight:
    draft: HydratedWriteDraft
    request_digest: str
    target_fingerprint: FileFingerprint
    checks: tuple[WritePreflightCheck, ...]
    blocked_entry_keys: tuple[str, ...] = ()
    artifact_fingerprints: tuple[tuple[str, FileFingerprint], ...] = ()
    projected_entries: tuple[TranslationEntry, ...] = ()
    projection_identity: str | None = None
    projection_metadata: tuple[tuple[str, str], ...] = ()

    @property
    def ready(self) -> bool:
        return not any(not item.passed and not item.warning for item in self.checks)


class HydratedWritePreflightService:
    def __init__(
        self,
        catalog: FormatCatalog | None = None,
        filesystem: PublishFilesystemPort | None = None,
        *,
        stage_policy: StagePolicyPort | None = None,
        entry_projection: WriteEntryProjectionSource | None = None,
    ) -> None:
        self._catalog = catalog or default_format_catalog()
        self._filesystem = filesystem or OsPublishFilesystem()
        self._stage_policy = stage_policy or DEFAULT_STAGE_POLICY
        self._entry_projection = entry_projection

    def freeze_projection(self, draft: HydratedWriteDraft) -> FrozenWriteEntryProjection | None:
        if self._entry_projection is None or draft.context.project_id is None or draft.context.variant_id is None:
            return None
        return self._entry_projection.freeze(draft.context.project_id, draft.context.variant_id)

    def preflight(
        self,
        draft: HydratedWriteDraft,
        *,
        frozen_projection: FrozenWriteEntryProjection | None | object = _UNSET_PROJECTION,
    ) -> HydratedWritePreflight:
        target = self._filesystem.canonicalize(draft.target_path)
        parent = Path(target).parent
        adapter = self._catalog.adapter(draft.format_id)
        checks: list[WritePreflightCheck] = []
        entries = _translation_entries(draft.entries)
        projection_identity = None
        projection_metadata: tuple[tuple[str, str], ...] = ()
        projection_diagnostics: tuple[object, ...] = ()
        frozen = self.freeze_projection(draft) if frozen_projection is _UNSET_PROJECTION else frozen_projection
        if frozen is not None:
            entries, projection_diagnostics = frozen.project_entries(entries)
            projection_identity = frozen.identity
            projection_metadata = frozen.metadata
        checks.append(
            WritePreflightCheck(
                "TERMINOLOGY_PROFILE_PROJECTION",
                not projection_diagnostics,
                (
                    "译名方案包含无法自动确认的位置；这些位置保留项目译文。"
                    if projection_diagnostics
                    else "本次写出使用的译名方案已固定。"
                ),
                warning=bool(projection_diagnostics),
            )
        )
        checks.append(
            WritePreflightCheck(
                "FORMAT_WRITE_CAPABILITY",
                adapter is not None
                and adapter.capabilities().write is not CapabilityLevel.UNAVAILABLE
                and adapter.capabilities().round_trip is not CapabilityLevel.UNAVAILABLE,
                "格式适配器必须同时支持写入与回读验证。",
            )
        )
        parent_ready = parent.is_dir() and os.access(parent, os.W_OK)
        checks.append(WritePreflightCheck("OUTPUT_WRITABLE", parent_ready, "输出目录不存在或不可写。"))
        checks.append(
            WritePreflightCheck(
                "ATOMIC_REPLACE_AVAILABLE",
                parent_ready and self._filesystem.atomic_replace_supported(target),
                "输出位置不支持同卷原子替换。",
            )
        )
        paths = plugin_artifact_paths(draft.source_snapshot, target, draft.options)
        fingerprints = tuple(
            (self._filesystem.canonicalize(path), self._filesystem.fingerprint(path)) for path in paths
        )
        initial = fingerprints[0][1]
        any_existing = any(value.exists for _path, value in fingerprints)
        overwrite_ready = not any_existing or draft.conflict_policy is ConflictPolicy.EXPLICIT_OVERWRITE
        checks.append(
            WritePreflightCheck(
                "OVERWRITE_CONFIRMED",
                overwrite_ready,
                "目标已存在；必须显式选择覆盖策略。",
            )
        )
        backup_ready = not any_existing or draft.backup_policy is not BackupPolicy.NONE
        checks.append(
            WritePreflightCheck(
                "BACKUP_POLICY",
                backup_ready,
                "覆盖已有目标但未启用备份。",
                warning=not backup_ready,
            )
        )
        blocked = tuple(
            entry.identity.serialize()
            for entry in entries
            if self._stage_policy.evaluate(
                entry.stage,
                entry.translation,
                StageOperation.PUBLISH,
                original=entry.original,
            ).blocks_publish
        )
        checks.append(
            WritePreflightCheck(
                "LOCKED_HIDDEN_POLICY",
                not blocked,
                "存在被阶段策略禁止发布的锁定/隐藏条目。",
            )
        )
        snapshot_content = draft.source_snapshot.content
        snapshot_valid = draft.source_snapshot.format_id is draft.format_id and (
            snapshot_content is None or hashlib.sha256(snapshot_content).hexdigest() == draft.source_snapshot.sha256
        )
        if draft.format_id is FormatId.PLUGIN_SSE:
            snapshot_valid = snapshot_valid and snapshot_content is not None
        checks.append(
            WritePreflightCheck(
                "SOURCE_SNAPSHOT_BOUND",
                snapshot_valid,
                "来源快照与格式身份不一致。",
            )
        )
        return HydratedWritePreflight(
            draft,
            _write_digest(draft, entries=entries, projection_identity=projection_identity),
            initial,
            tuple(checks),
            blocked,
            fingerprints,
            entries,
            projection_identity,
            projection_metadata,
        )


class HydratedWriteWorkload:
    """Render from hydration, validate, then publish under one runtime permit."""

    supports_cancel = True

    def __init__(
        self,
        preflight: HydratedWritePreflight,
        *,
        catalog: FormatCatalog | None = None,
        filesystem: PublishFilesystemPort | None = None,
        stage_policy: StagePolicyPort | None = None,
    ) -> None:
        if not preflight.ready:
            raise ValueError("cannot construct write workload from a blocked preflight")
        self._preflight = preflight
        self._catalog = catalog or default_format_catalog()
        self._filesystem = filesystem or OsPublishFilesystem()
        self._stage_policy = stage_policy or DEFAULT_STAGE_POLICY

    def __call__(self, run_context) -> OperationResult[dict[str, object]]:
        draft = self._preflight.draft
        entries = self._preflight.projected_entries or _translation_entries(draft.entries)
        if (
            _write_digest(
                draft,
                entries=entries,
                projection_identity=self._preflight.projection_identity,
            )
            != self._preflight.request_digest
        ):
            return _failed("WRITE_REQUEST_CHANGED", "写回输入在预检后发生变化。", run_context.ref.run_id)
        adapter = self._catalog.adapter(draft.format_id)
        if adapter is None:
            return _failed("FORMAT_ADAPTER_UNAVAILABLE", "写回格式适配器不可用。", run_context.ref.run_id)
        request_metadata = dict(draft.context.metadata)
        request_metadata.update(self._preflight.projection_metadata)
        request_context = replace(
            draft.context,
            run_id=run_context.ref.run_id,
            metadata=tuple(sorted(request_metadata.items())),
        )
        options = dict(draft.options)
        options["source_authority"] = "hydration-v2"
        if draft.format_id is FormatId.PLUGIN_SSE:
            options.setdefault("language", dict(draft.source_snapshot.metadata).get("localized_language", "english"))
        request = WriteRequest(
            target=SourceDescriptor(draft.target_path, display_name=Path(draft.target_path).name),
            format_id=draft.format_id,
            entries=entries,
            variant_revision=draft.variant_revision,
            context=request_context,
            source_snapshot=draft.source_snapshot,
            options=tuple(options.items()),
            cancellation=run_context.cancellation,
            stage_policy=self._stage_policy,
        )
        if len(self._preflight.artifact_fingerprints) > 1:
            from .publish.plugin_bundle import PluginBundlePublisher

            result = PluginBundlePublisher(self._filesystem, adapter).publish(
                request,
                self._preflight.artifact_fingerprints,
                conflict_policy=draft.conflict_policy,
                backup_policy=draft.backup_policy,
                commit_guard=run_context.publish_commit_guard(),
            )
            return _with_projection_metadata(result, self._preflight.projection_metadata)
        target = PublishTarget(
            draft.target_path,
            conflict_policy=draft.conflict_policy,
            backup_policy=draft.backup_policy,
            expected_fingerprint=self._preflight.target_fingerprint,
        )
        coordinator = PublishCoordinator(self._filesystem)
        result = coordinator.publish(
            request,
            target,
            renderer=FormatAdapterRenderer(adapter),
            validator=FormatRoundTripValidator(adapter, self._filesystem),
            commit_guard=run_context.publish_commit_guard(),
        )
        return _with_projection_metadata(
            _operation_result(result, run_context.ref.run_id),
            self._preflight.projection_metadata,
        )


def _translation_entries(
    entries: tuple[EntrySnapshot | TranslationEntry, ...],
) -> tuple[TranslationEntry, ...]:
    output = []
    for item in entries:
        if isinstance(item, TranslationEntry):
            output.append(replace(item))
            continue
        output.append(
            TranslationEntry(
                id=item.legacy_id,
                key=item.entry_key.local_key,
                original=item.original,
                translation=item.translation,
                stage=item.stage,
                context=item.context,
                entry_key=item.entry_key,
                external_refs=item.external_refs,
                revision=item.revision,
                provenance=item.provenance,
                metadata=item.metadata,
                string_id=item.string_id,
            )
        )
    return tuple(output)


def _write_digest(
    draft: HydratedWriteDraft,
    *,
    entries: tuple[TranslationEntry, ...] | None = None,
    projection_identity: str | None = None,
) -> str:
    frozen_entries = _translation_entries(draft.entries) if entries is None else entries
    payload = {
        "source": draft.source_snapshot.sha256,
        "format": draft.format_id.value,
        "target": str(Path(draft.target_path).resolve(strict=False)),
        "variant_revision": draft.variant_revision,
        "conflict": draft.conflict_policy.value,
        "backup": draft.backup_policy.value,
        "options": list(draft.options),
        "terminology_profile_projection": projection_identity,
        "entries": [
            {
                "key": entry.identity.serialize(),
                "revision": entry.revision.value,
                "original": entry.original,
                "translation": entry.translation,
                "stage": entry.stage,
                "string_id": entry.string_id,
            }
            for entry in frozen_entries
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _operation_result(result: PublishResult, run_id: str) -> OperationResult[dict[str, object]]:
    if result.outcome in {OperationOutcome.COMPLETED, OperationOutcome.PARTIAL}:
        value = {
            "outcomes": (
                {
                    "object_ref": result.target_path,
                    "label": Path(result.target_path).name,
                    "status": "succeeded",
                    "code": result.code,
                    "retryable": False,
                },
            ),
            "manifest": None if result.manifest is None else result.manifest.target_path,
            "backup_path": result.backup_path,
        }
        artifacts = (result.target_path,) if result.published else ()
        if result.outcome is OperationOutcome.COMPLETED:
            return OperationResult.completed(
                value,
                counts=OperationCounts(succeeded=1),
                artifact_refs=artifacts,
                run_id=run_id,
            )
        return OperationResult.partial(
            value,
            counts=OperationCounts(succeeded=1, failed=1),
            diagnostics=(Diagnostic(result.code, result.message),),
            artifact_refs=artifacts,
            run_id=run_id,
        )
    if result.outcome is OperationOutcome.CANCELLED:
        return OperationResult.cancelled(run_id=run_id)
    return _failed(result.code, result.message, run_id)


def _failed(code: str, message: str, run_id: str) -> OperationResult:
    return OperationResult.failed(
        DomainError(ErrorCategory.EXTERNAL, code, message),
        run_id=run_id,
    )


def _with_projection_metadata(
    result: OperationResult[dict[str, object]],
    metadata: tuple[tuple[str, str], ...],
) -> OperationResult[dict[str, object]]:
    if not metadata or result.value is None:
        return result
    value = dict(result.value)
    value["terminology_profile"] = dict(metadata)
    return replace(result, value=value)
