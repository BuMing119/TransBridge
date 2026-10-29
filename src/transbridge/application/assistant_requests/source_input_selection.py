"""Authorize explicit entry or template-source selections without rewriting accepted ingress."""

from dataclasses import replace
import json
from uuid import uuid4

from transbridge.application.io.identity import EntryKey

from .journal import EventCause
from .models import ItemKind, ItemStatus, RequestError, digest
from .reducer import RequestEvent, reduce_request, require_open
from .source_input_waiting import SOURCE_INPUT_REASON, needs_source_input


def _entry_id(key):
    return key.serialize() if key.original is not None else key.local_key


def _repair_keys(request, context, item_ids):
    keys, sources, template_keys, template_sources = set(), set(), set(), set()
    has_template = False
    for effect in request.effects:
        execution = effect.execution
        if (
            effect.status != "failed"
            or execution.request_revision != request.revision
            or effect.lease_epoch != request.lease_epoch
            or not set(execution.item_ids).intersection(item_ids)
            or not needs_source_input(effect.receipt)
        ):
            continue
        repair = json.loads(effect.receipt)["result"]["data"]["source_input_repair"]
        if any(
            repair.get(field, expected) != expected
            for field, expected in (
                ("project_id", context.project_id),
                ("variant_id", context.variant_id),
                ("session_id", context.session_id),
                ("request_id", request.request_id),
                ("request_revision", request.revision),
                ("request_lease_epoch", request.lease_epoch),
            )
        ):
            continue
        receipt_keys = {EntryKey.from_dict(value) for value in repair.get("entry_keys", ())}
        keys.update(receipt_keys)
        if repair["requirement"] == "template":
            template_keys.update(receipt_keys)
            template_sources.update(repair["source_keys"])
            has_template = True
        sources.update(repair["source_keys"])
    return keys, sources, template_keys, template_sources, has_template


def _source_scope_selection(source_scope, template_sources, has_template):
    if source_scope is None:
        return None
    if not isinstance(source_scope, dict) or not has_template:
        raise RequestError("REQUEST_SCOPE_MISMATCH", "来源范围只适用于本次文件模板修复")
    fields = ("requested_source_keys", "selected_source_keys", "resolved_source_keys")
    for field in fields:
        values = source_scope.get(field)
        if (
            not isinstance(values, list)
            or not values
            or any(not isinstance(key, str) or not key.strip() for key in values)
            or len(values) != len(set(values))
        ):
            raise RequestError("REQUEST_SCOPE_MISMATCH", "来源修复范围必须是非空且不重复的来源列表")
    requested, selected, resolved = (source_scope[field] for field in fields)
    if set(requested) != template_sources or not set(selected) <= set(requested) or len(selected) != len(resolved):
        raise RequestError("REQUEST_SCOPE_MISMATCH", "来源修复范围不符合原文件模板请求或所选来源映射")
    return {field: list(source_scope[field]) for field in fields}


def apply_source_input_selection(
    service, context, request_id, expected_revision, expected_lease_epoch, item_ids, mappings, *, source_scope=None
):
    """Persist human-confirmed mappings or source scope for the waiting repair item.

    Revoking the old request lease requires a fresh proposal and confirmation;
    this operation neither unblocks an item nor authorizes tool execution.
    """
    return _selection_change(
        service,
        context,
        request_id,
        expected_revision,
        expected_lease_epoch,
        item_ids,
        mappings,
        source_scope=source_scope,
        publish=lambda original, updated: service.update_request(
            context,
            request_id,
            lambda current: _unchanged(current, original, updated),
            cause=EventCause("request.source_input_selection", "user", {"request_ids": [request_id]}),
        ),
    )


def _unchanged(current, original, updated):
    if current != original:
        raise RequestError("REQUEST_REVISION_CONFLICT", "请求在来源修复准备后已变化")
    return updated


