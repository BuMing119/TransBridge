# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Limit authoritative workbench rows to the captured assistant input."""

from transbridge.application.workbench_filters import filtered_rows
from transbridge.application.workbench_statistics import scope_statistics
from transbridge.converter.translation_entry_collection import TranslationEntryCollection


def workbench_source_identity(app_context):
    """Persist the registered slot and namespace without materializing paged project entries."""
    slot = getattr(app_context, "active_slot", None)
    if slot is None:
        return None
    source_id = getattr(slot, "source_id", None)
    namespace = getattr(slot, "source_namespace", None)
    if not isinstance(namespace, str) or not namespace:
        collection = getattr(slot, "collection", None)
        info = getattr(getattr(collection, "source", None), "info", None)
        namespace = getattr(getattr(info, "namespace", None), "value", None)
        if not namespace and isinstance(collection, TranslationEntryCollection):
            entry = next(iter(collection), None)
            namespace = entry.identity.namespace.value if entry is not None else None
    return {
        "source_id": source_id if isinstance(source_id, str) else None,
        "namespace": namespace if isinstance(namespace, str) else None,
    }


def label_source_identity(slot_key, collection):
    source = getattr(collection, "source", None)
    if source is not None:
        return slot_key, frozenset((source.info.namespace,))
    return slot_key, frozenset(entry.identity.namespace for entry in collection or ())


def check_workbench_source(ctx, app):
    """Check the captured source registration before reading live workbench metadata."""
    if not hasattr(ctx, "_target_collection"):
        return
    if ctx._target_version_identity != getattr(app, "active_version_identity", None):
        raise ValueError("活动 Project/Variant 已变化，请重新核对筛选范围。")
    if ctx._target_source_key != getattr(app, "active_key", None):
        raise ValueError("活动来源已变化，请重新核对筛选范围。")
    if hasattr(ctx, "_target_workbench_source") and ctx._target_workbench_source != workbench_source_identity(app):
        raise ValueError("工作台的来源登记或命名空间已变化，请重新核对。")


def current_workbench_source(ctx, collection):
    """Resolve the full current source, retaining the request's project, version and namespace."""
    full = getattr(ctx, "_source_input_full_collection", None)
    source = collection if full is None else full
    app = getattr(ctx, "app_context", None)
    if app is not None and hasattr(ctx, "_target_collection"):
        source = ctx._target_collection if full is None else full
        check_workbench_source(ctx, app)
        source_key = getattr(app, "active_key", None)
        current = getattr(app, "collection", None)
        if current is not source:
            if label_source_identity(source_key, source) != label_source_identity(source_key, current):
                raise ValueError("活动来源已变化，请重新核对筛选范围。")
            source = current
    return source


def filtered_tool_rows(ctx, collection, filter_state, entry_labels=None, *, observe=None):
    token = getattr(ctx, "cancellation_token", None)
    checkpoint = None if token is None else token.raise_if_cancelled
    if checkpoint is not None:
        checkpoint()
    source = current_workbench_source(ctx, collection)
    full = getattr(ctx, "_source_input_full_collection", None)
    if (
        source is collection
        or full is collection
        or (full is None and getattr(ctx, "_target_collection", None) is collection)
    ):
        yield from filtered_rows(source, filter_state, entry_labels, checkpoint=checkpoint, observe=observe)
        return
    identities = {entry.identity for entry in collection}
    for row in filtered_rows(source, filter_state, entry_labels, checkpoint=checkpoint, observe=observe):
        if row[0].identity in identities:
            yield row


def tool_scope_statistics(ctx):
    """Use the desktop aggregate for the admitted source, respecting a repaired, narrower input."""
    collection = ctx.collection
    source = current_workbench_source(ctx, collection)
    revision = getattr(source, "collection_revision", None)
    keys = None
    if getattr(ctx, "_source_input_full_collection", None) is not None and collection is not source:
        keys = frozenset(entry.identity for entry in collection)
    read_statistics = getattr(ctx, "workbench_statistics", None)
    if callable(read_statistics):
        statistics = read_statistics(source, keys=keys)
    else:
        statistics = scope_statistics(source, getattr(ctx, "entry_labels", None), keys=keys)
    if current_workbench_source(ctx, collection) is not source or revision != getattr(
        source, "collection_revision", None
    ):
        raise ValueError("工作台来源或词条在读取期间发生变化，请重新查询。")
    return statistics
