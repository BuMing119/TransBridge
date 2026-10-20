"""Execute a reviewed remote target without sending source fields during upload."""

from collections import defaultdict

from transbridge.application.io.identity import ExternalEntryRef
from transbridge.application.io.paratranz_context_order import format_ordered_context
from transbridge.application.ports.paratranz import ParaTranzEntry

from .models import EntrySummary, SyncAction, SyncOperation, canonical_hash


class RemoteTargetIndex:
    """Resolve exact target snapshots by remote identity, independent of bare key."""

    def __init__(self, entries):
        self._by_ref = defaultdict(list)
        self._by_key = defaultdict(list)
        for entry in entries:
            self._by_key[(entry.entry_key.namespace, entry.entry_key.local_key)].append(entry)
            if entry.external_ref is not None:
                self._by_ref[entry.external_ref.index_key].append(entry)

    def get_for_item(self, item):
        if item.before is None:
            if self._by_key.get((item.entry_key.namespace, item.entry_key.local_key)):
                raise RuntimeError("a new remote entry occupies the planned creation key")
            return None
        reference = item.external_ref
        matches = () if reference is None else self._by_ref.get(reference.index_key, ())
        if len(matches) != 1 or EntrySummary.from_remote(matches[0]) != item.before:
            raise RuntimeError("the reviewed remote target changed or is ambiguous")
        return matches[0]


def execute_remote(service, request, item, local_map, targets):
    reference = item.external_ref
    if item.action is SyncAction.DELETE_REMOTE:
        _remote_id(reference)
        targets.get_for_item(item)
        service.delete_entry(request.project_id, reference.opaque_id, cancellation=request.cancellation)
        return reference, "deleted"
    local = local_map.get(item.entry_key)
    if local is None:
        raise ValueError("local source entry is missing")
    remote = targets.get_for_item(item)
    if item.action is SyncAction.UPDATE_REMOTE:
        remote_id = _remote_id(reference)
        if remote is None:
            raise ValueError("remote target entry is missing")
        if request.authorized_plan.plan.operation is SyncOperation.UPLOAD:
            result = service.update_entry_translation(
                request.project_id,
                ParaTranzEntry(
                    remote_id,
                    remote.entry_key.local_key,
                    remote.original,
                    local.translation,
                    _context(remote),
                    local.stage,
                ),
                cancellation=request.cancellation,
            )
        elif item.reason == "context_order_changed":
            if local.context_order is None:
                raise ValueError("context order refresh requires a local order")
            result = service.update_entry_context(
                request.project_id,
                ParaTranzEntry(
                    remote_id,
                    remote.entry_key.local_key,
                    remote.original,
                    remote.translation,
                    format_ordered_context(remote.context, local.context_order),
                    remote.stage,
                ),
                cancellation=request.cancellation,
            )
        else:
            result = service.upsert_entry(
                request.project_id,
                ParaTranzEntry(
                    remote_id,
                    local.entry_key.local_key,
                    local.original,
                    local.translation,
                    _context(local, remote.context_order),
                    local.stage,
                ),
                force_overwrite=True,
                cancellation=request.cancellation,
            )
        if result.remote_id != remote_id:
            raise ValueError("remote update response identity does not match the confirmed target")
    else:
        result = service.upsert_entry(
            request.project_id,
            ParaTranzEntry(
                None,
                local.entry_key.local_key,
                local.original,
                local.translation,
                _context(local),
                local.stage,
            ),
            force_overwrite=False,
            cancellation=request.cancellation,
        )
        if isinstance(result.remote_id, bool) or not isinstance(result.remote_id, int) or result.remote_id < 1:
            raise ValueError("remote create response is missing a valid identity")
    return ExternalEntryRef("paratranz", f"project:{request.project_id}", result.remote_id), _remote_revision(result)


def _remote_id(reference):
    value = None if reference is None else reference.opaque_id
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("ParaTranz update requires a positive integer remote id")
    return value


def _context(entry, fallback_order=None):
    order = entry.context_order if entry.context_order is not None else fallback_order
    return entry.context if order is None else format_ordered_context(entry.context, order)


def _remote_revision(entry: ParaTranzEntry) -> str:
    return canonical_hash({
        "id": entry.remote_id,
        "key": entry.key,
        "original": entry.original,
        "translation": entry.translation,
        "context": entry.context,
        "stage": entry.stage,
    })
