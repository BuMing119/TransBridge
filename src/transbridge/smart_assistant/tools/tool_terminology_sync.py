# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Smart Assistant projection of the shared terminology sync application facade."""

from __future__ import annotations

from dataclasses import asdict, fields, replace
from datetime import datetime
from typing import Any

from transbridge.application.contracts import JobRef, RequestContext
from transbridge.application.security.hitl import ConfirmationToken
from transbridge.application.tasks import OwnerRef
from transbridge.application.terminology.ports import PageRequest, SnapshotCursor
from transbridge.application.terminology_sync.draft_import_models import DraftImportChoice
from transbridge.application.terminology_sync.inbound import InboundReviewDecision, InboundReviewPage
from transbridge.application.terminology_sync.models import TerminologySyncMode
from transbridge.application.terminology_sync.service import (
    TerminologySyncApplicationService,
    TerminologySyncPlanRef,
    owner_from_context,
)

from .base import ToolResult
from .terminology_approval import require_review


def _service(ctx) -> TerminologySyncApplicationService:
    factory = getattr(ctx, "terminology_sync_service_factory", None)
    if factory is not None:
        return factory.sync_service_for(_context(ctx))
    direct = getattr(ctx, "terminology_sync_service", None)
    if direct is not None:
        return direct
    raise RuntimeError("terminology synchronization is not configured")


def _context(ctx) -> RequestContext:
    context = getattr(ctx, "request_context", None)
    if isinstance(context, RequestContext):
        return context
    owner_id = str(getattr(ctx, "owner_id", "") or "smart-assistant")
    return RequestContext(
        owner_id=owner_id,
        project_id=getattr(ctx, "project_id", None),
        variant_id=getattr(ctx, "variant_id", None),
        session_id=getattr(ctx, "session_id", None),
        metadata=(("entrypoint", "smart-assistant"),),
    )


def _owner(context: RequestContext) -> OwnerRef:
    return owner_from_context(context, entrypoint="smart-assistant")


def _plan_ref(args: dict[str, Any]) -> TerminologySyncPlanRef:
    return TerminologySyncPlanRef(str(args["plan_id"]), str(args["plan_hash"]))


def _tool_preflight(args: dict, ctx) -> ToolResult:
    try:
        result = _service(ctx).preflight(_context(ctx), TerminologySyncMode(args["mode"]))
        return ToolResult.ok("术语同步预检完成", data=_json_value(result))
    except Exception as exc:
        return _failure(exc)


def _tool_plan(args: dict, ctx) -> ToolResult:
    try:
        service = _service(ctx)
        summary = service.create_plan(_context(ctx), TerminologySyncMode(args["mode"]))
        page = service.page_plan(summary.ref, PageRequest())
        data = _json_value(summary)
        data["items"] = [_json_value(item) for item in page.items]
        data["next_cursor"] = None if page.next_cursor is None else page.next_cursor.encode()
        data["total"] = page.total
        return ToolResult.ok("术语同步计划已生成", data=data)
    except Exception as exc:
        return _failure(exc)


def _tool_activate_mapping(args: dict, ctx) -> ToolResult:
    try:
        raw_token = args.get("replacement_token")
        token = None if raw_token is None else ConfirmationToken(**raw_token)
        result = _service(ctx).activate_mapping(_context(ctx), TerminologySyncMode(args["mode"]), token)
        return ToolResult.ok("术语同步映射已显式启用", data=_json_value(result))
    except Exception as exc:
        return _failure(exc)


def _tool_confirm_mapping_replacement(args: dict, ctx) -> ToolResult:
    try:
        require_review(ctx, "terminology_sync_mapping_confirmation", args)
        token = _service(ctx).issue_mapping_replacement_confirmation(
            _context(ctx),
            TerminologySyncMode(args["mode"]),
        )
        return ToolResult.ok("已确认替换当前目标的 Variant 映射", data=_json_value(token))
    except Exception as exc:
        return _failure(exc)


def _tool_plan_page(args: dict, ctx) -> ToolResult:
    try:
        cursor = args.get("cursor")
        page = _service(ctx).page_plan(
            _plan_ref(args),
            PageRequest(
                cursor=None if not cursor else SnapshotCursor.decode(str(cursor)),
            ),
        )
        return ToolResult.ok(
            "术语同步计划分页已读取",
            data={
                "items": [_json_value(item) for item in page.items],
                "next_cursor": None if page.next_cursor is None else page.next_cursor.encode(),
                "total": page.total,
                "plan_hash": page.snapshot_digest,
            },
        )
    except Exception as exc:
        return _failure(exc)


