"""ToolExecutionHandler — 工具执行与护栏管理。

从 ChatWidget 提取，遵循 ADR-008 代码分层。
通过回调与 UI 层通信，不持有 ChatWidget 引用。
"""
import logging
from typing import Any, Callable

from src.transbridge.smart_assistant.tools.base import ToolResult

logger = logging.getLogger(__name__)


class ToolExecutionHandler:
    """工具查找、权限检查、执行编排、重试、结果处理。

    仅通过回调函数与 UI 层交互，不直接操作任何 QWidget。
    """

    def __init__(
        self,
        ctx,
        conversation_manager,
        *,
        on_system_message: Callable[[str], None] | None = None,
        on_plan_card: Callable[[list], None] | None = None,
        on_tool_card: Callable[[dict], None] | None = None,
        on_batch_tool_card: Callable[[list], None] | None = None,
        on_plan_confirmed: Callable[[list], None] | None = None,
        on_react_continue: Callable[[], None] | None = None,
        on_confirm_permission: Callable[[str, str], bool] | None = None,
    ):
        self._ctx = ctx
        self._conversation = conversation_manager
        self._middlewares: list | None = None

        # 回调注入
        self._on_system_message = on_system_message or (lambda _: None)
        self._on_plan_card = on_plan_card or (lambda _: None)
        self._on_tool_card = on_tool_card or (lambda _: None)
        self._on_batch_tool_card = on_batch_tool_card or (lambda _: None)
        self._on_plan_confirmed = on_plan_confirmed or (lambda _: None)
        self._on_react_continue = on_react_continue or (lambda: None)
        self._on_confirm_permission = on_confirm_permission

    # ── 护栏 ──────────────────────────────────────────────

    def _ensure_middlewares(self) -> list:
        """B1: 延迟构建护栏中间件链，遵循用户配置。"""
        if self._middlewares is not None:
            return self._middlewares
        try:
            from src.transbridge.paratranz.config_manager import LLMConfig
            cfg = LLMConfig.load_from_file()
        except Exception as e:
            logger.warning("护栏配置加载失败，使用默认护栏链: %s", e)
            from src.transbridge.smart_assistant.tools.base import _build_guard_chain
            self._middlewares = _build_guard_chain() or []
            return self._middlewares

        from src.transbridge.smart_assistant.guardrails import PermissionGuard, InputValidationGuard, OutputValidationGuard
        middlewares = []
        if getattr(cfg, 'guardrails_enable_input_validation', True):
            middlewares.append(InputValidationGuard(getattr(cfg, 'guardrails_max_input_size', 102400)))
        middlewares.append(PermissionGuard(
            enable_admin_confirm=getattr(cfg, 'guardrails_enable_admin_confirm', True),
            write_require_confirm=getattr(cfg, 'guardrails_write_require_confirm', False),
        ))
        if getattr(cfg, 'guardrails_enable_output_validation', True):
            middlewares.append(OutputValidationGuard())
        self._middlewares = middlewares
        return middlewares

    # ── 权限检查 ──────────────────────────────────────────

    @staticmethod
    def _needs_confirm(step: dict) -> bool:
        """检查步骤是否需要用户确认（admin 级或显式标记）。"""
        from .tool_registry import ToolRegistry
        spec = ToolRegistry.get(step.get("tool", ""))
        if spec is None:
            return False
        return (getattr(spec, "permission", "") == "admin"
                or getattr(spec, "require_confirmation", False))

    # ── 执行上下文构建 ──────────────────────────────────

    def _build_execution_context(self):
        """构建工具执行上下文 (ExecutionContext)。"""
        from src.transbridge.smart_assistant.tools.base import ExecutionContext
        from src.transbridge.smart_assistant.tools.task_manager import TaskManager
        return ExecutionContext(app_context=self._ctx, task_manager=TaskManager())

    # ── 权限预检查 ──────────────────────────────────────

    def _check_permission(
        self, step: dict, middlewares: list, exec_ctx,
        skip_react_continue: bool, mode: str,
    ) -> tuple:
        """CR3: ReAct/Plan 权限预检查。

        Args:
            step: 工具步骤字典。
            middlewares: 护栏中间件链。
            exec_ctx: 执行上下文。
            skip_react_continue: 是否跳过 ReAct 继续触发。
            mode: "react" 或 "plan"。

        Returns:
            (allowed, filtered_middlewares): allowed 为 False 表示用户拒绝，
            权限结果已通过 _handle_result 处理。
        """
        from src.transbridge.smart_assistant.guardrails.permission import PermissionGuard
        perm_guard = next((mw for mw in middlewares if isinstance(mw, PermissionGuard)), None)
        if perm_guard is None:
            return True, middlewares

        perm_result = perm_guard.before_execute(step, exec_ctx)
        if perm_result.allowed or not perm_result.requires_confirmation:
            return True, middlewares

        tool_name = step.get("tool", "?")
        perm_label = "管理级" if perm_result.requires_confirmation == "admin" else "写入级"
        if self._on_confirm_permission is not None:
            confirmed = self._on_confirm_permission(
                "操作确认",
                f"工具 '{tool_name}' 需要{perm_label}权限确认。是否继续？",
            )
            if not confirmed:
                result = ToolResult.fail(f"用户拒绝{perm_label}操作: {tool_name}")
                self._handle_result(step, result, skip_react_continue=skip_react_continue, mode=mode)
                return False, middlewares

        # 用户确认后，移除权限护栏以避免执行时二次检查
        filtered = [mw for mw in middlewares if not isinstance(mw, PermissionGuard)]
        return True, filtered

    # ── 重试执行 ──────────────────────────────────────

    def _execute_with_retry(self, spec, step: dict, middlewares: list, exec_ctx) -> ToolResult:
        """M6: 带重试循环与 Reflexion 调整的工具执行。

        Args:
            spec: 工具注册信息。
            step: 工具步骤字典（含 args 等参数）。
            middlewares: 已过滤的护栏中间件链。
            exec_ctx: 执行上下文。

        Returns:
            最终执行结果 (ToolResult)。
        """
        from src.transbridge.smart_assistant.tools.base import execute_with_guardrails

        try:
            from src.transbridge.smart_assistant.reflexion.retry_handler import RetryHandler
            retry_handler = RetryHandler()
        except ImportError:
            retry_handler = None

        max_attempts = retry_handler.MAX_RETRIES + 1 if retry_handler else 3
        current_step = dict(step)
        for attempt in range(max_attempts):
            try:
                result = execute_with_guardrails(
                    spec, current_step.get("args", {}), exec_ctx,
                    middlewares=middlewares,
                )
            except Exception as exc:
                result = ToolResult.fail(str(exc))

            if getattr(result, 'success', False) or attempt == max_attempts - 1:
                return result

            err_msg = getattr(result, 'message', '')
            if retry_handler and retry_handler.should_retry(err_msg):
                adjusted = retry_handler.analyze_and_adjust(current_step, err_msg, attempt)
                if adjusted:
                    current_step = adjusted
                    self._on_system_message(f"[重试 {attempt + 2}/{max_attempts}] {spec.name}: {err_msg}")
                    continue
            return result

        return result

    # ── 单步执行 ──────────────────────────────────────────

    def execute_step(self, step: dict, skip_react_continue: bool = False, mode: str = "react") -> ToolResult | None:
        """执行单个工具步骤。

        Args:
            step: 工具步骤字典，含 tool/args 等字段。
            skip_react_continue: 为 True 时不在执行后触发 ReAct 继续（批量场景由调用方统一触发）。
            mode: "react" 或 "plan"，影响执行后是否触发 ReAct 继续。

        Returns:
            ToolResult 或 None（用户拒绝权限确认时返回 None）。
        """
        tool_name = step.get("tool", "?")
        from .tool_registry import ToolRegistry
        spec = ToolRegistry.get(tool_name)

        if not spec or not spec.execute:
            result = ToolResult.fail(f"未知工具: {tool_name}")
            self._handle_result(step, result, skip_react_continue=skip_react_continue, mode=mode)
            return result

        exec_ctx = self._build_execution_context()
        middlewares = self._ensure_middlewares()

        allowed, middlewares = self._check_permission(
            step, middlewares, exec_ctx,
            skip_react_continue=skip_react_continue, mode=mode,
        )
        if not allowed:
            return None

        result = self._execute_with_retry(spec, step, middlewares, exec_ctx)
        self._handle_result(step, result, skip_react_continue=skip_react_continue, mode=mode)
        return result

    # ── 结果处理 ──────────────────────────────────────────

    def _handle_result(self, step: dict, result, skip_react_continue: bool = False, mode: str = "react") -> None:
        """统一处理工具执行结果，使用 ToolResult.to_observation() 生成富文本观察。

        Args:
            step: 工具步骤字典。
            result: 执行结果。
            skip_react_continue: 为 True 时不触发 ReAct 继续（批量场景由调用方统一触发）。
            mode: "react" 或 "plan"，plan 模式下不触发 ReAct 继续。
        """
        tool_name = step.get("tool", "?")
        # Normalize to ToolResult
        if isinstance(result, ToolResult):
            tr = result
        elif isinstance(result, dict):
            tr = ToolResult(
                success=result.get("success", True),
                message=result.get("message", result.get("error", "")),
                data=result.get("data"),
            )
        else:
            tr = ToolResult(success=False, message=str(result))

        # UI display (simple status line)
        self._on_system_message(
            f"[{'OK' if tr.success else 'FAIL'}] {tool_name}: {tr.message or ('完成' if tr.success else '失败')}"
        )
        # LLM observation (rich, with data)
        self._conversation.add_observation(tool_name, tr.to_observation(tool_name))

        # M13/M67: plan 模式下不触发 ReAct 继续
        if not skip_react_continue and mode != "plan":
            self._on_react_continue()

    # ── 自动模式批量执行 ──────────────────────────────────
    # M62: auto_execute_steps 已迁移至 ChatWidget._auto_execute_steps，
    # 统一通过 add_plan_card / add_tool_card / add_batch_tool_card 路由，
    # 消除与 orchestrator._on_finished 双重分发路径。
