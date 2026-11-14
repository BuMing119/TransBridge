# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Expand a captured export into independently projected scheme directories."""

from dataclasses import dataclass, replace
from pathlib import Path
import re

from transbridge.application.io.contracts import FormatId
from transbridge.application.io.json_export import JsonExportDraft
from transbridge.application.io.plugin_transfer import PluginTransferDraft

from .plan_view import EditableControl, EditableFieldState
from .write_batch import _HydratedWriteBatch
from .write_destinations import DESTINATION_MODE, DIRECTORY, retarget_plugin

PREFIX = "export_scheme:"


def comparison_sources(native):
    if isinstance(native, _HydratedWriteBatch):
        if native.failures:
            raise ValueError("部分来源不完整，请修复来源后再比较方案。")
        return native.drafts
    if isinstance(native, JsonExportDraft):
        return native.sources
    return (native.write if isinstance(native, PluginTransferDraft) else native,)


@dataclass(frozen=True)
class SchemeExportRequest:
    native: object
    source: object
    selected: tuple[str, ...] = ()
    directory: str = ""


def scheme_export_plan(native, source, selected=(), directory=""):
    request = SchemeExportRequest(native, source, tuple(selected), directory)
    fields = (
        *(
            EditableFieldState(PREFIX + key, label, str(key in selected).lower(), control=EditableControl.BOOLEAN)
            for key, label in source.choices
        ),
        EditableFieldState("scheme_directory", "多方案输出目录", directory),
        *native.editable_fields,
    )
    return replace(
        native,
        request=request,
        editable_fields=fields,
        output_ready=bool(directory.strip()) if selected else native.output_ready,
        target=str(Path(directory)) if selected and directory else native.target,
        mode_summary=native.mode_summary + (f"；按 {len(selected)} 个术语方案分别输出" if selected else ""),
        overwrite_risk=False if selected else native.overwrite_risk,
        backup_required=False if selected else native.backup_required,
    )


def edit_scheme_export(request, fields, edit_native):
    edits = dict(fields)
    selected = tuple(key for key, _ in request.source.choices if edits.get(PREFIX + key, "false") == "true")
    native_fields = tuple(
        (key, value) for key, value in fields if not key.startswith(PREFIX) and key != "scheme_directory"
    )
    native = edit_native(request.native, native_fields)
    return scheme_export_plan(native, request.source, selected, edits.get("scheme_directory", request.directory))


def scheme_directories(names):
    """Windows-safe directory components; disambiguate names after sanitizing."""
    used = set()
    result = []
    for name in names:
        stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")[:80] or "术语方案"
        if stem.split(".")[0].upper() in {
            "CON",
            "PRN",
            "AUX",
            "NUL",
            *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10)),
        }:
            stem = "_" + stem
        candidate, index = stem, 2
        while candidate.casefold() in used:
            candidate = f"{stem} ({index})"
            index += 1
        used.add(candidate.casefold())
        result.append(candidate)
    return tuple(result)


def expand_scheme_export(request):
    if not request.selected:
        return request.native
    if not request.directory.strip():
        raise ValueError("请选择多方案输出目录。")
    root = Path(request.directory).resolve()
    projections = request.source.resolve(request.selected)
    native = request.native.request
    sources = (
        native.sources
        if isinstance(native, JsonExportDraft)
        else native.drafts
        if isinstance(native, _HydratedWriteBatch)
        else (native,)
    )
    drafts = []
    for projection, folder in zip(projections, scheme_directories(p.profile.name for p in projections), strict=True):
        directory = root / folder
        if not directory.resolve().is_relative_to(root):
            raise ValueError("方案输出目录指向所选根目录之外，请更换输出位置。")
        for item in sources:
            transfer = isinstance(item, PluginTransferDraft)
            write = item.write if transfer else item
            if not transfer and write.format_id is FormatId.PLUGIN_SSE and not isinstance(native, JsonExportDraft):
                output = retarget_plugin(write, DIRECTORY, str(directory))
                drafts.append(replace(output, entry_projection=projection))
                continue
            filename = Path(
                (item.plugin_path if transfer else write.target_path)
                or write.source_snapshot.source.display_name
                or write.source_snapshot.source.uri
            ).name
            if isinstance(native, JsonExportDraft):
                filename = str(Path(filename).with_suffix(".json"))
            elif str(write.format_id).startswith("sst."):
                filename = str(Path(filename).with_suffix(".sst"))
            options = tuple({**dict(write.options), DESTINATION_MODE: DIRECTORY}.items()) if transfer else write.options
            output = replace(write, target_path=str(directory / filename), entry_projection=projection, options=options)
            drafts.append(replace(item, write=output) if transfer else output)
    expanded = (
        replace(native, sources=tuple(drafts), is_batch=True, output_directory=str(root))
        if isinstance(native, JsonExportDraft)
        else _HydratedWriteBatch(tuple(drafts), getattr(native, "failures", ()), str(root))
    )
    return replace(request.native, request=expanded, is_batch=True)
