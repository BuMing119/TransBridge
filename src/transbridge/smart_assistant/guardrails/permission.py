import logging

from transbridge.application.tools.contracts import ToolInvocation

from ..tool_registry import ToolRegistry
from .base import GuardMiddleware, GuardResult

logger = logging.getLogger(__name__)


class PermissionGuard(GuardMiddleware):
    def __init__(self, enable_admin_confirm: bool = True, write_require_confirm: bool = False):
        self._enable_admin_confirm = enable_admin_confirm
        self._write_require_confirm = write_require_confirm

    def before_execute(self, step, ctx) -> GuardResult:
        tool_name = step.get("tool", "")
        spec = ToolRegistry.get(tool_name)
        if spec is None:
            logger.warning("PermissionGuard: 未知工具 %s", tool_name)
            return GuardResult(False, f"未知工具: {tool_name}")

        perm = getattr(spec, "permission", "read")
        if perm == "read":
            return GuardResult(True)
        # M8: PermissionGuard 在 execute_with_guardrails 中仅返回状态文本
        # 上级调用方通过 GuardResult.requires_confirmation 字段判断
        # 是否需要弹窗确认，不再依赖 reason 中的 magic string。
        if perm == "write":
            if self._write_require_confirm or getattr(spec, "require_confirmation", False):
                return self._confirm_or_request(step, ctx, "write")
            return GuardResult(True)
        if perm == "admin":
            if self._enable_admin_confirm:
                return self._confirm_or_request(step, ctx, "admin")
            return GuardResult(True)
        return GuardResult(True)

    @staticmethod
    def _confirm_or_request(step, ctx, level: str) -> GuardResult:
        request_context = getattr(ctx, "request_context", None)
        owner_id = getattr(request_context, "owner_id", "") or getattr(ctx, "owner_id", "")
        plan_hash = getattr(ctx, "plan_hash", "")
        invocation = ToolInvocation(
            tool_name=step.get("tool", ""),
            arguments=step.get("args", {}),
            owner_id=owner_id,
            plan_hash=plan_hash,
        )
        authority = getattr(ctx, "confirmation_authority", None)
        token = getattr(ctx, "confirmation_token", None)
        if authority is None or token is None:
            label = "写入" if level == "write" else "管理级"
            return GuardResult(
                False,
                f"需要{label}权限确认",
                requires_confirmation=level,
                code="CONFIRMATION_REQUIRED",
            )
        decision = authority.consume(
            token,
            owner_id=owner_id,
            request_hash=invocation.request_hash,
        )
        ctx.confirmation_token = None
        if decision.allowed:
            return GuardResult(True, code=decision.code)
        return GuardResult(False, decision.reason, code=decision.code)

    def after_execute(self, step, result, ctx) -> GuardResult:
        return GuardResult(True)