def _tool_authorize(args: dict, ctx) -> ToolResult:
    try:
        require_review(ctx, "terminology_sync_authorize", args)
        context = _context(ctx)
        token = _service(ctx).issue_confirmation(_plan_ref(args), _owner(context))
        return ToolResult.ok("已签发一次性术语同步确认", data=_json_value(token))
    except Exception as exc:
        return _failure(exc)


def _tool_execute(args: dict, ctx) -> ToolResult:
    try:
        context = _context(ctx)
        raw_token = args.get("confirmation_token")
        token = None if raw_token is None else ConfirmationToken(**raw_token)
        ref = _service(ctx).execute(_plan_ref(args), _owner(context), token)
        return ToolResult.ok("术语同步任务已提交", data=ref.to_dict())
    except Exception as exc:
        return _failure(exc)


def _tool_status(args: dict, ctx) -> ToolResult:
    from transbridge.ui.tools.terminology.sync_projection import outcome_rows

    try:
        context = _context(ctx)
        ref = JobRef.from_dict(args["job"])
        service = _service(ctx)
        owner = _owner(context)
        snapshot = service.status(ref, owner)
        result = service.result(ref, owner) if snapshot.is_terminal else None
        failure = service.failure(ref, owner) if snapshot.is_terminal and result is None else None
        rows = () if result is None else outcome_rows(result, service.result_items(ref, owner))
        return ToolResult.ok(
            "术语同步任务状态已读取",
            data={
                "snapshot": _json_value(snapshot),
                "result": _json_value(result),
                "failure": _json_value(failure),
                "rows": _json_value(rows),
            },
        )
    except Exception as exc:
        return _failure(exc)


def _tool_retry(args: dict, ctx) -> ToolResult:
    try:
        context = _context(ctx)
        ref = _service(ctx).retry(JobRef.from_dict(args["job"]), _owner(context))
        return ToolResult.ok("术语同步重试任务已提交", data=ref.to_dict())
    except Exception as exc:
        return _failure(exc)


def _tool_reconcile(args: dict, ctx) -> ToolResult:
    try:
        context = _context(ctx)
        ref = _service(ctx).reconcile(JobRef.from_dict(args["job"]), _owner(context))
        return ToolResult.ok("术语同步核对任务已提交", data=ref.to_dict())
    except Exception as exc:
        return _failure(exc)


def _tool_list_inbound(args: dict, ctx) -> ToolResult:
    del args
    try:
        values = _service(ctx).list_inbound(_context(ctx))
        return ToolResult.ok(
            "已读取待复核的远端术语变化；这些变化尚未影响当前翻译术语版本",
            data={
                "change_sets": [
                    {
                        "change_set_id": item.change_set_id,
                        "created_at": item.created_at.isoformat(),
                        "total": len(item.items),
                    }
                    for item in values
                ]
            },
        )
    except Exception as exc:
        return _failure(exc)


def _tool_get_inbound(args: dict, ctx) -> ToolResult:
    try:
        value = _service(ctx).get_inbound(_context(ctx), str(args["change_set_id"]))
        page = InboundReviewPage.from_items(value.items, args.get("offset", 0))
        data = {field.name: _json_value(getattr(value, field.name)) for field in fields(value) if field.name != "items"}
        data.update(items=_json_value(page.items), offset=page.offset, total=page.total)
        end = page.offset + len(page.items)
        data["next_offset"] = end if end < page.total else None
        return ToolResult.ok("已读取入站术语变化；尚未影响当前翻译术语版本", data=data)
    except Exception as exc:
        return _failure(exc)


