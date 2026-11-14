# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Freeze explicitly selected assistant sources and authorize their exact outputs."""

from dataclasses import dataclass
import os
from pathlib import Path

from transbridge.application.assistant_requests.models import RequestError
from transbridge.application.contracts import DomainError, ErrorCategory, RequestContext
from transbridge.application.io import FormatId
from transbridge.application.io.identity import SourceNamespace
from transbridge.application.io.operation_write import HydratedWriteDraft, HydratedWritePreflightService
from transbridge.application.io.operation_write_batch import isolate_batch_output_conflicts
from transbridge.application.io.plugin_localized_sources import _snapshot_localized_sources
from transbridge.application.io.publish import BackupPolicy, ConflictPolicy
from transbridge.application.io.write_formats import SST_FORMATS, source_output_compatible, write_artifact_paths
from transbridge.application.projects.write_inputs import bind_plugin_write_input
from transbridge.application.security.paths import PathAuthorizationPolicy, PathGrant
from transbridge.application.terminology_profiles import TerminologyProfileWriteProjectionSource
from transbridge.parser.localized_resources import _case_child, candidate_input_paths
from transbridge.persistence.project_provisioning import TranslationIoProjectSourcePreparer
from transbridge.persistence.v2.ids import ProjectId, VariantId

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
        return tuple(item.draft.target_path for item in self.preflights)

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
    encodings = effective_text_encodings(getattr(ctx, "app_context", None) or ctx, request)
    inputs = args.get("input_plugins", {})
    if not isinstance(inputs, dict) or any(
        not isinstance(value, str) or not value.strip() for value in inputs.values()
    ):
        raise RequestError("WRITE_PLUGIN_INPUT_INVALID", "input_plugins 必须把来源键映射到本次插件文件。")
    project = None
    pending = None
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
        template_reader = getattr(collection, "native_template", None)
        if template_reader is not None:
            snapshot = template_reader()
        source_id = getattr(slot, "source_id", None)
        project_source = reference_store is not None and source_id and project_stamp[0] is not None
        if snapshot.format_id is FormatId.PLUGIN_SSE and snapshot.content is None and output_format not in SST_FORMATS:
            explicit = inputs.get(key) or (_path_argument(args, "input_plugin") if len(selected) == 1 else "")
            stored = None
            pending_source = False
            automatic_path = ""
            if project_source:
                if project is None:
                    project = _project_plugin_sources(ctx, request, reference_store, project_stamp[0])
                _store, token, sources = project
                namespace = SourceNamespace(dict(snapshot.metadata)["source_namespace"])
                stored = sources.get(namespace)
                if (
                    getattr(collection, "token", token) != token
                    or token.project_revision > project_stamp[1]
                    or token.variant_revision > project_stamp[2]
                ):
                    raise _project_changed()
                if stored is None:
                    if pending is None:
                        pending = reference_store.capture_pending_sources(
                            token.project_id,
                            token.variant_id,
                            project_revision=project_stamp[1],
                            variant_revision=project_stamp[2],
                        )
                        if pending is None:
                            raise _project_changed()
                    stored = next((item for item in pending.sources if item.namespace == namespace), None)
                    pending_source = True
                if (
                    stored is None
                    or stored.format_id is not FormatId.PLUGIN_SSE
                    or stored.fingerprint != snapshot.sha256
                ):
                    raise _project_changed()
                automatic_path = stored.reference_path
            snapshot = _bind_write_plugin(
                key, snapshot, entries, explicit, automatic_path, request, roots, base, encodings.input_encoding
            )
            if stored is not None and explicit and stored.reference_path != snapshot.source.uri:
                reference_updates[key] = (source_id, stored, snapshot.source.uri, pending_source)
        source = Path(snapshot.source.display_name or snapshot.source.uri)
        reference_field = {
            FormatId.PLUGIN_SSE: "esp_path",
            FormatId.XML_EET: "eet_path",
            FormatId.BINARY_EET: "eet_path",
            FormatId.XML_XT: "xt_path",
            FormatId.SST_SSU8: "sst_path",
            FormatId.SST_SSU9: "sst_path",
        }.get(snapshot.format_id)
        reference = (
            None
            if reference_field is None or project_source and snapshot.format_id is FormatId.PLUGIN_SSE
            else getattr(slot, reference_field, None)
        )
        converting = snapshot.format_id is not output_format
        if converting and not directory and not file_path:
            raise RequestError("OUTPUT", "从插件生成 SST 必须提供新的 path 或 output_dir。")
        if not directory and not file_path and not reference:
            raise RequestError("OUTPUT", f"来源没有参考输出位置，请提供 path 或 output_dir：{key}")
        destination = (
            Path(directory)
            / (
                source.with_suffix(".sst").name
                if converting
                else source.with_suffix(".eet").name
                if output_format is FormatId.BINARY_EET
                else source.name
            )
            if directory and (target == "strings" or not file_path)
            else Path(file_path or reference)
        )
        # Keep lexical components for the authorization policy to reject links.
        path = str(Path(os.path.abspath(destination if destination.is_absolute() else base / destination)))
        options = (
            ("pack_bsa", packed and output_format is FormatId.PLUGIN_SSE),
            *encodings.read_options().items(),
            ("output_encoding", encodings.output_encoding),
            ("target_language", encodings.target_language),
        )
        authorize_file_outputs(ctx, write_artifact_paths(snapshot, path, options, format_id=output_format))
        drafts.append(
            HydratedWriteDraft(
                snapshot,
                output_format,
                entries,
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
    frozen = service.freeze_projection(drafts[0])
    checked = isolate_batch_output_conflicts(
        tuple(service.preflight(item, frozen_projection=frozen) for item in drafts)
    )
    if project is not None:
        store, token, _sources = project
        if _project_stamp(ctx) != project_stamp or store.read_token(token.variant_id) != token:
            raise _project_changed()
        for (key, _slot), result in zip(selected, checked, strict=True):
            if result.ready and key in reference_updates:
                source_id, stored, new_path, pending_source = reference_updates[key]
                values = {
                    "expected_fingerprint": stored.fingerprint,
                    "expected_path": stored.reference_path,
                    "reference_path": new_path,
                }
                updated = (
                    reference_store.update_pending_source_reference(pending, source_id, stored.namespace, **values)
                    if pending_source
                    else reference_store.update_source_reference(
                        token.project_id, source_id, stored.namespace, token=token, **values
                    )
                )
                if not updated:
                    raise _project_changed()
    captures = tuple(
        dict.fromkeys(path for item in checked if item.ready for path, _fingerprint in item.artifact_fingerprints)
    )
    return PreparedWriteBack(tuple(key for key, _slot in selected), checked, captures)


def _project_stamp(ctx) -> tuple:
    return (
        getattr(ctx, "active_version_identity", None),
        getattr(ctx, "project_revision", None),
        getattr(ctx, "variant_revision", None),
    )


def _project_plugin_sources(ctx, request, references, identity):
    project_id, variant_id = identity
    expected = getattr(ctx, "_target_version_identity", None)
    if (
        (expected is not None and expected != identity)
        or getattr(request, "project_id", None) not in {None, project_id}
        or getattr(request, "variant_id", None) not in {None, variant_id}
    ):
        raise _project_changed()
    store = references.store_for(ProjectId(project_id))
    token = store.read_token(VariantId(variant_id))
    return store, token, {info.namespace: info for info in store.sources(native_fields=False)}


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
        _require_input_access(grant, absolute_input, base)
        absolute_input = absolute_input if absolute_input.is_absolute() else base / absolute_input
        if not explicit:
            _authorize_saved_plugin_resources(grant, absolute_input.resolve(strict=True), snapshot)
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


def _require_input_access(grant, path, base=None):
    access = grant.authorize(path, working_directory=base)
    if not access.allowed:
        raise RequestError(access.code, access.reason)


def _authorize_saved_plugin_resources(grant, path, snapshot):
    _require_input_access(grant, path.parent)
    strings = _case_child(path.parent, "Strings")
    if strings.exists():
        _require_input_access(grant, strings)
    language = dict(snapshot.metadata).get("localized_language", "english")
    for companion in candidate_input_paths(path, language):
        if companion.exists():
            _require_input_access(grant, companion)


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
