# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Freeze explicitly selected assistant sources and authorize their exact outputs."""

from dataclasses import dataclass
import os
from pathlib import Path

from transbridge.application.assistant_requests.models import RequestError
from transbridge.application.contracts import DomainError, ErrorCategory, RequestContext
from transbridge.application.io import FormatId
from transbridge.application.io.native_write import (
    DIRECTORY,
    ORIGINAL,
    NativeWriteBatch,
    directory_output_filename,
    native_write_draft,
    preflight_write_batch,
    retarget_plugin,
    retarget_write_batch,
)
from transbridge.application.io.operation_write import HydratedWritePreflightService
from transbridge.application.io.operation_write_batch import WriteBatchFailure
from transbridge.application.io.plugin_localized_sources import _snapshot_localized_sources
from transbridge.application.io.write_formats import SST_FORMATS, source_output_compatible, write_artifact_paths
from transbridge.application.projects.write_inputs import (
    ProjectPluginReferences,
    bind_plugin_write_input,
    native_write_template,
)
from transbridge.application.security.paths import PathAuthorizationPolicy, PathGrant
from transbridge.application.terminology_profiles import TerminologyProfileWriteProjectionSource
from transbridge.persistence.project_provisioning import TranslationIoProjectSourcePreparer
from transbridge.persistence.v2.ids import ProjectId, VariantId

from .file_input_access import authorize_plugin_resources, require_input_access
from .file_undo_capture import authorize_file_outputs
from .text_encoding import effective_text_encodings

_FORMATS = {"esp": FormatId.PLUGIN_SSE, "strings": FormatId.PLUGIN_SSE, "eet": FormatId.XML_EET, "xt": FormatId.XML_XT}
_STRINGS_FORMATS = frozenset({FormatId.STRINGS, FormatId.DLSTRINGS, FormatId.ILSTRINGS})


@dataclass(frozen=True, slots=True)
class PreparedWriteBack:
    source_keys: tuple[str, ...]
    preflights: tuple
    capture_paths: tuple[str, ...]

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(
            item.target_path if isinstance(item, WriteBatchFailure) else item.draft.target_path
            for item in self.preflights
        )

    @property
    def is_batch(self) -> bool:
        return len(self.preflights) > 1


