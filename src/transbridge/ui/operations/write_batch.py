# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Present shared native batch write requests in the export dialog."""

from __future__ import annotations

import hashlib
from pathlib import Path

from transbridge.application.io.contracts import FormatId
from transbridge.application.io.native_write import (
    ORIGINAL,
    NativeWriteBatch as _HydratedWriteBatch,
    all_plugins as _all_plugins,
    can_export_batch_sst as _can_export_sst,
    retarget_write_batch as retarget_write_batch,
)
from transbridge.application.io.operation_write_batch import HydratedWriteBatchWorkload
from transbridge.application.io.plugin_localized_sources import _snapshot_localized_sources
from transbridge.application.io.publish import ConflictPolicy as PublishConflictPolicy
from transbridge.ui.text_encoding import TEXT_FORMATS, export_encoding_summary

from .mappers import OperationPlanDraft
from .plan_view import EditableControl, EditableFieldState
from .write_destinations import destination_mode_field

_HydratedWriteBatchWorkload = HydratedWriteBatchWorkload


def _write_batch_plan(request: _HydratedWriteBatch) -> OperationPlanDraft:
    targets = tuple(item.target_path for item in (*request.drafts, *request.failures))
    existing = tuple(path for path in targets if path and Path(path).exists())
    entries = tuple(entry for item in request.drafts for entry in item.entries)
    overwrite = all(item.conflict_policy is PublishConflictPolicy.EXPLICIT_OVERWRITE for item in request.drafts)
    resource_mode = (
        "；本地化资源打包为 BSA"
        if any(
            item.format_id is FormatId.PLUGIN_SSE and dict(item.options).get("pack_bsa", False)
            for item in request.drafts
        )
        else "；本地化资源输出散装 STRINGS"
        if any(
            item.format_id is FormatId.PLUGIN_SSE and _snapshot_localized_sources(item.source_snapshot)
            for item in request.drafts
        )
        else ""
    )
    projection = next((item.entry_projection for item in request.drafts if item.entry_projection is not None), None)
    profile = getattr(projection, "profile", None)
    scheme_mode = "" if projection is None else f"；译名方案：{profile.name if profile else '项目默认译名'}"
    text_draft = next((item for item in request.drafts if item.source_snapshot.format_id in TEXT_FORMATS), None)
    encoding_summary = "" if text_draft is None else export_encoding_summary(text_draft, native=True)
    fingerprint = hashlib.sha256(
        "\0".join([
            *(f"{item.source_snapshot.sha256}:{item.variant_revision}" for item in request.drafts),
            *(item.entry_projection.identity for item in request.drafts if item.entry_projection is not None),
            *(f"{item.target_path}:{item.code}:{item.message}" for item in request.failures),
        ]).encode()
    ).hexdigest()
    return OperationPlanDraft(
        request=request,
        target=(
            "；".join(path for path in targets if path)
            or ("定位后写回原插件" if request.destination_mode == ORIGINAL else "请选择输出目录")
        ),
        target_revision=f"{len(existing)} existing / {len(targets) - len(existing)} missing",
        input_fingerprint=fingerprint,
        scope_summary=f"{len(targets)} 个来源文件，{len(entries)} 个条目",
        mode_summary=(
            "插件生成 xTranslator SST SSU9（实验性）；已有 SST 保留原格式与原名"
            if request.output_format == "sst.ssu9"
            else "覆盖各已定位的原插件，覆盖前备份"
            if request.destination_mode == ORIGINAL
            else "另存到指定目录，保留各来源原名"
            if request.output_directory
            else "选择输出目录，保留各来源原名"
        )
        + resource_mode
        + scheme_mode
        + (f"；{encoding_summary}" if request.output_format == "auto" and encoding_summary else ""),
        text_encoding_summary=encoding_summary,
        conflict_summary="全部覆盖已确认" if overwrite else "保留已有输出，继续写出其他文件",
        backup_summary="覆盖输出文件前创建备份",
        is_batch=True,
        estimated_impact=(("objects", len(entries)), ("files", len(targets))),
        editable_fields=(
            *(
                (
                    EditableFieldState(
                        "output_format",
                        "输出格式",
                        request.output_format,
                        control=EditableControl.CHOICE,
                        options=(("auto", "各来源原格式"), ("sst.ssu9", "xTranslator SST（插件生成 SSU9，实验性）")),
                    ),
                )
                if _can_export_sst(request)
                else ()
            ),
            *((destination_mode_field(request.destination_mode),) if _all_plugins(request) else ()),
            EditableFieldState("output_directory", "输出目录", request.output_directory, required=True),
            *(
                (
                    EditableFieldState(
                        "pack_bsa",
                        "资源输出",
                        "true"
                        if any(dict(item.options).get("pack_bsa", False) for item in request.drafts)
                        else "false",
                        control=EditableControl.BOOLEAN,
                    ),
                )
                if any(
                    item.format_id is FormatId.PLUGIN_SSE and _snapshot_localized_sources(item.source_snapshot)
                    for item in request.drafts
                )
                else ()
            ),
            EditableFieldState(
                "overwrite_confirmed",
                "覆盖确认",
                "true" if overwrite else "false",
                control=EditableControl.BOOLEAN,
            ),
        ),
        locked_count=sum(getattr(item, "stage", 0) == 9 for item in entries),
        hidden_count=sum(getattr(item, "stage", 0) == -1 for item in entries),
        output_ready=any(bool(path.strip()) for path in targets),
        # Coverage is checked per source by the domain preflight and publisher.
        # An unrelated existing target must not block the whole batch.
        overwrite_risk=False,
        overwrite_confirmed=overwrite,
        backup_required=bool(existing),
        backup_enabled=True,
        expected_side_effects=("分别验证并写出各来源，保留未完成来源和已完成结果",),
    )
