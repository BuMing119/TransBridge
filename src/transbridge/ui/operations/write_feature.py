"""Write operation composition, immutable drafts, and preflight ownership."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from threading import RLock

from transbridge.application.io.operation_write import HydratedWriteDraft, HydratedWriteWorkload
from transbridge.application.io.plugin_transfer import (
    PluginTransferDraft,
    PluginTransferPreflightService,
    PluginTransferWorkload,
)
from transbridge.application.io.publish import BackupPolicy, ConflictPolicy as PublishConflictPolicy
from transbridge.application.tasks import OwnerRef, TaskRuntime

from .errors import OperationCompositionError
from .facade import OperationFeatureAdapter
from .mappers import OperationPlanDraft, WriteOperationMapper
from .plan_view import OperationKind
from .preflight_view import PreflightCheckState, PreflightCheckStatus
from .production_support import blocked as _blocked, operation_request as _operation_request, trim_cache as _trim_cache
from .runtime_adapter import OperationTaskRequest
from .write_batch import _HydratedWriteBatch, _HydratedWriteBatchWorkload, _write_batch_plan, batch_output_conflicts
from .write_plan import _write_plan


def build_write_feature(context_factory, operation_tasks, write_preflights):
    write_cache: dict[int, object] = {}
    cache_lock = RLock()
    transfer_preflights = PluginTransferPreflightService(write_preflights=write_preflights)

    def write_checks(value: object) -> tuple[PreflightCheckState, ...]:
        if isinstance(value, PluginTransferDraft):
            checked = transfer_preflights.preflight(value)
            with cache_lock:
                write_cache[id(value)] = checked
                _trim_cache(write_cache)
            return tuple(
                _display_check(item, include_message=item.code.startswith("TRANSFER_")) for item in checked.checks
            )
        if isinstance(value, _HydratedWriteBatch):
            frozen_projection = write_preflights.freeze_projection(value.drafts[0])
            checked_batch = tuple(
                write_preflights.preflight(item, frozen_projection=frozen_projection) for item in value.drafts
            )
            with cache_lock:
                write_cache[id(value)] = checked_batch
                _trim_cache(write_cache)
            conflicts = batch_output_conflicts(checked_batch)
            checks = [
                PreflightCheckState(
                    "BATCH_OUTPUT_DISJOINT",
                    "批量输出互不重叠",
                    PreflightCheckStatus.BLOCKED if conflicts else PreflightCheckStatus.PASSED,
                    "\n".join(conflicts),
                )
            ]
            for index, checked in enumerate(checked_batch, 1):
                for item in checked.checks:
                    checks.append(
                        PreflightCheckState(
                            f"SOURCE_{index}_{item.code}",
                            f"{Path(checked.draft.source_snapshot.source.uri).name} · {_check_label(item.code)}",
                            (
                                PreflightCheckStatus.WARNING
                                if item.warning and not item.passed
                                else PreflightCheckStatus.PASSED
                                if item.passed
                                else PreflightCheckStatus.BLOCKED
                            ),
                            "" if item.passed else item.message,
                        )
                    )
            return tuple(checks)
        if not isinstance(value, HydratedWriteDraft):
            return (_blocked("WRITE_REQUEST_INVALID", "写回请求无效"),)
        checked = write_preflights.preflight(value)
        with cache_lock:
            write_cache[id(value)] = checked
            _trim_cache(write_cache)
        return tuple(
            PreflightCheckState(
                item.code,
                _check_label(item.code),
                (
                    PreflightCheckStatus.WARNING
                    if item.warning and not item.passed
                    else PreflightCheckStatus.PASSED
                    if item.passed
                    else PreflightCheckStatus.BLOCKED
                ),
                "" if item.passed else item.message,
            )
            for item in checked.checks
        )

    def create_write(context, batch: bool, values):
        if batch:
            drafts = []
            overwrite = bool(values.get("overwrite_confirmed", False))
            request_context = context_factory(context)
            for slot in getattr(context, "slots", {}).values():
                if any(getattr(slot, name, None) is None for name in ("source_snapshot", "format_id", "collection")):
                    raise OperationCompositionError("批量写回包含缺少来源快照、格式或译文集合的文件；请补齐后重试")
                source = Path(
                    getattr(slot, "esp_path", None)
                    or getattr(slot, "eet_path", None)
                    or getattr(slot, "xt_path", None)
                    or slot.source_snapshot.source.uri
                )
                target = source.with_name(f"{source.stem}_translated{source.suffix}")
                drafts.append(
                    HydratedWriteDraft(
                        slot.source_snapshot,
                        slot.format_id,
                        tuple(item.snapshot() for item in slot.collection),
                        str(target),
                        int(getattr(context, "variant_revision", slot.collection.collection_revision.value) or 0),
                        request_context,
                        conflict_policy=(
                            PublishConflictPolicy.EXPLICIT_OVERWRITE if overwrite else PublishConflictPolicy.FAIL
                        ),
                        backup_policy=BackupPolicy.REQUIRED_IF_EXISTS,
                    )
                )
            if len(drafts) < 2:
                raise OperationCompositionError("批量写回至少需要两个已载入的来源文件")
            return _write_batch_plan(_HydratedWriteBatch(tuple(drafts)))
        slot = getattr(context, "active_slot", None)
        if slot is None or any(
            getattr(slot, name, None) is None for name in ("source_snapshot", "format_id", "collection")
        ):
            raise OperationCompositionError("当前集合缺少来源文件信息，请先打开要导出的文件")
        collection = slot.collection
        entries = tuple(item.snapshot() for item in collection)
        plugin_path = str(values.get("plugin_path") or "").strip()
        source = Path(plugin_path or getattr(slot, "esp_path", None) or slot.source_snapshot.source.uri)
        target = str(values.get("target_path") or source.with_name(f"{source.stem}_translated{source.suffix}"))
        request_context = context_factory(context)
        overwrite = bool(values.get("overwrite_confirmed", False))
        request = HydratedWriteDraft(
            slot.source_snapshot,
            slot.format_id,
            entries,
            target,
            collection.collection_revision.value,
            request_context,
            conflict_policy=(PublishConflictPolicy.EXPLICIT_OVERWRITE if overwrite else PublishConflictPolicy.FAIL),
            backup_policy=BackupPolicy.REQUIRED_IF_EXISTS,
        )
        return _write_plan(PluginTransferDraft(request, plugin_path) if plugin_path else request)

    def edit_write(draft, fields):
        if isinstance(draft, OperationPlanDraft) and isinstance(draft.request, _HydratedWriteBatch):
            edits = dict(fields)
            overwrite = edits.get("overwrite_confirmed", "false").strip().casefold() in {
                "1",
                "true",
                "yes",
                "是",
            }
            request = _HydratedWriteBatch(
                tuple(
                    replace(
                        item,
                        conflict_policy=(
                            PublishConflictPolicy.EXPLICIT_OVERWRITE if overwrite else PublishConflictPolicy.FAIL
                        ),
                    )
                    for item in draft.request.drafts
                )
            )
            return _write_batch_plan(request)
        value = _write_request(draft)
        request = value.write if isinstance(value, PluginTransferDraft) else value
        edits = dict(fields)
        target = edits.get("target_path", request.target_path).strip()
        overwrite = edits.get("overwrite_confirmed", "false").strip().casefold() in {"1", "true", "yes", "是"}
        edited = replace(
            request,
            target_path=target,
            conflict_policy=(PublishConflictPolicy.EXPLICIT_OVERWRITE if overwrite else PublishConflictPolicy.FAIL),
        )
        plugin_path = edits.get(
            "plugin_path", value.plugin_path if isinstance(value, PluginTransferDraft) else ""
        ).strip()
        return _write_plan(PluginTransferDraft(edited, plugin_path) if plugin_path else edited)

    def submit_write(draft, preflight, owner: OwnerRef, tasks: TaskRuntime):
        if isinstance(draft, OperationPlanDraft) and isinstance(draft.request, PluginTransferDraft):
            request = draft.request
            with cache_lock:
                checked = write_cache.pop(id(request), None)
            if checked is None or not checked.ready:
                raise OperationCompositionError("套用预检已失效，请重新运行预检")
            return operation_tasks.submit(
                OperationTaskRequest(
                    OperationKind.WRITE,
                    preflight.request_digest,
                    request.write.source_snapshot.sha256,
                    "将当前译文套用到插件",
                    PluginTransferWorkload(checked),
                    True,
                    (request.write.target_path,),
                ),
                owner,
            )
        if isinstance(draft, OperationPlanDraft) and isinstance(draft.request, _HydratedWriteBatch):
            request = draft.request
            with cache_lock:
                checked_batch = write_cache.pop(id(request), None)
            current = tuple(write_preflights.preflight(item) for item in request.drafts)
            conflicts = batch_output_conflicts(current)
            if conflicts:
                raise OperationCompositionError("批量输出路径重叠，未提交任何文件：\n" + "\n".join(conflicts))
            if (
                checked_batch is None
                or any(not item.ready for item in current)
                or tuple(item.artifact_fingerprints for item in current)
                != tuple(item.artifact_fingerprints for item in checked_batch)
            ):
                raise OperationCompositionError("批量写回预检在提交前已失效")
            return operation_tasks.submit(
                OperationTaskRequest(
                    OperationKind.WRITE,
                    preflight.request_digest,
                    ":".join(item.source_snapshot.sha256 for item in request.drafts),
                    f"批量写回 {len(request.drafts)} 个来源",
                    _HydratedWriteBatchWorkload(checked_batch),
                    True,
                    tuple(item.target_path for item in request.drafts),
                ),
                owner,
            )
        request = _operation_request(draft, HydratedWriteDraft)
        with cache_lock:
            checked = write_cache.pop(id(request), None)
        current = write_preflights.preflight(request)
        if checked is None or not current.ready or current.artifact_fingerprints != checked.artifact_fingerprints:
            raise OperationCompositionError("写回预检在提交前已失效")
        workload = HydratedWriteWorkload(checked)
        return operation_tasks.submit(
            OperationTaskRequest(
                OperationKind.WRITE,
                preflight.request_digest,
                request.source_snapshot.sha256,
                "写回翻译文件",
                workload,
                True,
                (request.target_path,),
            ),
            owner,
        )

    def supports_write(context, batch: bool) -> bool:
        if batch:
            slots = tuple(getattr(context, "slots", {}).values())
            return len(slots) >= 2 and all(
                all(getattr(slot, name, None) is not None for name in ("source_snapshot", "format_id", "collection"))
                for slot in slots
            )
        slot = getattr(context, "active_slot", None)
        return bool(
            not batch
            and slot is not None
            and all(getattr(slot, name, None) is not None for name in ("source_snapshot", "format_id", "collection"))
        )

    def discard_write(draft) -> None:
        if isinstance(draft, OperationPlanDraft) and isinstance(draft.request, _HydratedWriteBatch):
            request = draft.request
        else:
            request = _write_request(draft)
        with cache_lock:
            write_cache.pop(id(request), None)

    return OperationFeatureAdapter(
        OperationKind.WRITE,
        WriteOperationMapper(write_checks),
        create_write,
        submit_write,
        edit_write,
        supports_write,
        discard_write,
    )


def _check_label(code: str) -> str:
    if code == "TRANSFER_MATCH_SUMMARY":
        return "匹配统计"
    if code.startswith("TRANSFER_ITEM_"):
        return "未套用条目"
    if code.startswith("TRANSFER_"):
        return "套用检查"
    return {
        "TERMINOLOGY_PROFILE_PROJECTION": "译名方案",
        "FORMAT_WRITE_CAPABILITY": "文件格式",
        "OUTPUT_WRITABLE": "输出目录",
        "ATOMIC_REPLACE_AVAILABLE": "安全写入",
        "SOURCE_SNAPSHOT_BOUND": "来源文件",
        "SOURCE_SNAPSHOT_UNCHANGED": "来源文件未变化",
        "TARGET_CONFLICT_POLICY": "覆盖策略",
        "ENTRY_STAGE_POLICY": "条目状态",
        "TRANSLATION_FIDELITY": "内容一致性",
    }.get(code, "写回检查")


def _write_request(draft):
    if isinstance(draft, OperationPlanDraft) and isinstance(draft.request, PluginTransferDraft):
        return draft.request
    return _operation_request(draft, HydratedWriteDraft)


def _display_check(item, *, include_message: bool = False) -> PreflightCheckState:
    return PreflightCheckState(
        item.code,
        _check_label(item.code),
        PreflightCheckStatus.WARNING
        if item.warning and not item.passed
        else PreflightCheckStatus.PASSED
        if item.passed
        else PreflightCheckStatus.BLOCKED,
        item.message if include_message or not item.passed else "",
    )