def _selection_change(
    service, context, request_id, expected_revision, expected_lease_epoch, item_ids, mappings, *, source_scope, publish
):
    item_ids = tuple(item_ids)
    with service.serialized(context):
        state = service.state(context)

        def update(request):
            require_open(request, expected_revision)
            if request.lease_epoch != expected_lease_epoch:
                raise RequestError("TURN_LEASE_STALE", "来源修复对应的执行许可已变化")
            if request.pause_reasons or request.unsettled:
                raise RequestError("REQUEST_NOT_READY", "请先处理当前任务的暂停或未完成操作")
            waiting = {
                item.item_id
                for item in request.items
                if item.kind == ItemKind.EXECUTION
                and item.status == ItemStatus.WAITING
                and SOURCE_INPUT_REASON in item.waiting_reasons
            }
            if not item_ids or not set(item_ids) <= waiting:
                raise RequestError("REQUEST_NOT_READY", "所选事项不再等待来源输入")
            if any(str(getattr(context, field)) != value for field, value in request.scope):
                raise RequestError("REQUEST_SCOPE_MISMATCH", "来源修复不属于原工程、版本或会话")
            source = next(
                (item for item in state.get("ingress", ()) if item["message_id"] in request.source_message_ids), None
            )
            if source is None:
                raise RequestError("REQUEST_SCOPE_MISMATCH", "原输入的选择记录不可用")
            selection = source.get("selection", {})
            keys, sources, template_keys, template_sources, has_template = _repair_keys(request, context, item_ids)
            frozen_scope = _source_scope_selection(source_scope, template_sources, has_template)
            relevant = {key: mappings[key] for key in keys if key in mappings}
            for key in template_keys:
                relevant.setdefault(key, key)
            if not relevant and not has_template:
                raise RequestError("REQUEST_SCOPE_MISMATCH", "没有与本次来源修复对应的人工映射")
            original_ids = (
                ()
                if frozen_scope is not None
                else selection.get("selected_entry_ids") or [_entry_id(key) for key in sorted(keys)]
            )
            aliases = {}
            for key in keys:
                for alias in {_entry_id(key), key.serialize()}:
                    aliases.setdefault(alias, []).append(key)
            selected = []
            selected_keys = []
            for identity in original_ids:
                matches = aliases.get(identity, ())
                if len(matches) > 1:
                    raise RequestError("REQUEST_SCOPE_MISMATCH", "原选择不能唯一对应到来源条目")
                if not matches:
                    selected.append(identity)
                elif (mapped := relevant.get(matches[0])) is not None:
                    selected.append(_entry_id(mapped))
                    selected_keys.append(mapped.to_dict())
            if not selected and not has_template:
                raise RequestError("REQUEST_SCOPE_MISMATCH", "原选择没有已明确对应的可执行条目")
            version = json.loads(request.execution_version_json) if request.execution_version_json else {}
            version.setdefault("active_version_identity", [context.project_id, context.variant_id])
            epoch = request.lease_epoch + 1
            version["source_input_selection"] = {
                "request_id": request_id,
                "request_revision": request.revision,
                "lease_epoch": epoch,
                "item_ids": list(item_ids),
                "source_message_id": source["message_id"],
                "selection_digest": digest(selection),
                "source_keys": sorted(sources),
                "selected_entry_ids": list(dict.fromkeys(selected)),
                "selected_entry_keys": selected_keys,
                "mappings": [
                    {"old": key.to_dict(), "new": None if value is None else value.to_dict()}
                    for key, value in sorted(relevant.items())
                ],
            }
            if frozen_scope is not None:
                version["source_input_selection"]["source_scope"] = frozen_scope
            return replace(request, lease_epoch=epoch, execution_version_json=json.dumps(version, ensure_ascii=False))

        original = next((item for item in service.requests(state) if item.request_id == request_id), None)
        if original is None:
            raise RequestError("REQUEST_TARGET_AMBIGUOUS", "原请求已不存在")
        return publish(original, update(original))


def prepare_source_input_repair(
    service,
    context,
    request_id,
    expected_revision,
    expected_lease_epoch,
    item_ids,
    mappings,
    *,
    source_scope=None,
    view_id,
    allowed_tools,
):
    """Atomically save remapping, unblock and accept a fresh item-scoped turn.

    A busy scheduler or a failed save leaves the original waiting request intact.
    The acquired lease admits a new proposal, never the previous confirmation.
    """
    item_ids = tuple(item_ids)

    def publish(original, updated):
        updated = reduce_request(
            updated,
            RequestEvent(
                uuid4().hex, "unblock", updated.revision, {"item_ids": item_ids, "reason": SOURCE_INPUT_REASON}
            ),
        )
        return _admit(service, context, original, updated, item_ids, view_id, allowed_tools)

    return _selection_change(
        service,
        context,
        request_id,
        expected_revision,
        expected_lease_epoch,
        item_ids,
        mappings,
        source_scope=source_scope,
        publish=publish,
    )


