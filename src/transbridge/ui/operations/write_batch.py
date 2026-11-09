# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Batch write destination editing and UI plans over shared application execution."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
from pathlib import Path

from transbridge.application.io.contracts import FormatId
from transbridge.application.io.operation_write import HydratedWriteDraft
from transbridge.application.io.operation_write_batch import (
    HydratedWriteBatchWorkload,
    WriteBatchFailure as _WriteBatchFailure,
    batch_output_conflicts as batch_output_conflicts,
    isolate_batch_output_conflicts as isolate_batch_output_conflicts,
)
from transbridge.application.io.plugin_localized_sources import _snapshot_localized_sources
from transbridge.application.io.publish import ConflictPolicy as PublishConflictPolicy
from transbridge.application.io.write_formats import SST_FORMATS

from .mappers import OperationPlanDraft
from .plan_view import EditableControl, EditableFieldState

# Preserve the existing UI import while both entrypoints use application execution.
_HydratedWriteBatchWorkload = HydratedWriteBatchWorkload


@dataclass(frozen=True, slots=True)
class _HydratedWriteBatch:
    drafts: tuple[HydratedWriteDraft, ...]
    failures: tuple[_WriteBatchFailure, ...] = ()
    output_directory: str = ""
    output_format: str = "auto"

    def __post_init__(self) -> None:
        if not self.drafts and not self.failures:
            raise ValueError("批量写回至少需要一个来源文件")
        if self.output_format not in {"auto", "sst.ssu9"}:
            raise ValueError("批量输出格式必须为 auto 或 sst.ssu9")


def batch_write_target(source: Path, output_directory: str) -> str:
    """Keep source filenames when collecting several outputs in one directory."""
    return str(Path(output_directory) / source.name) if output_directory else ""


def retarget_write_batch(
    request: _HydratedWriteBatch,
    output_directory: str,
    *,
    overwrite: bool,
    pack_bsa: bool | None = None,
    output_format: str | None = None,
) -> _HydratedWriteBatch:
    mode = request.output_format if output_format is None else output_format
    if mode not in {"auto", "sst.ssu9"}:
        raise ValueError("批量输出格式必须为 auto 或 sst.ssu9")
    if mode == "sst.ssu9" and not _can_export_sst(request):
        raise ValueError("批量 SST 输出仅支持已确认的插件和 SST 来源；其他格式请保留原格式")
    changed = output_directory != request.output_directory or mode != request.output_format

    def retarget(item):
        source_format = item.source_snapshot.format_id
        target_format = (
            FormatId.SST_SSU9 if mode == "sst.ssu9" and source_format is FormatId.PLUGIN_SSE else source_format
        )
        source = Path(item.source_snapshot.source.display_name or item.source_snapshot.source.uri)
        normalize_eet_suffix = target_format is FormatId.BINARY_EET and source.suffix.lower() != ".eet"
        if target_format is not source_format:
            source = source.with_suffix(".sst")
        elif normalize_eet_suffix:
            source = source.with_suffix(".eet")
        options = dict(item.options)
        if target_format in SST_FORMATS:
            options["pack_bsa"] = False
        elif pack_bsa is not None:
            options["pack_bsa"] = pack_bsa
        return replace(
            item,
            format_id=target_format,
            target_path=batch_write_target(source, output_directory)
            if changed or target_format is not item.format_id or normalize_eet_suffix
            else item.target_path,
            conflict_policy=PublishConflictPolicy.EXPLICIT_OVERWRITE if overwrite else PublishConflictPolicy.FAIL,
            options=tuple(options.items()),
        )

    return replace(
        request,
        drafts=tuple(retarget(item) for item in request.drafts),
        failures=tuple(
            replace(item, target_path=batch_write_target(Path(item.source_path), output_directory))
            if changed and item.source_path
            else item
            for item in request.failures
        ),
        output_directory=output_directory,
        output_format=mode,
    )


def _can_export_sst(request: _HydratedWriteBatch) -> bool:
    return (
        bool(request.drafts)
        and not request.failures
        and all(item.source_snapshot.format_id in {FormatId.PLUGIN_SSE, *SST_FORMATS} for item in request.drafts)
    )


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
    fingerprint = hashlib.sha256(
        "\0".join([
            *(f"{item.source_snapshot.sha256}:{item.variant_revision}" for item in request.drafts),
            *(item.entry_projection.identity for item in request.drafts if item.entry_projection is not None),
            *(f"{item.target_path}:{item.code}:{item.message}" for item in request.failures),
        ]).encode()
    ).hexdigest()
    return OperationPlanDraft(
        request=request,
        target="；".join(path for path in targets if path) or "请选择输出目录",
        target_revision=f"{len(existing)} existing / {len(targets) - len(existing)} missing",
        input_fingerprint=fingerprint,
        scope_summary=f"{len(targets)} 个来源文件，{len(entries)} 个条目",
        mode_summary=(
            "插件生成 xTranslator SST SSU9（实验性）；已有 SST 保留原格式与原名"
            if request.output_format == "sst.ssu9"
            else "另存到指定目录，保留各来源原名"
            if request.output_directory
            else "选择输出目录，保留各来源原名"
        )
        + resource_mode
        + scheme_mode,
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
