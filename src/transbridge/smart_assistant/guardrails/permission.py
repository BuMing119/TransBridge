import logging

from ..tool_registry import ToolRegistry
from .base import GuardMiddleware, GuardResult

logger = logging.getLogger(__name__)


class PermissionGuard(GuardMiddleware):
    def __init__(self, enable_admin_confirm: bool = True,
                 write_require_confirm: bool = False):
        self._enable_admin_confirm = enable_admin_confirm
        self._write_require_confirm = write_require_confirm

    def before_execute(self, step, ctx) -> GuardResult:
        tool_name = step.get("tool", "")
        spec = ToolRegistry.get(tool_name)
        if spec is None:
            logger.warning("PermissionGuard: 未知工具 %s", tool_name)
            return GuardResult(False, f"未知工具: {tool_name}")

        perm = getattr(spec, 'permission', 'read')
        if perm == "read":
            return GuardResult(True)
        # M8: PermissionGuard 在 execute_with_guardrails 中仅返回状态文本
        # 上级调用方通过 GuardResult.requires_confirmation 字段判断
        # 是否需要弹窗确认，不再依赖 reason 中的 magic string。
        if perm == "write":
            if self._write_require_confirm or getattr(spec, 'require_confirmation', False):
                return GuardResult(False, "需要写入权限确认", requires_confirmation="write")
            return GuardResult(True)
        if perm == "admin":
            if self._enable_admin_confirm:
                return GuardResult(False, "需要管理级权限确认", requires_confirmation="admin")
            return GuardResult(True)
        return GuardResult(True)

    def after_execute(self, step, result, ctx) -> GuardResult:
        return GuardResult(True)
