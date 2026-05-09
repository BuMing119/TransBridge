import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

from PyQt6.QtCore import QObject, pyqtSignal


@dataclass
class StepResult:
    step_id: int
    tool: str
    success: bool
    message: str
    data: Any = None
    duration_ms: int = 0


class ExecutionEngine(QObject):
    """统一执行引擎：DAG 拓扑排序 + 层级并行执行。"""

    step_started = pyqtSignal(int, str)       # step_id, tool_name
    step_finished = pyqtSignal(StepResult)
    all_finished = pyqtSignal(list)            # list[StepResult]
    progress = pyqtSignal(int, int)            # completed, total

    _MAX_WORKERS = 4

    def __init__(self, tool_registry, ctx, parent=None):
        super().__init__(parent)
        self._registry = tool_registry
        self._ctx = ctx
        self._cancelled = threading.Event()

    def execute(self, steps: list[dict]) -> list[StepResult]:
        """拓扑排序 → 层级并行执行 → 返回所有结果。"""
        self._cancelled.clear()
        results: dict[int, StepResult] = {}
        levels = self._topological_levels(steps)
        total = len(steps)
        completed = 0

        for level in levels:
            if self._cancelled.is_set():
                break

            workers = min(len(level), self._MAX_WORKERS)
            with ThreadPoolExecutor(max_workers=max(workers, 1)) as pool:
                futures = {pool.submit(self._run_single, s): s for s in level}
                for future in as_completed(futures):
                    if self._cancelled.is_set():
                        break
                    step = futures[future]
                    try:
                        result = future.result()
                    except Exception as exc:
                        result = StepResult(
                            step_id=step["id"], tool=step.get("tool", "?"),
                            success=False, message=f"异常: {exc}",
                            duration_ms=0,
                        )
                    results[step["id"]] = result
                    completed += 1
                    self.step_finished.emit(result)
                    self.progress.emit(completed, total)

        final = [results.get(s["id"]) for s in steps if s["id"] in results]
        self.all_finished.emit(final)
        return final

    def cancel(self) -> None:
        self._cancelled.set()

    def _topological_levels(self, steps: list[dict]) -> list[list[dict]]:
        """按 depends_on 分层，同层可并行。有环时兜底单层串行。"""
        step_map = {s["id"]: s for s in steps}
        in_degree: dict[int, int] = {
            s["id"]: len(s.get("depends_on", [])) for s in steps
        }
        remaining = set(step_map.keys())
        levels = []

        while remaining:
            current = [
                step_map[sid] for sid in remaining if in_degree[sid] == 0
            ]
            if not current:
                # 有环，兜底：全部剩余放一层串行
                return [[step_map[sid] for sid in remaining]]

            levels.append(current)
            for step in current:
                remaining.discard(step["id"])
                for sid in remaining:
                    deps = step_map[sid].get("depends_on", [])
                    if step["id"] in deps:
                        in_degree[sid] -= 1

        return levels

    def _run_single(self, step: dict) -> StepResult:
        """执行单个步骤。"""
        step_id = step["id"]
        tool_name = step.get("tool", "?")
        args = step.get("args", {})

        self.step_started.emit(step_id, tool_name)
        start = time.monotonic()

        spec = self._registry.get(tool_name)
        if spec is None:
            return StepResult(
                step_id=step_id, tool=tool_name,
                success=False, message=f"未知工具: {tool_name}",
                duration_ms=int((time.monotonic() - start) * 1000),
            )

        try:
            result = spec.execute(args, self._ctx)
            return StepResult(
                step_id=step_id, tool=tool_name,
                success=result.get("success", True),
                message=result.get("message", ""),
                data=result.get("data"),
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except Exception as exc:
            return StepResult(
                step_id=step_id, tool=tool_name,
                success=False, message=f"执行异常: {exc}",
                duration_ms=int((time.monotonic() - start) * 1000),
            )
