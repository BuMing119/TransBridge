# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Editable write plans for current sources and selected target plugins."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from transbridge.application.io.contracts import FormatId
from transbridge.application.io.operation_write import HydratedWriteDraft
from transbridge.application.io.plugin_localized_sources import _snapshot_localized_sources
from transbridge.application.io.plugin_transfer import PluginTransferDraft
from transbridge.application.io.publish import BackupPolicy, ConflictPolicy as PublishConflictPolicy
from transbridge.application.io.write_formats import SST_FORMATS, write_artifact_paths
from transbridge.ui.text_encoding import export_encoding_summary

from .mappers import OperationPlanDraft
from .plan_view import EditableControl, EditableFieldState
from .write_destinations import (
    DESTINATION_MODE,
    DIRECTORY,
    ORIGINAL,
    plugin_destination_fields,
    plugin_transfer_field,
    retarget_plugin,
)


def single_write_plan(request, plugin_path, values, directory=""):
    options = dict(request.options)
    previous_mode = options.pop(DESTINATION_MODE, "")
    request = replace(request, options=tuple(options.items()))
    if request.format_id is not FormatId.PLUGIN_SSE:
        return _write_plan(request)
    if plugin_path:
        return _write_plan(PluginTransferDraft(request, plugin_path))
    directory = str(values.get("output_directory", directory) or "").strip()
    mode = values.get("destination_mode")
    if not mode:
        if "output_directory" in values:
            mode = DIRECTORY
        elif not values.get("target_path") and (previous_mode or not request.target_path):
            mode = previous_mode or DIRECTORY
    if mode:
        request = retarget_plugin(request, mode, directory)
    return _write_plan(request, directory)


def _write_plan(value: HydratedWriteDraft | PluginTransferDraft, directory: str = "") -> OperationPlanDraft:
    transfer = isinstance(value, PluginTransferDraft)
    request = value.write if transfer else value
    artifact_paths = (
        ()
        if not request.target_path.strip()
        else (request.target_path,)
        if transfer
        else write_artifact_paths(
            request.source_snapshot, request.target_path, request.options, format_id=request.format_id
        )
    )
    target_exists = any(Path(path).exists() for path in artifact_paths)
    stages = tuple(item.stage for item in request.entries)
    plugin_source = request.source_snapshot.format_id is FormatId.PLUGIN_SSE
    destination_mode = dict(request.options).get(DESTINATION_MODE, "")
    plugin_fields = (plugin_transfer_field(value.plugin_path if transfer else ""),) if plugin_source or transfer else ()
    if not directory and destination_mode == DIRECTORY and request.target_path:
        directory = str(Path(request.target_path).parent)
    resource_mode = (
        "；本地化资源打包为 BSA"
        if request.format_id is FormatId.PLUGIN_SSE and dict(request.options).get("pack_bsa", False)
        else "；本地化资源输出散装 STRINGS"
        if request.format_id is FormatId.PLUGIN_SSE and _snapshot_localized_sources(request.source_snapshot)
        else ""
    )
    projection = request.entry_projection
    profile = getattr(projection, "profile", None)
    scheme_mode = "" if projection is None else f"；译名方案：{profile.name if profile else '项目默认译名'}"
    encoding_summary = export_encoding_summary(request)
    return OperationPlanDraft(
        request=value,
        target=request.target_path or ("定位后写回原插件" if destination_mode == ORIGINAL else "请选择输出位置"),
        target_revision="existing" if target_exists else "missing",
        input_fingerprint=request.source_snapshot.sha256 + ("" if projection is None else f":{projection.identity}"),
        scope_summary=f"{len(request.entries)} 个项目条目",
        mode_summary=(
            "将当前译文匹配到所选插件，预检后写出"
            if transfer
            else "覆盖已定位的原插件，覆盖前备份"
            if destination_mode == ORIGINAL
            else "保留原插件文件名，另存到所选目录"
            if destination_mode == DIRECTORY
            else "写出 xTranslator SST（实验性），完整回读验证后写出"
            if request.format_id in SST_FORMATS
            else "按原格式写出 ESP-ESM Translator EET（实验性），完整回读验证后写出"
            if request.format_id is FormatId.BINARY_EET
            else "导出当前文件的译文，验证后写出"
        )
        + resource_mode
        + scheme_mode
        + (f"；{encoding_summary}" if encoding_summary else ""),
        text_encoding_summary=export_encoding_summary(request, native=True),
        conflict_summary="已确认覆盖现有文件"
        if request.conflict_policy is PublishConflictPolicy.EXPLICIT_OVERWRITE
        else "目标已存在时停止",
        backup_summary="覆盖现有文件前先创建备份",
        estimated_impact=(
            (("objects", len(request.entries)),)
            if transfer
            else (("objects", len(request.entries)), ("files", len(artifact_paths)))
        ),
        editable_fields=(
            *(
                (
                    EditableFieldState(
                        "output_format",
                        "输出格式",
                        request.format_id.value,
                        control=EditableControl.CHOICE,
                        options=(("plugin.sse", "原插件格式"), ("sst.ssu9", "xTranslator SST SSU9（实验性）")),
                    ),
                )
                if plugin_source
                else ()
            ),
            *plugin_fields,
            *(plugin_destination_fields(directory, destination_mode) if plugin_source else ()),
            EditableFieldState(
                "target_path", "输出路径", "" if destination_mode else request.target_path, required=True
            ),
            *(
                (
                    EditableFieldState(
                        "pack_bsa",
                        "资源输出",
                        "true" if dict(request.options).get("pack_bsa", False) else "false",
                        control=EditableControl.BOOLEAN,
                    ),
                )
                if request.format_id is FormatId.PLUGIN_SSE
                and (transfer or _snapshot_localized_sources(request.source_snapshot))
                else ()
            ),
            EditableFieldState(
                "overwrite_confirmed",
                "覆盖确认",
                "true" if request.conflict_policy is PublishConflictPolicy.EXPLICIT_OVERWRITE else "false",
                control=EditableControl.BOOLEAN,
            ),
        ),
        output_ready=bool(request.target_path.strip()),
        locked_count=sum(stage == 9 for stage in stages),
        hidden_count=sum(stage == -1 for stage in stages),
        overwrite_risk=target_exists,
        overwrite_confirmed=request.conflict_policy is PublishConflictPolicy.EXPLICIT_OVERWRITE,
        backup_required=target_exists,
        backup_enabled=request.backup_policy is not BackupPolicy.NONE,
        expected_side_effects=(
            "验证后写出所选插件及需要的 Strings；现有输出覆盖前备份"
            if transfer
            else "验证通过后提交插件及全部 Strings；提交异常时恢复原有输出"
            if len(artifact_paths) > 1
            else "验证通过后原子替换一个正式输出文件",
        ),
    )
