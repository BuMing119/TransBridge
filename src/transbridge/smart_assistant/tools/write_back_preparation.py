"""Freeze explicitly selected assistant sources and authorize their exact outputs."""

from dataclasses import dataclass
import os
from pathlib import Path

from transbridge.application.assistant_requests.models import RequestError
from transbridge.application.contracts import RequestContext
from transbridge.application.io import FormatId
from transbridge.application.io.operation_write import HydratedWriteDraft, HydratedWritePreflightService
from transbridge.application.io.operation_write_batch import isolate_batch_output_conflicts
from transbridge.application.io.plugin_localized_sources import _snapshot_localized_sources
from transbridge.application.io.plugin_write import plugin_artifact_paths
from transbridge.application.io.publish import BackupPolicy, ConflictPolicy
from transbridge.application.terminology_profiles import TerminologyProfileWriteProjectionSource

from .file_undo_capture import authorize_file_outputs

_FORMATS = {"esp": FormatId.PLUGIN_SSE, "strings": FormatId.PLUGIN_SSE, "eet": FormatId.XML_EET, "xt": FormatId.XML_XT}


@dataclass(frozen=True, slots=True)
class PreparedWriteBack:
    source_keys: tuple[str, ...]
    preflights: tuple
    capture_paths: tuple[str, ...]

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(item.draft.target_path for item in self.preflights)

    @property
    def is_batch(self) -> bool:
        return len(self.preflights) > 1


def prepare_write_back(args: dict, ctx) -> PreparedWriteBack:
    target = str(args.get("target", "")).lower()
    if target not in {*_FORMATS, "auto"}:
        raise RequestError("WRITE_TARGET_INVALID", "无效的 target；可选 esp/eet/xt/strings/auto。")
    selected = _selected_sources(args, ctx)
    if target == "auto" and "source_keys" not in args:
        raise RequestError("WRITE_SOURCE_SELECTION_REQUIRED", "target=auto 必须显式提供 source_keys。")
    directory, file_path = _path_argument(args, "output_dir"), _path_argument(args, "path")
    if len(selected) > 1 and file_path:
        raise RequestError("WRITE_BATCH_PATH_INVALID", "批量写回请使用 output_dir，不支持所有来源共用一个 path。")
    if target == "strings" and not directory:
        raise RequestError("OUTPUT_DIRECTORY_REQUIRED", "请提供 output_dir；本地化插件与 Strings 将一起输出。")
    packed = args.get("pack_bsa", False)
    if not isinstance(packed, bool):
        raise RequestError("WRITE_BSA_OPTION_INVALID", "pack_bsa 必须是布尔值，默认 false。")
    request = getattr(ctx, "request_context", None) or getattr(ctx, "runtime_context", None)
    roots = tuple(getattr(request, "authorized_roots", ()) or getattr(ctx, "authorized_roots", ()) or ())
    if not roots:
        raise RequestError("PATH_GRANT_REQUIRED", "写回缺少授权目录。")
    base = Path(dict(getattr(request, "metadata", ()) or ()).get("working_directory") or roots[0])
    request = request or RequestContext("smart-assistant-writer", authorized_roots=roots)
    drafts = []
    for key, slot in selected:
        snapshot, collection = getattr(slot, "source_snapshot", None), getattr(slot, "collection", None)
        if not collection:
            raise RequestError("COLLECTION_NOT_LOADED", f"来源没有加载翻译集合：{key}")
        if snapshot is None or (target != "auto" and snapshot.format_id is not _FORMATS[target]):
            raise RequestError("SOURCE_SNAPSHOT_REQUIRED", f"来源缺少匹配的快照，请先解析对应源文件：{key}")
        if snapshot.format_id not in set(_FORMATS.values()):
            raise RequestError("SOURCE_FORMAT_UNSUPPORTED", f"来源格式不支持写回：{key}")
        source = Path(snapshot.source.uri)
        destination = (
            Path(directory) / source.name
            if directory and (target == "strings" or not file_path)
            else Path(file_path or source)
        )
        # Keep lexical components for the authorization policy to reject links.
        path = str(Path(os.path.abspath(destination if destination.is_absolute() else base / destination)))
        options = (("pack_bsa", packed and snapshot.format_id is FormatId.PLUGIN_SSE),)
        authorize_file_outputs(ctx, plugin_artifact_paths(snapshot, path, options))
        drafts.append(
            HydratedWriteDraft(
                snapshot,
                snapshot.format_id,
                tuple(entry.snapshot() for entry in collection),
                path,
                collection.collection_revision.value,
                request,
                conflict_policy=ConflictPolicy.EXPLICIT_OVERWRITE
                if args.get("overwrite", False)
                else ConflictPolicy.FAIL,
                backup_policy=BackupPolicy.REQUIRED_IF_EXISTS,
                options=options,
            )
        )
    if packed and not any(_snapshot_localized_sources(item.source_snapshot) for item in drafts):
        raise RequestError("WRITE_BSA_UNSUPPORTED", "所选来源没有可打包的本地化 STRINGS。")
    factory = getattr(ctx, "terminology_profile_service_factory", None)
    projection = (
        TerminologyProfileWriteProjectionSource(
            factory.profile_service_for, base_snapshot_for=factory.base_terminology_snapshot
        )
        if factory is not None
        else None
    )
    service = HydratedWritePreflightService(entry_projection=projection)
    frozen = service.freeze_projection(drafts[0])
    checked = isolate_batch_output_conflicts(
        tuple(service.preflight(item, frozen_projection=frozen) for item in drafts)
    )
    captures = tuple(
        dict.fromkeys(path for item in checked if item.ready for path, _fingerprint in item.artifact_fingerprints)
    )
    return PreparedWriteBack(tuple(key for key, _slot in selected), checked, captures)


def _selected_sources(args, ctx) -> tuple:
    if "source_keys" not in args:
        slot = getattr(ctx, "active_slot", None)
        if slot is None or not getattr(slot, "collection", None):
            raise RequestError("COLLECTION_NOT_LOADED", "当前没有加载翻译集合。")
        return ((getattr(ctx, "active_key", "") or "active", slot),)
    keys = args["source_keys"]
    if not isinstance(keys, list) or not keys or any(not isinstance(key, str) or not key.strip() for key in keys):
        raise RequestError(
            "WRITE_SOURCE_SELECTION_INVALID", "source_keys 必须是非空字符串列表，取自 list_collections。"
        )
    if len(keys) != len(set(keys)):
        raise RequestError("WRITE_SOURCE_SELECTION_DUPLICATE", "source_keys 不能重复。")
    available = getattr(ctx, "slots", {}) or {}
    missing = tuple(key for key in keys if key not in available)
    if missing:
        raise RequestError("WRITE_SOURCE_NOT_FOUND", "来源不属于当前已加载集合：" + "；".join(missing))
    return tuple((key, available[key]) for key in keys)


def _path_argument(args, name) -> str:
    value = args.get(name)
    if value is None:
        return ""
    if not isinstance(value, (str, os.PathLike)):
        raise RequestError("WRITE_PATH_INVALID", f"{name} 必须是文件系统路径。")
    return os.fspath(value).strip()