def _tool_preview_inbound(args: dict, ctx) -> ToolResult:
    from transbridge.ui.tools.terminology.sync_projection import proposal_rows

    try:
        if set(args) != {"change_set_id", "choices"}:
            raise ValueError("请提供入站变化集 change_set_id 和逐项审核 choices")
        service = _service(ctx)
        context = _context(ctx)
        change_set = service.get_inbound(context, args["change_set_id"])
        items = {item.item_id: item for item in change_set.items}
        choices = []
        for choice in args["choices"]:
            decision = InboundReviewDecision(choice["decision"])
            expected = (
                {"item_id", "decision", "translation"}
                if decision is InboundReviewDecision.EDIT
                else {"item_id", "decision"}
            )
            if set(choice) != expected:
                raise ValueError("审核项需包含 item_id、decision，并仅在选择 edit 时提供 translation")
            choices.append(
                DraftImportChoice.from_review(items[choice["item_id"]], decision, choice.get("translation", ""))
            )
        selection = service.prepare_import_selection(context, change_set.change_set_id, tuple(choices))
        proposal = service.preview_import(selection)
        return ToolResult.ok(
            "入站变化草稿预览已生成；预览不会影响当前翻译术语版本",
            data={
                "proposal_digest": proposal.proposal_digest,
                "counts": _json_value(proposal.counts),
                "changes": _json_value(proposal_rows(proposal)),
                "diagnostics": list(proposal.diagnostics),
                "committable": proposal.committable,
            },
        )
    except Exception as exc:
        return _failure(exc)


def _tool_commit_inbound(args: dict, ctx) -> ToolResult:
    try:
        require_review(ctx, "terminology_sync_inbound_commit", args)
        digest = args["proposal_digest"]
        context = _context(ctx)
        metadata = dict(context.metadata)
        if not metadata.get("manual_actor_id") and "gui" in context.permissions and metadata.get("entrypoint") == "gui":
            metadata["manual_actor_id"] = context.owner_id
            context = replace(context, metadata=tuple(sorted(metadata.items())))
        result = _service(ctx).commit_import_ref(digest, context)
        return ToolResult.ok(
            "入站变化已写入待发布草稿；仍未影响当前翻译术语版本",
            data=_json_value(result),
        )
    except Exception as exc:
        return _failure(exc)


def _json_value(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "__dataclass_fields__"):
        return {key: _json_value(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_json_value(item) for item in value]
    return value


def _failure(exc: Exception) -> ToolResult:
    from .diagnostics import technical_error

    code = getattr(exc, "code", type(exc).__name__.upper())
    category = "permission" if isinstance(exc, PermissionError) else "input"
    return ToolResult.fail(technical_error(exc), error_category=category, error_code=str(code))


_MODE_SCHEMA = {"type": "string", "enum": [mode.value for mode in TerminologySyncMode]}
_PLAN_REF_PROPERTIES = {"plan_id": {"type": "string"}, "plan_hash": {"type": "string"}}
_JOB_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "schema_version": {"type": "integer", "const": JobRef.SCHEMA_VERSION},
        "job_id": {"type": "string", "minLength": 1},
        "owner_id": {"type": "string", "minLength": 1},
        "run_id": {"type": "string", "minLength": 1},
    },
    "required": ["job_id", "owner_id", "run_id"],
}


