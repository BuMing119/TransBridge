# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Build the native write requests used by desktop export and assistant publication."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from .contracts import FormatId, SourceSnapshot
from .operation_write import HydratedWriteDraft
from .operation_write_batch import WriteBatchFailure, isolate_batch_output_conflicts
from .plugin_destination import PLUGIN_DESTINATION_MODE as DESTINATION_MODE
from .plugin_transfer import PluginTransferDraft, PluginTransferPreflightService
from .publish import BackupPolicy, ConflictPolicy
from .write_formats import SST_FORMATS

DIRECTORY = "directory"
ORIGINAL = "original"


def native_write_draft(
    snapshot,
    entries,
    revision,
    request,
    output_format,
    target,
    *,
    encodings,
    overwrite,
    pack_bsa,
    entry_projection=None,
):
    return HydratedWriteDraft(
        snapshot,
        output_format,
        entries,
        target,
        revision,
        request,
        conflict_policy=ConflictPolicy.EXPLICIT_OVERWRITE if overwrite else ConflictPolicy.FAIL,
        backup_policy=BackupPolicy.REQUIRED_IF_EXISTS,
        options=(
            ("pack_bsa", pack_bsa),
            *encodings.read_options().items(),
            ("output_encoding", encodings.output_encoding),
            ("target_language", encodings.target_language),
        ),
        entry_projection=entry_projection,
    )


def directory_output_filename(source, output_format):
    filename = Path(source)
    suffix = {
        "json.paratranz": ".json",
        "json.dsd": ".json",
        "sst.ssu8": ".sst",
        "sst.ssu9": ".sst",
        "binary.eet": ".eet",
        "xml.eet": ".xml",
        "xml.xt": ".xml",
    }.get(output_format)
    return (filename.with_suffix(suffix) if suffix else filename).name


def plugin_source_path(snapshot: SourceSnapshot) -> str:
    path = snapshot.source.uri
    return path if snapshot.content is not None and Path(path).is_absolute() else ""


def plugin_filename(snapshot: SourceSnapshot) -> str:
    return Path(plugin_source_path(snapshot) or snapshot.source.display_name or snapshot.source.uri).name


def retarget_plugin(draft: HydratedWriteDraft, mode: str, directory: str = "") -> HydratedWriteDraft:
    options = dict(draft.options)
    if mode == ORIGINAL:
        target = plugin_source_path(draft.source_snapshot)
    elif mode == DIRECTORY:
        target = str(Path(directory) / plugin_filename(draft.source_snapshot)) if directory else ""
    else:
        raise ValueError("请选择有效的插件输出方式。")
    options[DESTINATION_MODE] = mode
    return replace(draft, target_path=target, options=tuple(options.items()))


@dataclass(frozen=True, slots=True)
class NativeWriteBatch:
    drafts: tuple[HydratedWriteDraft, ...]
    failures: tuple[WriteBatchFailure, ...] = ()
    output_directory: str = ""
    output_format: str = "auto"
    destination_mode: str = DIRECTORY

    def __post_init__(self) -> None:
        if not self.drafts and not self.failures:
            raise ValueError("批量写回至少需要一个来源文件")
        if self.output_format not in {"auto", "sst.ssu9"}:
            raise ValueError("批量输出格式必须为 auto 或 sst.ssu9")


def batch_write_target(source: Path, output_directory: str) -> str:
    """Keep source filenames when collecting several outputs in one directory."""
    return str(Path(output_directory) / source.name) if output_directory else ""


def retarget_write_batch(
    request: NativeWriteBatch,
    output_directory: str,
    *,
    overwrite: bool,
    pack_bsa: bool | None = None,
    output_format: str | None = None,
    destination_mode: str | None = None,
) -> NativeWriteBatch:
    mode = request.output_format if output_format is None else output_format
    if mode not in {"auto", "sst.ssu9"}:
        raise ValueError("批量输出格式必须为 auto 或 sst.ssu9")
    if mode == "sst.ssu9" and not can_export_batch_sst(request):
        raise ValueError("批量 SST 输出仅支持已确认的插件和 SST 来源；其他格式请保留原格式")
    destination = request.destination_mode if destination_mode is None else destination_mode
    if destination not in {DIRECTORY, ORIGINAL}:
        raise ValueError("请选择有效的插件输出方式。")
    if mode != "auto":
        destination = DIRECTORY
    if destination == ORIGINAL and not all_plugins(request):
        raise ValueError("批量写回原插件仅支持全为游戏插件的来源。")
    changed = (
        output_directory != request.output_directory
        or mode != request.output_format
        or destination != request.destination_mode
    )

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
        options.pop(DESTINATION_MODE, None)
        if target_format in SST_FORMATS:
            options["pack_bsa"] = False
        elif pack_bsa is not None:
            options["pack_bsa"] = pack_bsa
        updated = replace(
            item,
            format_id=target_format,
            target_path=batch_write_target(source, output_directory)
            if changed or target_format is not item.format_id or normalize_eet_suffix
            else item.target_path,
            conflict_policy=ConflictPolicy.EXPLICIT_OVERWRITE if overwrite else ConflictPolicy.FAIL,
            options=tuple(options.items()),
        )
        if target_format is FormatId.PLUGIN_SSE and (
            destination == ORIGINAL or output_directory or changed or not item.target_path
        ):
            return retarget_plugin(updated, destination, output_directory)
        return updated

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
        destination_mode=destination,
    )


def can_export_batch_sst(request: NativeWriteBatch) -> bool:
    return (
        bool(request.drafts)
        and not request.failures
        and all(item.source_snapshot.format_id in {FormatId.PLUGIN_SSE, *SST_FORMATS} for item in request.drafts)
    )


def all_plugins(request: NativeWriteBatch) -> bool:
    return not request.failures and all(
        item.source_snapshot.format_id is FormatId.PLUGIN_SSE for item in request.drafts
    )


def preflight_write_batch(value, service, **kwargs):
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
                WriteBatchFailure(
                    item.target_path, Path(item.source_snapshot.source.uri).name, "WRITE_PREFLIGHT_FAILED", str(exc)
                )
            )
    return isolate_batch_output_conflicts((*checked, *value.failures))
