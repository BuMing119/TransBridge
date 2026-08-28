from __future__ import annotations

from collections.abc import Callable

from .checkpoint_manager import CheckpointManager  # noqa: F401
from .condition_evaluator import ConditionEvaluator  # noqa: F401
from .graph_executor import GraphExecutor, StepResult


class ExecutionEngine:
    """统一执行引擎：委托 GraphExecutor 执行 BFS 图调度。

    ADR-008: 后端核心不依赖 PyQt6。Story 01/M1: 组合 ConditionEvaluator +
    CheckpointManager + GraphExecutor。BFS 执行逻辑完整提取到 GraphExecutor。
    """

    _MAX_WORKERS = 5
    _CONFIRM_TIMEOUT = 300.0
    _MAX_DISPATCH_DEPTH = 50
    _SAFE_SERIALIZE_MAX_CHARS = 2000

    def __init__(self, tool_registry, ctx, middlewares=None):
        self._executor = GraphExecutor(tool_registry, ctx, middlewares)
        self._condition_evaluator = self._executor._condition_evaluator
        self._checkpoint_manager = self._executor._checkpoint_manager

    # ── 图执行 ──────────────────────────────────────────────────

    def execute(self, steps: list[dict]) -> list[StepResult]:
        return self._executor.execute(steps)

    def execute_graph(self, graph, *, checkpoint_identity=None) -> list[StepResult]:
        return self._executor.execute_graph(graph, checkpoint_identity=checkpoint_identity)

    # ── 生命周期 ────────────────────────────────────────────────

    def pause(self) -> None:
        self._executor.pause()

    def resume(self) -> None:
        self._executor.resume()

    def cancel(self) -> None:
        self._executor.cancel()

    def shutdown(self):
        self._executor.shutdown()

    # ── 决策注入 ────────────────────────────────────────────────

    def provide_decision(self, node_id: str, choice: str) -> None:
        self._executor.provide_decision(node_id, choice)

    # ── 回调注册 ────────────────────────────────────────────────

    def on_step_started(self, callback: Callable) -> None:
        self._executor.on_step_started(callback)

    def on_step_finished(self, callback: Callable) -> None:
        self._executor.on_step_finished(callback)

    def on_all_finished(self, callback: Callable) -> None:
        self._executor.on_all_finished(callback)

    def on_progress(self, callback: Callable) -> None:
        self._executor.on_progress(callback)

    def on_step_retrying(self, callback: Callable) -> None:
        self._executor.on_step_retrying(callback)

    def on_step_requires_confirmation(self, callback: Callable) -> None:
        self._executor.on_step_requires_confirmation(callback)

    def __getattr__(self, name: str):
        """代理未命中属性到 GraphExecutor（向后兼容测试/内部访问）。"""
        if name.startswith("_") and name != "_executor":
            try:
                return getattr(self._executor, name)
            except AttributeError:
                pass
        raise AttributeError(f"'{type(self).__name__}' has no attribute '{name}'")
