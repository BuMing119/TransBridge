"""Presentation mapping for explicit JSON exports, not native format conversion."""

from dataclasses import replace
import hashlib
from pathlib import Path

from transbridge.application.io.contracts import FormatId
from transbridge.application.io.json_export import JSON_EXPORT_LABELS, JsonExportDraft
from transbridge.application.io.operation_write import HydratedWriteDraft
from transbridge.application.io.plugin_transfer import PluginTransferDraft
from transbridge.application.io.publish import ConflictPolicy

from .mappers import OperationPlanDraft
from .plan_view import EditableControl, EditableFieldState
from .write_batch import _HydratedWriteBatch, _write_batch_plan
from .write_plan import _write_plan

FORMAT_NAMES = {
    FormatId.PLUGIN_SSE: "原插件格式",
    FormatId.XML_EET: "ESP-ESM Translator XML (.xml)",
    FormatId.BINARY_EET: "ESP-ESM Translator EET (.eet)",
    FormatId.XML_XT: "xTranslator XML (.xml)",
    FormatId.SST_SSU8: "xTranslator SST SSU8 (.sst)",
    FormatId.SST_SSU9: "xTranslator SST SSU9 (.sst)",
    FormatId.JSON_PARATRANZ: "ParaTranz JSON (.json)",
    FormatId.JSON_DSD: "DSD JSON (.json)",
}


def json_export_plan(draft, fields) -> OperationPlanDraft:
    edits = dict(fields)
    value = draft.request
    if isinstance(value, JsonExportDraft):
        sources, batch = value.sources, value.is_batch
        directory = edits.get("output_directory", value.output_directory).strip()
    elif isinstance(value, _HydratedWriteBatch):
        if value.failures:
            raise ValueError("部分来源不完整，请先修复这些来源后再导出 JSON。")
        sources, batch = value.drafts, True
        directory = edits.get("output_directory", value.output_directory).strip()
    else:
        source = value.write if isinstance(value, PluginTransferDraft) else value
        if not isinstance(source, HydratedWriteDraft):
            raise TypeError("JSON export requires captured sources")
        sources, batch, directory = (source,), False, ""
    output = FormatId(edits.get("output_format", getattr(value, "format_id", "json.paratranz")))
    overwrite = edits.get("overwrite_confirmed", "false").lower() == "true"
    converted = tuple(
        replace(
            source,
            format_id=source.source_snapshot.format_id,
            target_path=(
                str(Path(directory) / Path(source.source_snapshot.source.uri).with_suffix(".json").name)
                if batch and directory
                else ""
                if batch
                else edits.get("target_path", source.target_path).strip()
            ),
            conflict_policy=ConflictPolicy.EXPLICIT_OVERWRITE if overwrite else ConflictPolicy.FAIL,
        )
        for source in sources
    )
    request = JsonExportDraft(converted, output, directory, batch)
    targets = tuple(source.target_path for source in converted)
    existing = any(path and Path(path).exists() for path in targets)
    native = "auto" if batch else converted[0].source_snapshot.format_id.value
    native_label = "各来源原格式" if batch else FORMAT_NAMES.get(converted[0].source_snapshot.format_id, "原格式")
    options = list(dict(((native, native_label), *JSON_EXPORT_LABELS)).items())
    can_sst = all(source.source_snapshot.format_id is FormatId.PLUGIN_SSE for source in converted)
    if can_sst:
        options.append(("sst.ssu9", "xTranslator SST SSU9（实验性）"))
    return OperationPlanDraft(
        request=request,
        target="；".join(filter(None, targets)) or "请选择输出位置",
        target_revision="existing" if existing else "missing",
        input_fingerprint=hashlib.sha256(
            "".join(source.source_snapshot.sha256 for source in converted).encode()
        ).hexdigest(),
        scope_summary=f"{len(converted)} 个来源文件",
        mode_summary=f"导出 {FORMAT_NAMES[output]}",
        conflict_summary="已确认覆盖" if overwrite else "同名文件需要确认",
        backup_summary="覆盖前创建备份",
        estimated_impact=(("files", len(converted)),),
        editable_fields=(
            EditableFieldState(
                "output_format", "输出格式", output.value, control=EditableControl.CHOICE, options=tuple(options)
            ),
            EditableFieldState(
                "output_directory" if batch else "target_path",
                "输出目录" if batch else "保存到",
                directory if batch else targets[0],
                required=True,
            ),
            EditableFieldState(
                "overwrite_confirmed", "覆盖确认", str(overwrite).lower(), control=EditableControl.BOOLEAN
            ),
        ),
        output_ready=all(targets),
        overwrite_risk=existing,
        overwrite_confirmed=overwrite,
        backup_required=existing,
        backup_enabled=True,
        is_batch=batch,
    )


def native_export_plan(draft):
    request = draft.request
    if not isinstance(request, JsonExportDraft):
        return draft
    if request.is_batch:
        return _write_batch_plan(_HydratedWriteBatch(request.sources, output_directory=request.output_directory))
    return _write_plan(request.sources[0])


def with_json_choices(plan):
    """Every export window names the two JSON contracts explicitly."""
    fields = list(plan.editable_fields)
    field_index = next((index for index, item in enumerate(fields) if item.field_id == "output_format"), None)
    if field_index is not None:
        current = fields[field_index]
        fields[field_index] = replace(current, options=tuple(dict((*current.options, *JSON_EXPORT_LABELS)).items()))
    else:
        value = plan.request
        if isinstance(value, PluginTransferDraft):
            return plan
        batch = isinstance(value, _HydratedWriteBatch)
        format_id = None if batch else value.format_id
        native = "auto" if batch else format_id.value
        options = ((native, "各来源原格式" if batch else FORMAT_NAMES.get(format_id, "原格式")), *JSON_EXPORT_LABELS)
        fields.insert(
            0,
            EditableFieldState(
                "output_format",
                "输出格式",
                native,
                control=EditableControl.CHOICE,
                options=tuple(dict(options).items()),
            ),
        )
    return replace(plan, editable_fields=tuple(fields))
