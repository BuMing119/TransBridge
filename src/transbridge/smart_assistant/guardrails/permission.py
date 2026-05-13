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
        # ("write_confirm_required" / "admin_confirm_required")，不触发 UI 弹窗。
        # 实际弹窗/确认逻辑由上层调用方（chat_widget.py 的 ReAct/Auto 模式
        # 或 ExecutionEngine 的 HITL 循环）检测 GuardResult 后通过信号机制触发，
        # 此文件不负责 UI 交互。MCP 通道无 HITL 支持（见 mcp/adapter.py M11）。
        if perm == "write":
            if self._write_require_confirm or getattr(spec, 'require_confirmation', False):
                return GuardResult(False, "write_confirm_required")
            return GuardResult(True)
        if perm == "admin":
            if self._enable_admin_confirm:
                return GuardResult(False, "admin_confirm_required")
            return GuardResult(True)
        return GuardResult(True)

    def after_execute(self, step, result, ctx) -> GuardResult:
        return GuardResult(True)