def _register_terminology_sync_tools() -> None:
    from ..tool_registry import ToolRegistry

    ToolRegistry.register_tools(
        "terminology_sync",
        [
            {
                "name": "terminology_sync_preflight",
                "display_name": "术语同步预检",
                "description": "①Check terminology backup or bidirectional prerequisites without writing remote data.",
                "execute": _tool_preflight,
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"mode": _MODE_SCHEMA},
                    "required": ["mode"],
                },
            },
            {
                "name": "terminology_sync_plan",
                "display_name": "生成术语同步计划",
                "description": "①Create the shared immutable terminology sync plan and return a bounded first page.",
                "execute": _tool_plan,
                "is_long_running": True,
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"mode": _MODE_SCHEMA},
                    "required": ["mode"],
                },
            },
            {
                "name": "terminology_sync_activate_mapping",
                "display_name": "启用术语同步映射",
                "description": (
                    "①Explicitly bind the current Project/Variant to its verified ParaTranz target; "
                    "no remote write occurs."
                ),
                "execute": _tool_activate_mapping,
                "permission": "write",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"mode": _MODE_SCHEMA, "replacement_token": {"type": "object"}},
                    "required": ["mode"],
                },
            },
            {
                "name": "terminology_sync_mapping_confirmation",
                "display_name": "确认替换同步映射",
                "description": "①Issue a one-use owner-bound confirmation before replacing another Variant mapping.",
                "execute": _tool_confirm_mapping_replacement,
                "permission": "write",
                "require_confirmation": True,
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"mode": _MODE_SCHEMA},
                    "required": ["mode"],
                },
            },
            {
                "name": "terminology_sync_plan_page",
                "display_name": "读取术语同步计划分页",
                "description": "①Read another bounded page from an existing immutable terminology sync plan.",
                "execute": _tool_plan_page,
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {**_PLAN_REF_PROPERTIES, "cursor": {"type": "string"}},
                    "required": ["plan_id", "plan_hash"],
                },
            },
            {
                "name": "terminology_sync_authorize",
                "display_name": "确认术语同步计划",
                "description": "①Issue a request-bound one-use confirmation for the exact plan and owner scope.",
                "execute": _tool_authorize,
                "permission": "write",
                "require_confirmation": True,
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": _PLAN_REF_PROPERTIES,
                    "required": ["plan_id", "plan_hash"],
                },
            },
            {
                "name": "terminology_sync_execute",
                "display_name": "执行术语同步计划",
                "description": (
                    "①Execute the exact fresh plan through TaskRuntime; destructive plans require the issued token."
                ),
                "execute": _tool_execute,
                "permission": "write",
                "is_long_running": True,
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {**_PLAN_REF_PROPERTIES, "confirmation_token": {"type": "object"}},
                    "required": ["plan_id", "plan_hash"],
                },
            },
            {
                "name": "terminology_sync_status",
                "display_name": "术语同步任务状态",
                "description": "①Read TaskRuntime status and the bounded terminal terminology sync result.",
                "execute": _tool_status,
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"job": _JOB_SCHEMA},
                    "required": ["job"],
                },
            },
            {
                "name": "terminology_sync_retry",
                "display_name": "重试术语同步",
                "description": (
                    "①Retry only failed safe items from the selected task result; unknown items require reconciliation."
                ),
                "execute": _tool_retry,
                "permission": "write",
                "is_long_running": True,
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"job": _JOB_SCHEMA},
                    "required": ["job"],
                },
            },
            {
                "name": "terminology_sync_reconcile",
                "display_name": "核对术语同步未知结果",
                "description": (
                    "①Reconcile unknown remote outcomes recorded in the selected task result before retry."
                ),
                "execute": _tool_reconcile,
                "permission": "write",
                "is_long_running": True,
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"job": _JOB_SCHEMA},
                    "required": ["job"],
                },
            },
            {
                "name": "terminology_sync_inbound",
                "display_name": "查看入站术语变化",
                "description": (
                    "①List bounded inbound terminology change sets; reading them never publishes a local version."
                ),
                "execute": _tool_list_inbound,
                "parameters": {"type": "object", "additionalProperties": False, "properties": {}},
            },
            {
                "name": "terminology_sync_inbound_get",
                "display_name": "读取入站术语变化",
                "description": "①Read one bounded page of immutable inbound changes without modifying a draft.",
                "execute": _tool_get_inbound,
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "change_set_id": {"type": "string"},
                        "offset": {"type": "integer", "minimum": 0},
                    },
                    "required": ["change_set_id"],
                },
            },
            {
                "name": "terminology_sync_inbound_preview",
                "display_name": "预览入站草稿变更",
                "description": (
                    "①Preview an explicit accept/reject/edit selection as a local draft proposal without publishing it."
                ),
                "execute": _tool_preview_inbound,
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "change_set_id": {"type": "string"},
                        "choices": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "item_id": {"type": "string"},
                                    "decision": {"type": "string", "enum": [d.value for d in InboundReviewDecision]},
                                    "translation": {"type": "string", "description": "Replacement text for edit"},
                                },
                                "required": ["item_id", "decision"],
                                "if": {"properties": {"decision": {"const": "edit"}}},
                                "then": {"required": ["translation"]},
                                "else": {"not": {"required": ["translation"]}},
                            },
                        },
                    },
                    "required": ["change_set_id", "choices"],
                },
            },
            {
                "name": "terminology_sync_inbound_commit",
                "display_name": "提交入站草稿变更",
                "description": (
                    "①Commit an exact previewed proposal into the local draft; this still does not publish a version."
                ),
                "execute": _tool_commit_inbound,
                "permission": "write",
                "require_confirmation": True,
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"proposal_digest": {"type": "string"}},
                    "required": ["proposal_digest"],
                },
            },
        ],
    )


_register_terminology_sync_tools()


__all__ = [
    "_tool_authorize",
    "_tool_activate_mapping",
    "_tool_confirm_mapping_replacement",
    "_tool_execute",
    "_tool_plan",
    "_tool_plan_page",
    "_tool_preflight",
    "_tool_retry",
    "_tool_reconcile",
    "_tool_list_inbound",
    "_tool_get_inbound",
    "_tool_preview_inbound",
    "_tool_commit_inbound",
    "_tool_status",
]
