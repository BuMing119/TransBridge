"""Resolve downloads from current source paths without saved file/string IDs."""

from pathlib import PureWindowsPath

from transbridge.application.categorized_upload import UploadSource
from transbridge.application.categorized_upload.classification import source_directory_names
from transbridge.application.io.paratranz_context_order import PARATRANZ_CONTEXT_ORDER_METADATA
from transbridge.application.projects.source_content import authoritative_baseline_sources, source_content_identity
from transbridge.application.sync import SyncOperation
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.paratranz.download_paths import legacy_plugin_paths, unambiguous_legacy_paths
from transbridge.paratranz.path_snapshot import CurrentPathSnapshotAdapter, current_category_paths

from .ordinary_upload_scope import ordinary_upload_path


def download_snapshot_adapter(service, request):
    if request.operation is not SyncOperation.DOWNLOAD:
        raise ValueError("当前文件下载范围仅适用于下载操作。")
    entries = _complete_source_entries(request)
    context = request.ui_context
    root_path = getattr(request, "upload_path", "") or ordinary_upload_path(context, entries=entries)
    categories = current_category_paths(root_path, entries)
    source_paths = _registered_source_paths(context, root_path, categories, request.namespace)
    if source_paths is None:
        # An explicit headless scope has no other registered source whose old
        # name could be claimed; let the adapter infer its local legacy paths.
        return CurrentPathSnapshotAdapter(context.config, root_path, category_paths=categories)
    source_id, paths = source_paths
    allowed = unambiguous_legacy_paths(paths)[source_id]
    blocked = tuple(path for path in legacy_plugin_paths(paths[source_id]) if path not in allowed)
    return CurrentPathSnapshotAdapter(
        context.config,
        root_path,
        category_paths=categories,
        legacy_paths=allowed,
        blocked_legacy_paths=blocked,
    )


def _registered_source_paths(context, root_path, category_paths, namespace):
    registrations = tuple(getattr(context, "project_sources", ()))
    if not registrations:
        return None
    registered = tuple(
        source
        for source in authoritative_baseline_sources(registrations, tuple(getattr(context, "project_relations", ())))
        if source.get("enabled", True)
    )
    sources = tuple(
        UploadSource(
            str(
                source.get("source_id")
                or source_content_identity(source)
                or source.get("location")
                or source.get("path")
                or ""
            ),
            str(
                source.get("display_name")
                or PureWindowsPath(str(source.get("location") or source.get("path") or "")).name
            ),
            (),
        )
        for source in registered
    )
    names = source_directory_names(sources)
    paths = {identity: (name + ".json",) for identity, name in names.items()}
    identities = {
        source.source_id: source_content_identity(raw) for source, raw in zip(sources, registered, strict=True)
    }
    namespace_value = getattr(namespace, "value", namespace)
    owners = [source_id for source_id, identity in identities.items() if identity == namespace_value]
    if len(owners) != 1:
        # Older registrations can lack namespace metadata. Only their canonical
        # root may identify them; a known different namespace is never guessed.
        owners = [
            source_id
            for source_id, name in names.items()
            if identities[source_id] is None and (name + ".json").casefold() == root_path.casefold()
        ]
    if len(owners) == 1:
        source_id = owners[0]
    else:
        # Explicit full paths were supported by headless/legacy plan callers.
        # Keep that scope without assigning it to an unrelated registration;
        # all registered roots still reserve their current and legacy names.
        source_id = "explicit-download-scope"
        while source_id in paths:
            source_id += "_"
    paths[source_id] = (root_path, *category_paths)
    return source_id, paths


def _complete_source_entries(request):
    """Prefer the source collection so UI selection cannot change task titles."""
    collection = getattr(request.ui_context, "collection", None)
    if collection is not None:
        entries = tuple(entry for entry in collection if entry.identity.namespace == request.namespace)
        if not entries:
            raise ValueError("当前集合不包含请求的插件来源，无法确定下载文件。")
        return entries
    # Headless callers already carry the complete source in local snapshots.
    # Rebuild the transmission view without transporting historical remote IDs.
    entries = tuple(
        TranslationEntry(
            id=item.entry_key.local_key,
            key=item.entry_key.local_key,
            original=item.original,
            translation=item.translation,
            stage=item.stage,
            context=item.context,
            entry_key=item.entry_key,
            revision=item.revision,
            metadata=() if item.context_order is None else ((PARATRANZ_CONTEXT_ORDER_METADATA, item.context_order),),
        )
        for item in request.local_entries
        if item.entry_key.namespace == request.namespace and not item.deleted
    )
    if not entries:
        raise ValueError("下载请求缺少当前插件的完整词条，无法确定下载文件。")
    return entries