def prepare_write_back(args: dict, ctx) -> PreparedWriteBack:
    target = str(args.get("target", "")).lower()
    if target not in {*_FORMATS, "sst", "auto"}:
        raise RequestError("WRITE_TARGET_INVALID", "无效的 target；可选 esp/eet/xt/sst/strings/auto。")
    selected = _selected_sources(args, ctx)
    if target == "auto" and "source_keys" not in args:
        raise RequestError("WRITE_SOURCE_SELECTION_REQUIRED", "target=auto 必须显式提供 source_keys。")
    directory, file_path = _path_argument(args, "output_dir"), _path_argument(args, "path")
    if len(selected) > 1 and file_path:
        raise RequestError("WRITE_BATCH_PATH_INVALID", "批量写回请使用 output_dir，不支持所有来源共用一个 path。")
    destination_mode = args.get("destination_mode", DIRECTORY)
    if destination_mode not in (DIRECTORY, ORIGINAL):
        raise RequestError("WRITE_DESTINATION_INVALID", "插件输出方式必须为 directory 或 original。")
    if destination_mode == ORIGINAL and (directory or file_path):
        raise RequestError("WRITE_DESTINATION_INVALID", "写回原插件时不能另外指定输出位置。")
    if target == "strings" and not directory and destination_mode != ORIGINAL:
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
    encodings = effective_text_encodings(getattr(ctx, "app_context", None) or ctx, request)
    inputs = args.get("input_plugins", {})
    if not isinstance(inputs, dict) or any(
        not isinstance(value, str) or not value.strip() for value in inputs.values()
    ):
        raise RequestError("WRITE_PLUGIN_INPUT_INVALID", "input_plugins 必须把来源键映射到本次插件文件。")
    if inputs.keys() - {key for key, _slot in selected}:
        raise RequestError("WRITE_PLUGIN_INPUT_INVALID", "input_plugins 只能指定已选来源。")
    if "input_plugin" in args and (len(selected) != 1 or inputs):
        raise RequestError("WRITE_PLUGIN_INPUT_INVALID", "单来源可用 input_plugin；多来源请按键提供 input_plugins。")
    overwrite = args.get("overwrite", False)
    if not isinstance(overwrite, bool):
        raise RequestError("WRITE_OVERWRITE_INVALID", "overwrite 必须是布尔值。")
    if directory:
        directory = str(Path(os.path.abspath(base / directory)))
    project = None
    reference_updates = {}
    project_stamp = _project_stamp(ctx)
    reference_store = getattr(ctx, "project_source_references", None)
    drafts = []
    for key, slot in selected:
        snapshot, collection = getattr(slot, "source_snapshot", None), getattr(slot, "collection", None)
        if not collection:
            raise RequestError("COLLECTION_NOT_LOADED", f"来源没有加载翻译集合：{key}")
        if snapshot is None:
            raise RequestError("SOURCE_SNAPSHOT_REQUIRED", f"来源缺少匹配的快照，请先解析对应源文件：{key}")
        # Historical target="eet" covers ESP-ESM Translator sources. Preserve
        # the snapshot's independent XML_EET/BINARY_EET format; it is not a
        # request to convert XML into EET (or the reverse).
        output_format = (
            (snapshot.format_id if snapshot.format_id in SST_FORMATS else FormatId.SST_SSU9)
            if target == "sst"
            else snapshot.format_id
            if target == "auto"
            or target == "eet"
            and snapshot.format_id is FormatId.BINARY_EET
            or target == "strings"
            and snapshot.format_id in _STRINGS_FORMATS
            else _FORMATS[target]
        )
        if not source_output_compatible(snapshot.format_id, output_format):
            raise RequestError("SOURCE_SNAPSHOT_REQUIRED", f"来源缺少匹配的快照，请先解析对应源文件：{key}")
        if snapshot.format_id not in {*_FORMATS.values(), *SST_FORMATS, *_STRINGS_FORMATS, FormatId.BINARY_EET}:
            raise RequestError("SOURCE_FORMAT_UNSUPPORTED", f"来源格式不支持写回：{key}")
        entries = tuple(entry.snapshot() for entry in collection)
        snapshot = native_write_template(slot)
        source_id = getattr(slot, "source_id", None)
        project_source = reference_store is not None and source_id and project_stamp[0] is not None
        explicit = inputs.get(key) or _path_argument(args, "input_plugin")
        needs_plugin = snapshot.format_id is FormatId.PLUGIN_SSE and output_format not in SST_FORMATS
        if explicit and not needs_plugin:
            raise RequestError("WRITE_PLUGIN_INPUT_INVALID", f"来源 {key} 的输出不需要绑定插件。")
        if needs_plugin and (snapshot.content is None or explicit):
            stored = None
            automatic_path = ""
            if project_source:
                if project is None:
                    project = _project_plugin_sources(ctx, request, reference_store, project_stamp)
                if getattr(collection, "token", project.token) != project.token:
                    raise _project_changed()
                namespace = dict(snapshot.metadata).get("source_namespace") or getattr(slot, "source_namespace", None)
                stored = project.find(snapshot, namespace)
                if stored is None:
                    raise _project_changed()
                automatic_path = stored.reference_path
            snapshot = _bind_write_plugin(
                key, snapshot, entries, explicit, automatic_path, request, roots, base, encodings.input_encoding
            )
            if stored is not None and explicit and stored.reference_path != snapshot.source.uri:
                reference_updates[key] = (source_id, stored, snapshot.source.uri)
        source = Path(snapshot.source.display_name or snapshot.source.uri)
        if destination_mode == ORIGINAL and output_format is not FormatId.PLUGIN_SSE:
            raise RequestError("WRITE_DESTINATION_INVALID", "仅插件支持写回原插件。")
        if file_path and (output_format is FormatId.PLUGIN_SSE or target == "strings"):
            raise RequestError("WRITE_PLUGIN_PATH_INVALID", "插件另存请提供 output_dir，保留已定位插件的原文件名。")
        if destination_mode != ORIGINAL and not (directory or file_path):
            raise RequestError("OUTPUT", f"请为来源选择输出位置：{key}")
        if file_path:
            path = str(Path(os.path.abspath(base / file_path)))
        elif directory:
            path = str(Path(directory) / directory_output_filename(source, output_format))
        else:
            path = ""
        revision = collection.collection_revision.value
        if len(selected) > 1:
            revision = int(getattr(ctx, "variant_revision", revision) or 0)
        draft = native_write_draft(
            snapshot,
            entries,
            revision,
            request,
            output_format,
            path,
            encodings=encodings,
            overwrite=overwrite,
            pack_bsa=packed,
        )
        if len(selected) == 1 and output_format is FormatId.PLUGIN_SSE:
            draft = retarget_plugin(draft, destination_mode, directory)
        drafts.append(draft)
    batch = None
    if len(drafts) > 1:
        batch = retarget_write_batch(
            NativeWriteBatch(tuple(drafts)),
            directory,
            overwrite=overwrite,
            pack_bsa=packed,
            output_format="sst.ssu9" if target == "sst" else "auto",
            destination_mode=destination_mode,
        )
        drafts = batch.drafts
    for draft in drafts:
        if not draft.target_path:
            raise RequestError("OUTPUT", "当前没有已定位的原插件，请先选择来源文件。")
        authorize_file_outputs(
            ctx,
            write_artifact_paths(draft.source_snapshot, draft.target_path, draft.options, format_id=draft.format_id),
        )
    if packed and not any(
        item.format_id is FormatId.PLUGIN_SSE and _snapshot_localized_sources(item.source_snapshot) for item in drafts
    ):
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
    checked = preflight_write_batch(batch, service) if batch is not None else (service.preflight(drafts[0]),)
    if project is not None:
        if _project_stamp(ctx) != project_stamp or project.store.read_token(project.token.variant_id) != project.token:
            raise _project_changed()
        for (key, _slot), result in zip(selected, checked, strict=True):
            if not isinstance(result, WriteBatchFailure) and result.ready and key in reference_updates:
                if not project.remember(*reference_updates[key]):
                    raise _project_changed()
    captures = tuple(
        dict.fromkeys(
            path
            for item in checked
            if not isinstance(item, WriteBatchFailure) and item.ready
            for path, _fingerprint in item.artifact_fingerprints
        )
    )
    return PreparedWriteBack(tuple(key for key, _slot in selected), checked, captures)


