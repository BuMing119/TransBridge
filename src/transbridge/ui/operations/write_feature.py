# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Write operation composition, immutable drafts, and preflight ownership."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from pathlib import Path
from threading import RLock

from transbridge.application.io.contracts import FormatId
from transbridge.application.io.json_export import (
    JSON_EXPORT_FORMATS,
    JsonExportDraft,
    JsonExportWorkload,
    preflight_json_export,
)
from transbridge.application.io.operation_write import HydratedWriteDraft, HydratedWriteWorkload
from transbridge.application.io.plugin_transfer import (
    PluginTransferDraft,
    PluginTransferPreflightService,
    PluginTransferWorkload,
)
from transbridge.application.io.publish import BackupPolicy, ConflictPolicy as PublishConflictPolicy
from transbridge.application.tasks import OwnerRef, TaskRuntime
from transbridge.config.text_encoding import TextEncodings

from .errors import OperationCompositionError
from .facade import OperationFeatureAdapter
from .mappers import OperationPlanDraft, WriteOperationMapper
from .plan_view import OperationKind
from .preflight_view import PreflightCheckState, PreflightCheckStatus
from .production_support import blocked as _blocked, operation_request as _operation_request, trim_cache as _trim_cache
from .runtime_adapter import OperationTaskRequest
from .write_batch import (
    _HydratedWriteBatch,
    _HydratedWriteBatchWorkload,
    _write_batch_plan,
    _WriteBatchFailure,
    batch_output_conflicts,
    batch_write_target,
    isolate_batch_output_conflicts,
    retarget_write_batch,
)
from .write_json_export import json_export_plan, native_export_plan, with_json_choices
from .write_plan import single_write_plan
from .write_schemes import SchemeExportRequest, edit_scheme_export, expand_scheme_export, scheme_export_plan