def _admit(service, context, original, updated, item_ids, view_id, allowed_tools):
    candidate = replace(
        updated,
        items=tuple(
            item
            if item.item_id in item_ids
            else replace(item, waiting_reasons=(*item.waiting_reasons, "source_input_repair_scope"))
            for item in updated.items
        ),
    )
    selected = service.scheduler.select_next_turn(
        context.session_id, view_id, (candidate,), automatic=False, allowed_tools=tuple(allowed_tools)
    )
    if selected is None:
        raise RequestError("SOURCE_INPUT_REPAIR_BUSY", "执行权限仍被占用，请稍后重试补充输入")
    try:
        with service.scheduler.serialized(selected.admission):
            service.update_request(
                context,
                original.request_id,
                lambda current: _unchanged(current, original, updated),
                cause=EventCause(
                    "turn.selected",
                    "user",
                    {
                        "request_ids": [original.request_id],
                        "item_ids": list(item_ids),
                        "turn_id": selected.admission.turn_id,
                    },
                    details={"source_input_repair": True},
                ),
            )
    except Exception:
        service.scheduler.release(selected.admission)
        raise
    return replace(selected, request=updated)


def readmit_source_input_confirmation(
    service, context, request_id, expected_revision, expected_lease_epoch, item_ids, *, view_id, allowed_tools
):
    """Retry presentation of the same saved mapping; never accept a new scope."""
    with service.serialized(context):
        state = service.state(context)
        request = next((item for item in service.requests(state) if item.request_id == request_id), None)
        if request is None:
            raise RequestError("REQUEST_TARGET_AMBIGUOUS", "原请求已不存在")
        require_open(request, expected_revision)
        baseline = json.loads(request.execution_version_json) if request.execution_version_json else {}
        override = baseline.get("source_input_selection", {})
        item_ids = tuple(item_ids)
        if (
            request.lease_epoch != expected_lease_epoch
            or request.pause_reasons
            or request.unsettled
            or override.get("request_id") != request_id
            or override.get("request_revision") != request.revision
            or not item_ids
            or set(item_ids) != set(override.get("item_ids", ()))
            or any(str(getattr(context, field)) != value for field, value in request.scope)
        ):
            raise RequestError("REQUEST_SCOPE_MISMATCH", "已确认的来源映射或执行范围已变化")
        source = next(
            (item for item in state.get("ingress", ()) if item["message_id"] == override["source_message_id"]), None
        )
        if source is None or digest(source.get("selection", {})) != override["selection_digest"]:
            raise RequestError("REQUEST_SCOPE_MISMATCH", "原选择记录已变化")
        for item in request.items:
            if item.item_id in item_ids and (
                item.status not in {ItemStatus.PENDING, ItemStatus.WAITING}
                or set(item.waiting_reasons) - {SOURCE_INPUT_REASON, "approval", "approval_revalidation"}
            ):
                raise RequestError("REQUEST_NOT_READY", "原事项已开始执行或仍有其他等待原因")
        epoch = request.lease_epoch + 1
        updated = replace(
            request,
            lease_epoch=epoch,
            execution_version_json=json.dumps(baseline, ensure_ascii=False),
            items=tuple(
                replace(item, status=ItemStatus.PENDING, waiting_reasons=()) if item.item_id in item_ids else item
                for item in request.items
            ),
        )
        return _admit(service, context, request, updated, item_ids, view_id, allowed_tools)


def repaired_selection(request, source, context, baseline):
    """Read the override only under its new, item-specific execution lease."""
    override = baseline.get("source_input_selection")
    if override is None:
        return None
    gate = getattr(context, "assistant_gate", None)
    admission = getattr(gate, "admission", None)
    execution_items = {
        item.item_id
        for item in request.items
        if item.kind == ItemKind.EXECUTION and admission is not None and item.item_id in admission.ready_item_ids
    }
    if not execution_items or not execution_items <= set(override["item_ids"]):
        return None
    if (
        override["request_id"] != request.request_id
        or override["request_revision"] != request.revision
        or admission.request_lease_epoch != request.lease_epoch
        or source is None
        or override["source_message_id"] != source["message_id"]
        or override["selection_digest"] != digest(source.get("selection", {}))
    ):
        raise RequestError("REQUEST_SCOPE_MISMATCH", "来源修复选择已过期，请重新准备并确认")
    return {**source.get("selection", {}), "selected_entry_ids": override["selected_entry_ids"]}
