from datetime import datetime

from src.transbridge.smart_assistant.workers.async_worker import AsyncWorker
from ..execution_engine import StepResult
from ..tool_registry import ToolRegistry


class AgentWorker(AsyncWorker):
    """Agent 工具执行后台线程。

    Phase 2: 从 QThread+pyqtSignal 迁移到 AsyncWorker(threading.Thread)+回调。
    调用方通过 on_progress/on_finished/on_error 属性注册回调，
    需自行保证跨线程 Qt GUI 安全。
    """

    def __init__(self, step: dict, instance):
        super().__init__(daemon=True)
        self._step = step
        self._instance = instance
        self.on_progress: callable | None = None
        self.on_finished: callable | None = None
        self.on_error: callable | None = None

    def run(self):
        if self._instance is None or self._instance.agent_spec is None:
            if self.on_error:
                self.on_error("AgentInstance 未正确设置")
            return
        if self._instance.ctx is None:
            if self.on_error:
                self.on_error("AgentInstance.ctx 未设置")
            return

        tool_name = self._step.get("tool", "")
        namespace = self._instance.agent_spec.namespace
        tool = ToolRegistry.get(tool_name, namespace=namespace)
        if tool is None:
            if self.on_error:
                self.on_error(f"工具不存在或无权访问: {tool_name} (namespace={namespace})")
            return

        # M7: 在工具执行前检查取消标志
        if self.is_cancelled():
            if self.on_error:
                self.on_error("任务已取消")
            return

        start = datetime.now()
        try:
            if self.on_progress:
                self.on_progress(f"{self._instance.agent_spec.name} 正在执行 {tool_name}...")
            from src.transbridge.smart_assistant.tools.base import (
                ExecutionContext, execute_with_guardrails,
            )
            from src.transbridge.smart_assistant.tools.task_manager import TaskManager
            exec_ctx = ExecutionContext(app_context=self._instance.ctx, task_manager=TaskManager())
            result = execute_with_guardrails(tool, self._step.get("args", {}), exec_ctx)
            duration_ms = int((datetime.now() - start).total_seconds() * 1000)
            sr = StepResult(
                step_id=self._step["id"],
                tool=tool_name,
                success=result.get("success", True),
                message=result.get("message", ""),
                data=result.get("data"),
                duration_ms=duration_ms,
            )
            if self.on_finished:
                self.on_finished(sr)
        except Exception as exc:
            if self.on_error:
                self.on_error(str(exc))
