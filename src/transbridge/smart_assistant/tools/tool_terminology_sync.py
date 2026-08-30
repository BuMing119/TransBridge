"""Smart Assistant projection of the shared terminology sync application facade."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from transbridge.application.contracts import JobRef, RequestContext
from transbridge.application.security.hitl import ConfirmationToken
from transbridge.application.tasks import OwnerRef
from transbridge.application.terminology.drafts import DraftLineState, DraftWriteExpectation
from transbridge.application.terminology.ports import PageRequest, SnapshotCursor
from transbridge.application.terminology_sync.draft_import_models import DraftImportChoice, DraftImportSelection
from transbridge.application.terminology_sync.execution_models import TerminologySyncRetryToken
from transbridge.application.terminology_sync.inbound import InboundReviewDecision
from transbridge.application.terminology_sync.models import TerminologySyncMode
from transbridge.application.terminology_sync.plan_models import TerminologyContentSummary
from transbridge.application.terminology_sync.service import (
    TerminologySyncApplicationService,
    TerminologySyncPlanRef,
    owner_from_context,
)

from .base import ToolResult


def _service(ctx) -> TerminologySyncApplicationService:
    direct = getattr(ctx, "terminology_sync_service", None)
    if isinstance(direct, TerminologySyncApplicationService) or direct is not None:
        return direct
    app = getattr(ctx, "app_context", None)
    runtime = getattr(app, "app_runtime", None) or getattr(app, "runtime", None)
    use_cases = getattr(runtime, "use_cases", None)
    if use_cases is not None:
        names = use_cases.names()
        if "terminology_sync_service" in names:
            return use_cases.resolve("terminology_sync_service")
        if "terminology_sync_service_factory" in names:
            factory = use_cases.resolve("terminology_sync_service_factory")
            create = getattr(factory, "sync_service_for", None)
            if callable(create):
                return create(_context(ctx))
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
        page = service.page_plan(summary.ref, PageRequest(limit=int(args.get("limit", 100))))
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
                limit=int(args.get("limit", 100)),
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
    try:
        context = _context(ctx)
        ref = JobRef.from_dict(args["job"])
        service = _service(ctx)
        snapshot = service.status(ref, _owner(context))
        result = service.result(ref, _owner(context)) if snapshot.is_terminal else None
        return ToolResult.ok(
            "术语同步任务状态已读取",
            data={"snapshot": _json_value(snapshot), "result": None if result is None else _json_value(result)},
        )
    except Exception as exc:
        return _failure(exc)


def _tool_retry(args: dict, ctx) -> ToolResult:
    try:
        context = _context(ctx)
        token = TerminologySyncRetryToken(**args["retry_token"])
        ref = _service(ctx).retry(token, _owner(context))
        return ToolResult.ok("术语同步重试任务已提交", data=ref.to_dict())
    except Exception as exc:
        return _failure(exc)


def _tool_reconcile(args: dict, ctx) -> ToolResult:
    try:
        context = _context(ctx)
        token = TerminologySyncRetryToken(**args["retry_token"])
        ref = _service(ctx).reconcile(token, _owner(context))
        return ToolResult.ok("术语同步核对任务已提交", data=ref.to_dict())
    except Exception as exc:
        return _failure(exc)


def _tool_list_inbound(args: dict, ctx) -> ToolResult:
    del args
    try:
        values = _service(ctx).list_inbound(_context(ctx))
        return ToolResult.ok(
            "已读取待复核的远端术语变化；这些变化尚未影响当前翻译术语版本",
            data={"change_sets": [_json_value(item) for item in values]},
        )
    except Exception as exc:
        return _failure(exc)


def _tool_get_inbound(args: dict, ctx) -> ToolResult:
    try:
        value = _service(ctx).get_inbound(_context(ctx), str(args["change_set_id"]))
        offset = max(0, int(args.get("offset", 0)))
        limit = min(1000, max(1, int(args.get("limit", 100))))
        data = _json_value(value)
        data["items"] = [_json_value(item) for item in value.items[offset : offset + limit]]
        data["total"] = len(value.items)
        data["next_offset"] = offset + limit if offset + limit < len(value.items) else None
        return ToolResult.ok("已读取入站术语变化；尚未影响当前翻译术语版本", data=data)
    except Exception as exc:
        return _failure(exc)


def _tool_preview_inbound(args: dict, ctx) -> ToolResult:
    try:
        proposal = _service(ctx).preview_import(_selection(args["selection"]))
        return ToolResult.ok(
            "入站变化草稿预览已生成；预览不会影响当前翻译术语版本",
            data={
                "proposal_digest": proposal.proposal_digest,
                "counts": _json_value(proposal.counts),
                "diagnostics": list(proposal.diagnostics),
                "committable": proposal.committable,
            },
        )
    except Exception as exc:
        return _failure(exc)


def _tool_commit_inbound(args: dict, ctx) -> ToolResult:
    try:
        result = _service(ctx).commit_import_ref(str(args["proposal_digest"]), _context(ctx))
        return ToolResult.ok(
            "入站变化已写入待发布草稿；仍未影响当前翻译术语版本",
            data=_json_value(result),
        )
    except Exception as exc:
        return _failure(exc)


def _selection(raw: dict[str, Any]) -> DraftImportSelection:
    line = DraftLineState(**raw["expected_line"])
    expectation_raw = raw.get("draft_expectation")
    expectation = None
    if expectation_raw is not None:
        expectation = DraftWriteExpectation(
            str(expectation_raw["draft_id"]),
            int(expectation_raw["draft_revision"]),
            str(expectation_raw["decision_set_digest"]),
            line,
        )
    choices = []
    for value in raw["choices"]:
        edited = value.get("edited")
        choices.append(
            DraftImportChoice(
                str(value["item_id"]),
                InboundReviewDecision(str(value["decision"])),
                None if edited is None else TerminologyContentSummary(**edited),
                value.get("reason"),
            )
        )
    return DraftImportSelection(
        str(raw["change_set_id"]),
        str(raw["change_set_content_digest"]),
        int(raw["expected_review_revision"]),
        line,
        tuple(choices),
        expectation,
    )


def _json_value(value):
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
    code = getattr(exc, "code", type(exc).__name__.upper())
    category = "permission" if isinstance(exc, PermissionError) else "input"
    return ToolResult.fail(str(exc), error_category=category, error_code=str(code))


_MODE_SCHEMA = {"type": "string", "enum": [mode.value for mode in TerminologySyncMode]}
_PLAN_REF_PROPERTIES = {"plan_id": {"type": "string"}, "plan_hash": {"type": "string"}}


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
                "parameters": {"type": "object", "properties": {"mode": _MODE_SCHEMA}, "required": ["mode"]},
            },
            {
                "name": "terminology_sync_plan",
                "display_name": "生成术语同步计划",
                "description": "①Create the shared immutable terminology sync plan and return a bounded first page.",
                "execute": _tool_plan,
                "is_long_running": True,
                "parameters": {
                    "type": "object",
                    "properties": {"mode": _MODE_SCHEMA, "limit": {"type": "integer", "minimum": 1, "maximum": 1000}},
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
                "parameters": {"type": "object", "properties": {"mode": _MODE_SCHEMA}, "required": ["mode"]},
            },
            {
                "name": "terminology_sync_plan_page",
                "display_name": "读取术语同步计划分页",
                "description": "①Read another bounded page from an existing immutable terminology sync plan.",
                "execute": _tool_plan_page,
                "parameters": {
                    "type": "object",
                    "properties": {**_PLAN_REF_PROPERTIES, "cursor": {"type": "string"}, "limit": {"type": "integer"}},
                    "required": ["plan_id", "plan_hash"],
                },
            },
            {
                "name": "terminology_sync_authorize",
                "display_name": "确认术语同步计划",
                "description": "①Issue a request-bound one-use confirmation for the exact plan and owner scope.",
                "execute": _tool_authorize,
                "permission": "write",
                "parameters": {
                    "type": "object",
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
                    "properties": {**_PLAN_REF_PROPERTIES, "confirmation_token": {"type": "object"}},
                    "required": ["plan_id", "plan_hash"],
                },
            },
            {
                "name": "terminology_sync_status",
                "display_name": "术语同步任务状态",
                "description": "①Read TaskRuntime status and the bounded terminal terminology sync result.",
                "execute": _tool_status,
                "parameters": {"type": "object", "properties": {"job": {"type": "object"}}, "required": ["job"]},
            },
            {
                "name": "terminology_sync_retry",
                "display_name": "重试术语同步",
                "description": (
                    "①Retry only failed safe items using an owner-bound retry token; "
                    "unknown items require reconciliation."
                ),
                "execute": _tool_retry,
                "permission": "write",
                "is_long_running": True,
                "parameters": {
                    "type": "object",
                    "properties": {"retry_token": {"type": "object"}},
                    "required": ["retry_token"],
                },
            },
            {
                "name": "terminology_sync_reconcile",
                "display_name": "核对术语同步未知结果",
                "description": (
                    "①Reconcile unknown remote outcomes before any retry using the same owner-bound evidence token."
                ),
                "execute": _tool_reconcile,
                "permission": "write",
                "is_long_running": True,
                "parameters": {
                    "type": "object",
                    "properties": {"retry_token": {"type": "object"}},
                    "required": ["retry_token"],
                },
            },
            {
                "name": "terminology_sync_inbound",
                "display_name": "查看入站术语变化",
                "description": (
                    "①List bounded inbound terminology change sets; reading them never publishes a local version."
                ),
                "execute": _tool_list_inbound,
                "parameters": {"type": "object", "properties": {}},
            },
            {
                "name": "terminology_sync_inbound_get",
                "display_name": "读取入站术语变化",
                "description": "①Read one bounded page of immutable inbound changes without modifying a draft.",
                "execute": _tool_get_inbound,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "change_set_id": {"type": "string"},
                        "offset": {"type": "integer", "minimum": 0},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
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
                    "properties": {"selection": {"type": "object"}},
                    "required": ["selection"],
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
                "parameters": {
                    "type": "object",
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
