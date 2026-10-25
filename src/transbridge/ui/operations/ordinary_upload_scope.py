"""Resolve a fixed ordinary-upload root file from registered source identity."""

from pathlib import PureWindowsPath

from transbridge.application.categorized_upload import UploadSource
from transbridge.application.categorized_upload.classification import source_directory_names
from transbridge.application.projects.source_content import authoritative_baseline_sources, source_content_identity
from transbridge.paratranz.categorized_upload import CategorizedUploadGateway
from transbridge.paratranz.ordinary_upload import OrdinaryUploadAdapter


def ordinary_upload_path(context, entries=None) -> str:
    entries = tuple(context.collection if entries is None else entries)
    namespaces = {_entry_namespace(entry) for entry in entries}
    if len(namespaces) != 1:
        raise ValueError("普通上传必须选择一个明确的插件来源。")
    namespace = next(iter(namespaces))
    registrations = tuple(getattr(context, "project_sources", ()))
    registered = tuple(
        source
        for source in authoritative_baseline_sources(
            registrations,
            tuple(getattr(context, "project_relations", ())),
        )
        if source.get("enabled", True)
    )
    slots = tuple(getattr(context, "slots", {}).values())
    sources, owners = [], {}
    for source in registered:
        location = str(source.get("location") or source.get("path") or "")
        identity = source_content_identity(source)
        if identity is None:
            matching = [slot for slot in slots if location in _locations(slot)]
            if len(matching) == 1:
                identity = _namespace(matching[0].collection)
        if identity is None and len(registered) == 1:
            identity = namespace
        label = str(source.get("display_name") or PureWindowsPath(location).name)
        source_id = str(source.get("source_id") or identity or location)
        if not source_id or not label:
            raise ValueError("工程来源缺少稳定插件名称，无法确定普通上传文件。")
        sources.append(UploadSource(source_id, label, ()))
        if identity:
            if identity in owners:
                raise ValueError("工程来源不唯一，无法确定普通上传文件。")
            owners[identity] = source_id
    if not registrations:
        for slot in slots:
            identity = _namespace(slot.collection)
            label = _slot_label(slot)
            if identity and label:
                sources.append(UploadSource(identity, label, ()))
                owners[identity] = identity
        if not slots:
            location = str(getattr(context, "esp_path", None) or getattr(context, "eet_path", None) or "")
            if location:
                sources.append(UploadSource(namespace, PureWindowsPath(location).name, ()))
                owners[namespace] = namespace
    if namespace not in owners:
        raise ValueError("无法定位当前插件的固定上传路径，请重新打开工程来源。")
    return source_directory_names(tuple(sources))[owners[namespace]] + ".json"


def ordinary_upload_adapter(service, request):
    gateway = CategorizedUploadGateway(request.ui_context.config)
    try:
        return OrdinaryUploadAdapter(service, gateway, request.upload_path)
    except Exception:
        gateway.close()
        raise


def _namespace(collection):
    values = {_entry_namespace(entry) for entry in collection or ()}
    return next(iter(values)) if len(values) == 1 else None


def _entry_namespace(entry):
    identity = getattr(entry, "identity", None) or entry.entry_key
    return identity.namespace.value


def _locations(slot):
    return tuple(str(getattr(slot, name, None) or "") for name in ("esp_path", "eet_path", "xt_path", "sst_path"))


def _slot_label(slot):
    return next((PureWindowsPath(value).name for value in _locations(slot) if value), str(slot.label or ""))