def _project_stamp(ctx) -> tuple:
    return (
        getattr(ctx, "active_version_identity", None),
        getattr(ctx, "project_revision", None),
        getattr(ctx, "variant_revision", None),
    )


def _project_plugin_sources(ctx, request, references, stamp):
    identity, project_revision, variant_revision = stamp
    project_id, variant_id = identity
    expected = getattr(ctx, "_target_version_identity", None)
    if (
        (expected is not None and expected != identity)
        or getattr(request, "project_id", None) not in {None, project_id}
        or getattr(request, "variant_id", None) not in {None, variant_id}
    ):
        raise _project_changed()
    return ProjectPluginReferences(
        references, ProjectId(project_id), VariantId(variant_id), project_revision, variant_revision
    )


def _project_changed() -> RequestError:
    return RequestError("WRITE_SOURCE_CHANGED", "工程版本或来源已变化，请重新发起写回。")


def _bind_write_plugin(key, snapshot, entries, explicit, automatic_path, request, roots, base, input_encoding):
    path = explicit or automatic_path
    if not path:
        raise _plugin_input_required(key)
    try:
        grant = PathAuthorizationPolicy(PathGrant(Path(root)) for root in roots)
        absolute_input = Path(path)
        if not explicit and (
            not absolute_input.is_absolute()
            or not any(
                Path(os.path.abspath(absolute_input)).is_relative_to(Path(os.path.abspath(root))) for root in roots
            )
        ):
            raise RequestError("PATH_OUTSIDE_GRANT", "来源路径超出授权范围")
        require_input_access(grant, absolute_input, base)
        absolute_input = absolute_input if absolute_input.is_absolute() else base / absolute_input
        authorize_plugin_resources(
            grant, absolute_input.resolve(strict=True), dict(snapshot.metadata).get("localized_language", "english")
        )
        return bind_plugin_write_input(
            snapshot,
            entries,
            str(absolute_input),
            request,
            TranslationIoProjectSourcePreparer(),
            input_encoding=input_encoding,
        )
    except (DomainError, OSError, ValueError) as exc:
        if (
            explicit
            or isinstance(exc, DomainError)
            and exc.category in {ErrorCategory.CANCELLED, ErrorCategory.INTERNAL}
        ):
            raise
        raise _plugin_input_required(key) from exc


def _plugin_input_required(key) -> RequestError:
    return RequestError(
        "WRITE_PLUGIN_INPUT_REQUIRED",
        f"无法使用来源 {key} 的已存插件。请提供与工程内容匹配且在授权目录内的 input_plugin 或 input_plugins。",
    )


def _selected_sources(args, ctx) -> tuple:
    override = getattr(ctx, "_source_input_selection", None) or {}
    source_scope = override.get("source_scope")
    frozen = source_scope["resolved_source_keys"] if source_scope is not None else None
    available = getattr(ctx, "slots", {}) or {}
    if "source_keys" not in args and frozen is None:
        captured_key = getattr(ctx, "_target_source_key", None)
        if captured_key is not None:
            slot = available.get(captured_key)
            if slot is None or slot.collection is not getattr(ctx, "_target_collection", None):
                raise _project_changed()
        else:
            slot = getattr(ctx, "active_slot", None)
        if slot is None or not getattr(slot, "collection", None):
            raise RequestError("COLLECTION_NOT_LOADED", "当前没有加载翻译集合。")
        return ((captured_key or getattr(ctx, "active_key", "") or "active", slot),)
    keys = args.get("source_keys", frozen)
    if not isinstance(keys, list) or not keys or any(not isinstance(key, str) or not key.strip() for key in keys):
        raise RequestError(
            "WRITE_SOURCE_SELECTION_INVALID", "source_keys 必须是非空字符串列表，取自 list_collections。"
        )
    if len(keys) != len(set(keys)):
        raise RequestError("WRITE_SOURCE_SELECTION_DUPLICATE", "source_keys 不能重复。")
    if frozen is not None:
        if set(keys) != set(frozen):
            raise RequestError("REQUEST_SCOPE_MISMATCH", "写回来源必须与已确认的补充输入范围一致，不能恢复已跳过来源。")
        keys = frozen
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
