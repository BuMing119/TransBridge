from datetime import datetime

from PyQt6.QtCore import QThread, pyqtSignal

from ..execution_engine import StepResult
from ..tool_registry import ToolRegistry


class AgentWorker(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(StepResult)
    error = pyqtSignal(str)

    def __init__(self, step: dict, instance, parent=None):
        super().__init__(parent)
        self._step = step
        self._instance = instance
        self._cancelled = False

    def run(self):
        if self._instance is None or self._instance.agent_spec is None:
            self.error.emit("AgentInstance 未正确设置")
            return
        if self._instance.ctx is None:
            self.error.emit("AgentInstance.ctx 未设置")
            return

        tool_name = self._step.get("tool", "")
        namespace = self._instance.agent_spec.namespace
        tool = ToolRegistry.get(tool_name, namespace=namespace)
        if tool is None:
            self.error.emit(f"工具不存在或无权访问: {tool_name} (namespace={namespace})")
            return

        start = datetime.now()
        try:
            self.progress.emit(f"{self._instance.agent_spec.name} 正在执行 {tool_name}...")
            result = tool.execute(self._step.get("args", {}), self._instance.ctx)
            duration_ms = int((datetime.now() - start).total_seconds() * 1000)
            sr = StepResult(
                step_id=self._step["id"],
                tool=tool_name,
                success=result.get("success", True),
                message=result.get("message", ""),
                data=result.get("data"),
                duration_ms=duration_ms,
            )
            self.finished.emit(sr)
        except Exception as exc:
            self.error.emit(str(exc))

    def cancel(self):
        self._cancelled = True
