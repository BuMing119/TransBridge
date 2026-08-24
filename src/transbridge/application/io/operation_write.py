"""Hydration-safe write planning and TaskRuntime guarded publication."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path

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


@dataclass(frozen=True, slots=True)
class HydratedWritePreflight:
    draft: HydratedWriteDraft
    request_digest: str
    target_fingerprint: FileFingerprint
    checks: tuple[WritePreflightCheck, ...]
    blocked_entry_keys: tuple[str, ...] = ()

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
    ) -> None:
        self._catalog = catalog or default_format_catalog()
        self._filesystem = filesystem or OsPublishFilesystem()
        self._stage_policy = stage_policy or DEFAULT_STAGE_POLICY

    def preflight(self, draft: HydratedWriteDraft) -> HydratedWritePreflight:
        target = self._filesystem.canonicalize(draft.target_path)
        parent = Path(target).parent
        adapter = self._catalog.adapter(draft.format_id)
        checks: list[WritePreflightCheck] = []
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
        initial = self._filesystem.fingerprint(target) if parent_ready else FileFingerprint.missing()
        overwrite_ready = not initial.exists or draft.conflict_policy is ConflictPolicy.EXPLICIT_OVERWRITE
        checks.append(
            WritePreflightCheck(
                "OVERWRITE_CONFIRMED",
                overwrite_ready,
                "目标已存在；必须显式选择覆盖策略。",
            )
        )
        backup_ready = not initial.exists or draft.backup_policy is not BackupPolicy.NONE
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
            for entry in _translation_entries(draft.entries)
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
        checks.append(
            WritePreflightCheck(
                "SOURCE_SNAPSHOT_BOUND",
                snapshot_valid,
                "来源快照与格式身份不一致。",
            )
        )
        return HydratedWritePreflight(
            draft,
            _write_digest(draft),
            initial,
            tuple(checks),
            blocked,
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
        if _write_digest(draft) != self._preflight.request_digest:
            return _failed("WRITE_REQUEST_CHANGED", "写回输入在预检后发生变化。", run_context.ref.run_id)
        adapter = self._catalog.adapter(draft.format_id)
        if adapter is None:
            return _failed("FORMAT_ADAPTER_UNAVAILABLE", "写回格式适配器不可用。", run_context.ref.run_id)
        request_context = replace(draft.context, run_id=run_context.ref.run_id)
        request = WriteRequest(
            target=SourceDescriptor(draft.target_path, display_name=Path(draft.target_path).name),
            format_id=draft.format_id,
            entries=_translation_entries(draft.entries),
            variant_revision=draft.variant_revision,
            context=request_context,
            source_snapshot=draft.source_snapshot,
            options=tuple((key, value) for key, value in draft.options if key != "source_authority")
            + (("source_authority", "hydration-v2"),),
            cancellation=run_context.cancellation,
            stage_policy=self._stage_policy,
        )
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
        return _operation_result(result, run_context.ref.run_id)


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
            )
        )
    return tuple(output)


def _write_digest(draft: HydratedWriteDraft) -> str:
    payload = {
        "source": draft.source_snapshot.sha256,
        "format": draft.format_id.value,
        "target": str(Path(draft.target_path).resolve(strict=False)),
        "variant_revision": draft.variant_revision,
        "conflict": draft.conflict_policy.value,
        "backup": draft.backup_policy.value,
        "options": list(draft.options),
        "entries": [
            {
                "key": entry.identity.serialize(),
                "revision": entry.revision.value,
                "original": entry.original,
                "translation": entry.translation,
                "stage": entry.stage,
            }
            for entry in _translation_entries(draft.entries)
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