def build_write_feature(context_factory, operation_tasks, write_preflights, *, text_encodings=None):
    write_cache: dict[int, object] = {}
    cache_lock = RLock()
    transfer_preflights = PluginTransferPreflightService(write_preflights=write_preflights)

    def write_checks(value: object) -> tuple[PreflightCheckState, ...]:
        if isinstance(value, SchemeExportRequest):
            expanded = expand_scheme_export(value)
            checks = write_checks(expanded.request)
            with cache_lock:
                write_cache[id(value)] = expanded
                _trim_cache(write_cache)
            return checks
        if isinstance(value, JsonExportDraft):
            checked = preflight_json_export(value, write_preflights)
            with cache_lock:
                write_cache[id(value)] = checked
                _trim_cache(write_cache)
            codes = Counter(item.code for item in checked.checks)
            return tuple(
                replace(_display_check(item), check_id=item.code if codes[item.code] == 1 else f"{item.code}_{index}")
                for index, item in enumerate(checked.checks)
            )
        if isinstance(value, PluginTransferDraft):
            checked = transfer_preflights.preflight(value)
            with cache_lock:
                write_cache[id(value)] = checked
                _trim_cache(write_cache)
            return tuple(
                _display_check(item, include_message=item.code.startswith("TRANSFER_")) for item in checked.checks
            )
        if isinstance(value, _HydratedWriteBatch):
            checked_batch = _check_batch(value, write_preflights)
            with cache_lock:
                write_cache[id(value)] = checked_batch
                _trim_cache(write_cache)
            conflicts = batch_output_conflicts(checked_batch)
            runnable = any(not isinstance(item, _WriteBatchFailure) and item.ready for item in checked_batch)
            checks = [
                PreflightCheckState(
                    "BATCH_OUTPUT_DISJOINT",
                    "批量输出互不重叠",
                    PreflightCheckStatus.WARNING
                    if conflicts and runnable
                    else PreflightCheckStatus.BLOCKED
                    if conflicts
                    else PreflightCheckStatus.PASSED,
                    "\n".join(conflicts),
                )
            ]
            for index, checked in enumerate(checked_batch, 1):
                if isinstance(checked, _WriteBatchFailure):
                    checks.append(
                        PreflightCheckState(
                            f"SOURCE_{index}_{checked.code}",
                            checked.label,
                            PreflightCheckStatus.WARNING if runnable else PreflightCheckStatus.BLOCKED,
                            checked.message,
                        )
                    )
                    continue
                for item in checked.checks:
                    checks.append(
                        PreflightCheckState(
                            f"SOURCE_{index}_{item.code}",
                            f"{Path(checked.draft.source_snapshot.source.uri).name} · {_check_label(item.code)}",
                            (
                                PreflightCheckStatus.WARNING
                                if (item.warning or runnable) and not item.passed
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
        native_values = dict(values)
        json_output = native_values.get("output_format") in JSON_EXPORT_FORMATS
        if json_output:
            native_values.pop("output_format")
        plan = create_native_write(context, batch, native_values)
        plan = json_export_plan(plan, tuple(values.items())) if json_output else with_json_choices(plan)
        source = values.get("export_schemes")
        return scheme_export_plan(plan, source) if source is not None and source.choices else plan

    def create_native_write(context, batch: bool, values):
        request_context = context_factory(context)
        encodings = getattr(context, "text_encodings", None)
        if encodings is None:
            encodings = TextEncodings() if text_encodings is None else text_encodings(request_context)
        pack_bsa = bool(values.get("pack_bsa", False))
        options = (
            ("pack_bsa", pack_bsa),
            *encodings.read_options().items(),
            ("output_encoding", encodings.output_encoding),
            ("target_language", encodings.target_language),
        )
        if batch:
            drafts = []
            failures = []
            output_directory = str(values.get("output_directory") or "").strip()
            overwrite = bool(values.get("overwrite_confirmed", False))
            for slot_key, slot in getattr(context, "slots", {}).items():
                captured_source = getattr(getattr(slot, "source_snapshot", None), "source", None)
                source_ref = (
                    getattr(captured_source, "display_name", None)
                    or getattr(captured_source, "uri", None)
                    or getattr(slot, "esp_path", None)
                    or getattr(slot, "eet_path", None)
                    or getattr(slot, "xt_path", None)
                    or str(slot_key)
                )
                try:
                    source = Path(source_ref)
                    target = batch_write_target(source, output_directory)
                except (TypeError, ValueError) as exc:
                    failures.append(
                        _WriteBatchFailure(
                            str(source_ref), str(slot_key), "WRITE_SOURCE_INVALID", str(exc), str(source_ref)
                        )
                    )
                    continue
                if any(getattr(slot, name, None) is None for name in ("source_snapshot", "format_id", "collection")):
                    failures.append(
                        _WriteBatchFailure(
                            str(target),
                            source.name,
                            "WRITE_SOURCE_INCOMPLETE",
                            "来源快照、格式或译文集合不完整。",
                            str(source),
                        )
                    )
                    continue
                try:
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
                            options=options,
                            entry_projection=values.get("entry_projection"),
                        )
                    )
                except (TypeError, ValueError) as exc:
                    failures.append(
                        _WriteBatchFailure(str(target), source.name, "WRITE_SOURCE_INVALID", str(exc), str(source))
                    )
            if not drafts and not failures:
                raise OperationCompositionError("请选择至少一个来源进行写回")
            request = _HydratedWriteBatch(tuple(drafts), tuple(failures), output_directory)
            request = retarget_write_batch(
                request,
                output_directory,
                overwrite=overwrite,
                pack_bsa=pack_bsa,
                output_format=str(values.get("output_format") or "auto"),
                destination_mode=values.get("destination_mode"),
            )
            return _write_batch_plan(request)
        slot = getattr(context, "active_slot", None)
        if slot is None or any(
            getattr(slot, name, None) is None for name in ("source_snapshot", "format_id", "collection")
        ):
            raise OperationCompositionError("当前集合缺少来源文件信息，请先打开要导出的文件")
        collection = slot.collection
        entries = tuple(item.snapshot() for item in collection)
        plugin_path = str(values.get("plugin_path") or "").strip()
        target = str(values.get("target_path") or "").strip()
        overwrite = bool(values.get("overwrite_confirmed", False))
        request = HydratedWriteDraft(
            slot.source_snapshot,
            FormatId(values.get("output_format") or slot.format_id),
            entries,
            target,
            collection.collection_revision.value,
            request_context,
            conflict_policy=(PublishConflictPolicy.EXPLICIT_OVERWRITE if overwrite else PublishConflictPolicy.FAIL),
            backup_policy=BackupPolicy.REQUIRED_IF_EXISTS,
            options=options,
            entry_projection=values.get("entry_projection"),
        )
        return single_write_plan(request, plugin_path, values)

    def edit_write(draft, fields):
        if isinstance(draft.request, SchemeExportRequest):
            return edit_scheme_export(draft.request, fields, edit_write)
        edits = dict(fields)
        if edits.get("output_format", getattr(draft.request, "format_id", None)) in JSON_EXPORT_FORMATS:
            return json_export_plan(draft, fields)
        draft = native_export_plan(draft)
        return with_json_choices(edit_native_write(draft, fields))

    def edit_native_write(draft, fields):
        if isinstance(draft, OperationPlanDraft) and isinstance(draft.request, _HydratedWriteBatch):
            edits = dict(fields)
            overwrite = _edited_flag(edits, "overwrite_confirmed")
            packed = any(dict(item.options).get("pack_bsa", False) for item in draft.request.drafts)
            request = retarget_write_batch(
                draft.request,
                edits.get("output_directory", draft.request.output_directory).strip(),
                overwrite=overwrite,
                pack_bsa=_edited_flag(edits, "pack_bsa", packed),
                output_format=edits.get("output_format"),
                destination_mode=edits.get("destination_mode"),
            )
            return _write_batch_plan(request)
        value = _write_request(draft)
        request = value.write if isinstance(value, PluginTransferDraft) else value
        edits = dict(fields)
        output_format = FormatId(edits.get("output_format", request.format_id))
        target = edits.get("target_path", request.target_path if output_format is request.format_id else "").strip()
        overwrite = _edited_flag(edits, "overwrite_confirmed")
        edited = replace(
            request,
            format_id=output_format,
            target_path=target,
            conflict_policy=(PublishConflictPolicy.EXPLICIT_OVERWRITE if overwrite else PublishConflictPolicy.FAIL),
            options=tuple(
                {
                    **dict(request.options),
                    "pack_bsa": _edited_flag(edits, "pack_bsa", dict(request.options).get("pack_bsa", False)),
                }.items()
            ),
        )
        plugin_path = edits.get(
            "plugin_path", value.plugin_path if isinstance(value, PluginTransferDraft) else ""
        ).strip()
        directory = next((field.value for field in draft.editable_fields if field.field_id == "output_directory"), "")
        return single_write_plan(edited, plugin_path, edits, directory)

    def submit_write(draft, preflight, owner: OwnerRef, tasks: TaskRuntime):
        if isinstance(draft.request, SchemeExportRequest):
            with cache_lock:
                expanded = write_cache.pop(id(draft.request), None)
            if expanded is None:
                raise OperationCompositionError("多方案导出检查已失效，请重新导出。")
            return submit_write(expanded, preflight, owner, tasks)
        if isinstance(draft, OperationPlanDraft) and isinstance(draft.request, JsonExportDraft):
            request = draft.request
            with cache_lock:
                checked = write_cache.pop(id(request), None)
            if checked is None or not checked.ready:
                raise OperationCompositionError("JSON 导出检查已失效，请重试导出。")
            return operation_tasks.submit(
                OperationTaskRequest(
                    OperationKind.WRITE,
                    preflight.request_digest,
                    draft.input_fingerprint,
                    "导出 JSON 文件",
                    JsonExportWorkload(checked),
                    True,
                    tuple(source.target_path for source in request.sources),
                ),
                owner,
            )
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
            if checked_batch is None:
                raise OperationCompositionError("批量写回预检在提交前已失效")
            conflicts = tuple(
                item.reason
                for item in preflight.checks
                if item.check_id == "BATCH_OUTPUT_DISJOINT" and item.status is PreflightCheckStatus.BLOCKED
            )
            if conflicts and not preflight.ready:
                raise OperationCompositionError("批量输出路径重叠，未提交任何文件：\n" + "\n".join(conflicts))
            # Confirmation runs on the GUI thread. Reuse the worker's frozen
            # projection; each workload checks input/target identity and the
            # publisher checks fingerprints again before committing any file.
            return operation_tasks.submit(
                OperationTaskRequest(
                    OperationKind.WRITE,
                    preflight.request_digest,
                    ":".join(item.source_snapshot.sha256 for item in request.drafts),
                    f"批量写回 {len(checked_batch)} 个来源",
                    _HydratedWriteBatchWorkload(checked_batch),
                    True,
                    tuple(item.target_path for item in request.drafts),
                ),
                owner,
            )
        request = _operation_request(draft, HydratedWriteDraft)
        with cache_lock:
            checked = write_cache.pop(id(request), None)
        if checked is None or not checked.ready:
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
            return bool(slots) and any(getattr(slot, "collection", None) is not None for slot in slots)
        slot = getattr(context, "active_slot", None)
        return bool(not batch and slot is not None and getattr(slot, "collection", None) is not None)

    def discard_write(draft) -> None:
        if isinstance(draft.request, SchemeExportRequest):
            with cache_lock:
                expanded = write_cache.pop(id(draft.request), None)
            if expanded is not None:
                discard_write(expanded)
            return
        if isinstance(draft, OperationPlanDraft) and isinstance(draft.request, (_HydratedWriteBatch, JsonExportDraft)):
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


def _check_batch(value, service, **kwargs):
    checked = []
    unfrozen = next(
        (item for item in value.drafts if isinstance(item, HydratedWriteDraft) and item.entry_projection is None), None
    )
    fallback = service.freeze_projection(unfrozen) if unfrozen is not None else None
    for item in value.drafts:
        try:
            checked.append(
                PluginTransferPreflightService(service).preflight(item)
                if isinstance(item, PluginTransferDraft)
                else service.preflight(item, frozen_projection=item.entry_projection or fallback, **kwargs)
            )
        except (OSError, TypeError, ValueError) as exc:
            checked.append(
                _WriteBatchFailure(
                    item.target_path, Path(item.source_snapshot.source.uri).name, "WRITE_PREFLIGHT_FAILED", str(exc)
                )
            )
    return isolate_batch_output_conflicts((*checked, *value.failures))


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
        "BSA_LOOSE_OUTPUT_CONFLICT": "散装资源冲突",
        "SOURCE_ARCHIVE_PROTECTED": "来源 BSA 保护",
        "TRANSLATION_FIDELITY": "内容一致性",
    }.get(code, "写回检查")


def _edited_flag(edits, name: str, default: bool = False) -> bool:
    return edits.get(name, str(default)).strip().casefold() in {"1", "true", "yes", "是"}


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
