from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import hashlib
import json
import logging
import threading
import time
from typing import Any
from uuid import uuid4

from transbridge.application.tasks import (
    CheckpointExpectation,
    CheckpointFrontier,
    CheckpointRecord,
    OwnerRef,
)
from transbridge.config.paratranz import ParatranzConfig

from .checkpoint_manager import CheckpointManager
from .condition_evaluator import ConditionEvaluator

logger = logging.getLogger(__name__)


class GraphCheckpointFault(RuntimeError):
    """Fault-injection exception that must escape result conversion."""


@dataclass
class StepResult:
    step_id: int
    tool: str
    success: bool
    message: str
    data: Any = None
    duration_ms: int = 0
    agent_instance_id: str = ""


class GraphExecutor:
    """统一执行引擎：DAG 拓扑排序 + 层级并行执行 + 安全护栏中间件。

    ADR-008: 后端核心不依赖 PyQt6。信号机制替换为回调列表，调用方通过
    on_* 方法注册回调，跨线程安全由调用方保证。
    """

    _MAX_WORKERS = 5
    _CONFIRM_TIMEOUT = 300.0
    _MAX_DISPATCH_DEPTH = 50
    _SAFE_SERIALIZE_MAX_CHARS = 2000

    def __init__(self, tool_registry, ctx, middlewares=None, *, checkpoint_manager=None):
        self._registry = tool_registry
        self._ctx = ctx
        self._cancelled = threading.Event()
        # M1: 实例化 RetryHandler 并注入 LLM 客户端以启用 Reflexion 自纠错
        try:
            from transbridge.config.llm import LLMConfig
            from transbridge.infra.llm_client import create_llm_client
            from transbridge.smart_assistant.reflexion.retry_handler import RetryHandler

            llm_cfg = LLMConfig.load_from_file()
            self._retry_handler = RetryHandler(llm_client=create_llm_client(llm_cfg))
        except ImportError:
            self._retry_handler = None
        # B3 FIX: 优先使用传入的 middlewares，无传参时 fallback 到默认链
        if middlewares:
            self._guards = list(middlewares)
        else:
            from transbridge.smart_assistant.tools.base import _build_guard_chain

            self._guards = _build_guard_chain() or []
        self._pending_decisions: dict[str, str] = {}
        # m9: Condition 替代忙等轮询
        self._decision_cv = threading.Condition()
        # M8: _paused 改为实例级属性，不同会话独立暂停
        self._paused = threading.Event()
        self._pause_cv = threading.Condition()
        # m11: 复用 ThreadPoolExecutor
        self._executor = ThreadPoolExecutor(max_workers=self._MAX_WORKERS)
        # ADR-008: 回调列表替代 pyqtSignal
        self._on_step_started: list[Callable] = []
        self._on_step_finished: list[Callable] = []
        self._on_all_finished: list[Callable] = []
        self._on_progress: list[Callable] = []
        self._on_step_retrying: list[Callable] = []
        self._on_step_requires_confirmation: list[Callable] = []

        # Story 01: 组合 ConditionEvaluator 和 CheckpointManager
        self._condition_evaluator = ConditionEvaluator()
        self._checkpoint_manager = checkpoint_manager or CheckpointManager(self._default_checkpoint_dir())
        self._branch_decisions: dict[str, str] = {}
        self._loop_counters: dict[str, int] = {}
        self._hitl_results: dict[str, str] = {}
        self._active_checkpoint_run_id: str | None = None
        self._active_checkpoint_identity: tuple[str, OwnerRef, str, str] | None = None
        self._active_checkpoint_ready: tuple[str, ...] = ()
        self._active_checkpoint_running: tuple[str, ...] = ()
        self._active_checkpoint_completed: set[str] | None = None
        self._active_checkpoint_results: dict[str, StepResult] | None = None
        self._checkpoint_revision = 0
        self._checkpoint_write_lock = threading.RLock()

    def cancel(self) -> None:
        self._cancelled.set()
        with self._pause_cv:
            self._pause_cv.notify_all()

    def shutdown(self):
        """关闭线程池，释放线程资源。"""
        if hasattr(self, "_executor") and self._executor:
            try:
                self._executor.shutdown(wait=True, cancel_futures=True)
            except RuntimeError:
                # Already shut down, ignore
                pass

    def _default_checkpoint_dir(self):
        from pathlib import Path

        project_dir = getattr(self._ctx, "project_path", None) or Path(ParatranzConfig.get_data_dir())
        return Path(project_dir) / "checkpoints"

    # ── 回调注册 (ADR-008) ────────────────────────────────────

    def on_step_started(self, callback: Callable) -> None:
        """注册步骤开始回调。callback(step_id: int, tool_name: str)。"""
        self._on_step_started.append(callback)

    def on_step_finished(self, callback: Callable) -> None:
        """注册步骤完成回调。callback(result: StepResult)。"""
        self._on_step_finished.append(callback)

    def on_all_finished(self, callback: Callable) -> None:
        """注册全部完成回调。callback(results: list[StepResult])。"""
        self._on_all_finished.append(callback)

    def on_progress(self, callback: Callable) -> None:
        """注册进度回调。callback(completed: int, total: int)。"""
        self._on_progress.append(callback)

    def on_step_retrying(self, callback: Callable) -> None:
        """注册重试回调。callback(step_id: int, attempt: int)。"""
        self._on_step_retrying.append(callback)

    def on_step_requires_confirmation(self, callback: Callable) -> None:
        """注册确认请求回调。callback(node_id: str, prompt: str, choices: list)。"""
        self._on_step_requires_confirmation.append(callback)

    @staticmethod
    def _emit(callbacks: list[Callable], *args) -> None:
        """安全触发回调列表。单个回调异常不影响其余回调。"""
        for cb in callbacks:
            try:
                cb(*args)
            except Exception:
                pass

    # ── 决策注入 ──────────────────────────────────────────────

    def provide_decision(self, node_id: str, choice: str) -> None:
        self._pending_decisions[node_id] = choice
        with self._decision_cv:
            self._decision_cv.notify_all()

    def _wait_if_paused(self) -> bool:
        with self._pause_cv:
            while self._paused.is_set() and not self._cancelled.is_set():
                self._pause_cv.wait(timeout=0.5)
        return not self._cancelled.is_set()

    def _inject_checkpoint_fault(self, stage: str) -> None:
        if self._active_checkpoint_run_id is not None:
            if stage in {
                "node_completed",
                "branch_decision",
                "loop_iteration",
                "loop_iteration_completed",
                "hitl_result",
            }:
                self._persist_active_graph_state()
            self._checkpoint_manager.inject(stage, self._active_checkpoint_run_id)

    def _persist_active_graph_state(self) -> None:
        identity = self._active_checkpoint_identity
        completed = self._active_checkpoint_completed
        results = self._active_checkpoint_results
        if identity is None or completed is None or results is None:
            return
        with self._checkpoint_write_lock:
            self._checkpoint_revision = self._save_graph_checkpoint(
                *identity,
                self._checkpoint_revision,
                ready=self._active_checkpoint_ready,
                running=self._active_checkpoint_running,
                completed=completed,
                results=results,
            )

    # ── 确认等待 ──────────────────────────────────────────────

    def _await_decision(self, node_id: str, default: str = "跳过") -> str | None:
        """等待用户确认决策。返回决策值，超时/取消时返回 None。"""
        waited = 0.0
        while node_id not in self._pending_decisions and waited < self._CONFIRM_TIMEOUT:
            if self._cancelled.is_set():
                return None
            with self._decision_cv:
                self._decision_cv.wait(timeout=0.5)
            waited += 0.5
        return self._pending_decisions.pop(node_id, default)

    # ── 护栏链 ────────────────────────────────────────────────

    def _run_guard_chain(
        self, step: dict, exec_ctx, step_id: int, tool_name: str, start_time: float, agent_instance_id: str
    ) -> tuple[StepResult | None, dict]:
        """执行 before 护栏中间件链。

        Returns:
            (early_result, current_step): early_result 非 None 表示被护栏阻断，
            调用方应直接返回该 StepResult；current_step 为可能被修改后的 step 副本。
        """
        current_step = dict(step)
        for mw in self._guards:
            guard_result = mw.before_execute(step, exec_ctx)
            if not guard_result.allowed:
                if guard_result.requires_confirmation:
                    perm_label = "管理级" if guard_result.requires_confirmation == "admin" else "写入级"
                    node_id = f"step_{step_id}"
                    self._emit(
                        self._on_step_requires_confirmation,
                        node_id,
                        f"工具 '{tool_name}' 需要{perm_label}权限确认。是否继续？",
                        ["继续", "跳过"],
                    )
                    decision = self._await_decision(node_id, default="跳过")
                    if decision is None:
                        return (
                            StepResult(
                                step_id=step_id,
                                tool=tool_name,
                                success=False,
                                message="已取消",
                                duration_ms=int((time.monotonic() - start_time) * 1000),
                                agent_instance_id=agent_instance_id,
                            ),
                            current_step,
                        )
                    if decision != "继续":
                        return (
                            StepResult(
                                step_id=step_id,
                                tool=tool_name,
                                success=False,
                                message=f"用户拒绝{perm_label}操作: {tool_name}",
                                duration_ms=int((time.monotonic() - start_time) * 1000),
                                agent_instance_id=agent_instance_id,
                            ),
                            current_step,
                        )
                else:
                    return (
                        StepResult(
                            step_id=step_id,
                            tool=tool_name,
                            success=False,
                            message=f"护栏拒绝: {guard_result.reason}",
                            duration_ms=int((time.monotonic() - start_time) * 1000),
                            agent_instance_id=agent_instance_id,
                        ),
                        current_step,
                    )
            if guard_result.modified_args is not None:
                current_step["args"] = guard_result.modified_args
        return (None, current_step)

    # ── 单步执行 ──────────────────────────────────────────────

    def _run_single(self, step: dict) -> StepResult:
        """执行单个步骤。支持 Agent namespace 路由。"""
        step_id = step["id"]
        tool_name = step.get("tool", "?")
        # C8: 虚拟起始节点 — 不触发回调也不查工具注册表
        if tool_name == "__builtin__noop":
            return StepResult(
                step_id=step_id,
                tool="__builtin__noop",
                success=True,
                message="",
            )
        args = step.get("args", {})
        agent_instance = step.get("_instance")
        agent_instance_id = step.get("agent_instance_id", "")
        namespace = None
        if agent_instance is not None and hasattr(agent_instance, "agent_spec"):
            namespace = agent_instance.agent_spec.namespace

        self._emit(self._on_step_started, step_id, tool_name)
        start = time.monotonic()

        spec = self._registry.get(tool_name, namespace=namespace)
        if spec is None:
            return StepResult(
                step_id=step_id,
                tool=tool_name,
                success=False,
                message=f"未知工具: {tool_name}",
                duration_ms=int((time.monotonic() - start) * 1000),
                agent_instance_id=agent_instance_id,
            )

        # Before 中间件链
        from transbridge.smart_assistant.tools.base import ExecutionContext
        from transbridge.smart_assistant.tools.task_manager import TaskManager

        raw_ctx = agent_instance.ctx if agent_instance is not None else self._ctx
        exec_ctx = ExecutionContext(app_context=raw_ctx, task_manager=TaskManager())

        early_result, current_step = self._run_guard_chain(step, exec_ctx, step_id, tool_name, start, agent_instance_id)
        if early_result is not None:
            return early_result

        # 工具执行（带 Reflexion 重试）
        raw_result, exec_error = self._execute_tool_with_retry(
            current_step, spec, exec_ctx, start, tool_name, agent_instance_id, args
        )

        if exec_error is not None:
            return exec_error

        final_result = StepResult(
            step_id=step_id,
            tool=tool_name,
            success=raw_result.get("success", True),
            message=raw_result.get("message", ""),
            data=raw_result.get("data"),
            duration_ms=int((time.monotonic() - start) * 1000),
            agent_instance_id=agent_instance_id,
        )

        # After 中间件链（逆序）
        for mw in reversed(self._guards):
            guard_result = mw.after_execute(step, final_result, exec_ctx)
            if not guard_result.allowed:
                final_result.success = False
                final_result.message = f"输出校验拒绝: {guard_result.reason}"
                return final_result
            if guard_result.modified_result is not None:
                final_result.data = guard_result.modified_result

        return final_result

    def _execute_tool_with_retry(
        self, current_step: dict, spec, exec_ctx, start: float, tool_name: str, agent_instance_id: str, args: dict
    ):
        """执行工具调用并处理 Reflexion 重试循环。

        Returns:
            (raw_result, error_result): 成功时 error_result 为 None，
            失败时 raw_result 为 None。
        """
        step_id = current_step["id"]
        attempt = 0
        while True:
            if self._cancelled.is_set():
                return None, StepResult(
                    step_id=step_id,
                    tool=tool_name,
                    success=False,
                    message="已取消",
                    duration_ms=int((time.monotonic() - start) * 1000),
                    agent_instance_id=agent_instance_id,
                )
            try:
                raw_result = spec.execute(current_step.get("args", args), exec_ctx)
                return raw_result, None
            except Exception as exc:
                if (
                    self._retry_handler is None
                    or not self._retry_handler.should_retry(str(exc))
                    or attempt >= self._retry_handler.MAX_RETRIES
                ):
                    return None, StepResult(
                        step_id=step_id,
                        tool=tool_name,
                        success=False,
                        message=f"执行异常: {exc}",
                        duration_ms=int((time.monotonic() - start) * 1000),
                        agent_instance_id=agent_instance_id,
                    )
                adjusted = self._retry_handler.analyze_and_adjust(current_step, str(exc), attempt)
                if adjusted is None:
                    return None, StepResult(
                        step_id=step_id,
                        tool=tool_name,
                        success=False,
                        message=f"执行异常: {exc}",
                        duration_ms=int((time.monotonic() - start) * 1000),
                        agent_instance_id=agent_instance_id,
                    )
                current_step = adjusted
                attempt += 1
                self._emit(self._on_step_retrying, step_id, attempt)

    # ── Graph 编排扩展 (S09/S10) ──────────────────────────────

    def _dispatch_node(self, node, node_map: dict, results: dict, _visited: set | None = None, _depth: int = 0):
        """调度单个图节点：根据节点类型执行相应逻辑。

        C9: _visited 防止环路导致的无限递归，_depth 作为硬上限兜底。
        """
        from .graph_types import ActionNode, ConditionNode, HumanConfirmNode, LoopNode

        if _visited is None:
            _visited = set()

        # C9: 递归深度硬上限
        if _depth > self._MAX_DISPATCH_DEPTH:
            logger.warning(
                "调度深度超限 (%d)，节点 %s 终止递归", self._MAX_DISPATCH_DEPTH, getattr(node, "node_id", "?")
            )
            return None

        # C9: 环路检测 — 已访问节点跳过
        if node.node_id in _visited:
            logger.warning("检测到调度环路，节点 %s 已存在于调度链中，跳过", node.node_id)
            return None

        _visited.add(node.node_id)

        if self._cancelled.is_set():
            return None
        if isinstance(node, ActionNode):
            step = {
                "id": self._stable_step_id(node.node_id),
                "tool": node.tool,
                "args": node.args,
                "retry": node.retry,
                "agent": node.agent,
                "agent_instance_id": "",
                "_instance": None,
            }
            return self._run_single(step)
        elif isinstance(node, ConditionNode):
            cond_results = {nid: results.get(nid) for nid in results}
            next_id = self._branch_decisions.get(node.node_id)
            if next_id is None:
                cond = self._condition_evaluator.eval_condition(node.condition, cond_results)
                next_id = node.true_node if cond else node.false_node
                self._branch_decisions[node.node_id] = next_id
                self._inject_checkpoint_fault("branch_decision")
            if next_id and next_id in node_map:
                return self._dispatch_node(node_map[next_id], node_map, results, _visited, _depth + 1)
            return None
        elif isinstance(node, LoopNode):
            last_result = None
            start_iteration = self._loop_counters.get(node.node_id, 0)
            for i in range(start_iteration, node.max_iterations):
                if self._cancelled.is_set():
                    break
                if not self._wait_if_paused():
                    break
                self._loop_counters[node.node_id] = i
                self._inject_checkpoint_fault("loop_iteration")
                # C9: 每轮迭代使用 _visited 副本，允许跨迭代重入但阻断单次内环路
                iter_visited = set(_visited)
                for sub_node in node.sub_nodes:
                    r = self._dispatch_node(sub_node, node_map, results, iter_visited, _depth + 1)
                    if r is not None:
                        results[sub_node.node_id] = r
                        last_result = r
                self._loop_counters[node.node_id] = i + 1
                self._inject_checkpoint_fault("loop_iteration_completed")
                if last_result and self._condition_evaluator.eval_condition(
                    node.exit_condition, {nid: results.get(nid) for nid in results}
                ):
                    break
            return last_result
        elif isinstance(node, HumanConfirmNode):
            decision = self._hitl_results.get(node.node_id)
            if decision is None:
                self._emit(self._on_step_requires_confirmation, node.node_id, node.prompt, node.choices)
                decision = self._await_decision(node.node_id, default=node.default_choice)
            if decision is None:
                return None
            self._hitl_results[node.node_id] = decision
            self._inject_checkpoint_fault("hitl_result")
            return StepResult(
                step_id=self._stable_step_id(node.node_id),
                tool="human_confirm",
                success=decision != "终止",
                message=f"用户选择: {decision}",
                data={"decision": decision},
            )
        return None

    @staticmethod
    def _stable_step_id(node_id: str) -> int:
        return int.from_bytes(hashlib.sha256(node_id.encode("utf-8")).digest()[:4], "big") % 1_000_000

    @staticmethod
    def _stable_graph_id(prefix: str, steps: list[dict]) -> str:
        payload = CheckpointManager._safe_serialize(steps)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return f"{prefix}_{hashlib.sha256(encoded).hexdigest()[:24]}"

    # ── BFS 层级迭代 ──────────────────────────────────────────

    def _bfs_one_level(
        self, pending: list, node_map: dict, visited: set, graph, results: dict, completed: set, total: int
    ) -> list:
        """执行一层 BFS：并行执行当前层所有节点，返回下一层待处理节点 ID 列表。"""
        # 构建当前层节点列表
        level_nodes = []
        for nid in pending:
            if nid not in visited and nid in node_map:
                level_nodes.append(node_map[nid])
                visited.add(nid)

        if not level_nodes:
            return []

        # 并行执行当前层
        futures = {self._executor.submit(self._dispatch_node, node, node_map, results): node for node in level_nodes}
        for future in as_completed(futures):
            if self._cancelled.is_set():
                break
            node = futures[future]
            try:
                r = future.result()
            except GraphCheckpointFault:
                raise
            except Exception as exc:
                r = StepResult(
                    step_id=self._stable_step_id(node.node_id),
                    tool=getattr(node, "tool", "?"),
                    success=False,
                    message=f"异常: {exc}",
                )
            if r is not None:
                results[node.node_id] = r
                completed.add(node.node_id)
                self._inject_checkpoint_fault("node_completed")
                self._emit(self._on_step_finished, r)
                self._emit(self._on_progress, len(completed), total)

        # 找下一层节点
        next_pending = []
        for edge in graph.edges:
            if edge.from_node in visited and edge.to_node not in visited:
                if edge.edge_type != "loop_back":
                    next_pending.append(edge.to_node)

        return list(set(next_pending))

    # ── Graph 执行入口 ────────────────────────────────────────

    def execute_graph(
        self,
        graph,
        *,
        checkpoint_identity: CheckpointExpectation | None = None,
    ) -> list[StepResult]:
        """执行有状态图：BFS 遍历 + 条件路由 + 循环 + HITL + checkpoint。"""
        self._cancelled.clear()

        node_map = {n.node_id: n for n in graph.nodes}
        run_id, owner, spec_fingerprint, input_fingerprint = self._graph_checkpoint_identity(
            graph,
            checkpoint_identity,
        )
        expected = CheckpointExpectation(run_id, owner, spec_fingerprint, input_fingerprint)
        checkpoint = self._checkpoint_manager.load_record(run_id, expected=expected)
        self._active_checkpoint_run_id = run_id
        self._active_checkpoint_identity = (run_id, owner, spec_fingerprint, input_fingerprint)
        if checkpoint is None:
            results: dict[str, StepResult] = {}
            completed: set[str] = set()
            pending = [graph.entry_node] if graph.entry_node else []
            visited: set[str] = set()
            revision = 0
            self._branch_decisions = {}
            self._loop_counters = {}
            self._hitl_results = {}
        else:
            results = {
                node_id: self._deserialize_step_result(json.loads(value)) for node_id, value in checkpoint.graph_results
            }
            completed = set(checkpoint.frontier.completed)
            pending = list(dict.fromkeys((*checkpoint.frontier.running, *checkpoint.frontier.ready)))
            visited = set(completed)
            if not pending and completed != set(node_map):
                pending = list(
                    dict.fromkeys(
                        edge.to_node
                        for edge in graph.edges
                        if edge.from_node in completed and edge.to_node not in completed
                    )
                )
                if not pending and graph.entry_node not in completed:
                    pending = [graph.entry_node]
            revision = checkpoint.revision
            self._branch_decisions = dict(checkpoint.branch_decisions)
            self._loop_counters = dict(checkpoint.loop_counters)
            self._hitl_results = dict(checkpoint.hitl_results)
        total = len(graph.nodes)
        self._checkpoint_revision = revision
        self._active_checkpoint_completed = completed
        self._active_checkpoint_results = results

        while pending:
            if not self._wait_if_paused():
                break

            running = tuple(node_id for node_id in dict.fromkeys(pending) if node_id not in visited)
            self._active_checkpoint_ready = ()
            self._active_checkpoint_running = running
            with self._checkpoint_write_lock:
                revision = self._save_graph_checkpoint(
                    run_id,
                    owner,
                    spec_fingerprint,
                    input_fingerprint,
                    max(revision, self._checkpoint_revision),
                    ready=(),
                    running=running,
                    completed=completed,
                    results=results,
                )
                self._checkpoint_revision = revision

            pending = self._bfs_one_level(pending, node_map, visited, graph, results, completed, total)

            self._active_checkpoint_ready = tuple(dict.fromkeys(pending))
            self._active_checkpoint_running = ()
            with self._checkpoint_write_lock:
                revision = self._save_graph_checkpoint(
                    run_id,
                    owner,
                    spec_fingerprint,
                    input_fingerprint,
                    max(revision, self._checkpoint_revision),
                    ready=self._active_checkpoint_ready,
                    running=(),
                    completed=completed,
                    results=results,
                )
                self._checkpoint_revision = revision

        final = [results.get(n.node_id) for n in graph.nodes if n.node_id in results]
        self._emit(self._on_all_finished, final)
        return [r for r in final if r is not None]

    def _graph_checkpoint_identity(
        self,
        graph,
        explicit: CheckpointExpectation | None,
    ) -> tuple[str, OwnerRef, str, str]:
        spec_fingerprint = self.graph_spec_fingerprint(graph)

        if explicit is not None:
            if explicit.spec_fingerprint != spec_fingerprint:
                from transbridge.application.tasks import CheckpointMismatchError

                raise CheckpointMismatchError(
                    "checkpoint_graph_spec_mismatch",
                    "explicit checkpoint identity does not match graph definition",
                )
            return explicit.run_id, explicit.owner, spec_fingerprint, explicit.input_fingerprint

        run_id = self._non_empty_context_value("run_id")
        owner_id = self._non_empty_context_value("owner_id")
        entrypoint = self._non_empty_context_value("entrypoint")
        input_fingerprint = self._non_empty_context_value("input_fingerprint")
        if all((run_id, owner_id, entrypoint, input_fingerprint)):
            owner = OwnerRef(
                owner_id=owner_id,
                entrypoint=entrypoint,
                project_id=self._optional_context_value("project_id"),
                variant_id=self._optional_context_value("variant_id"),
                session_id=self._optional_context_value("session_id"),
            )
            return run_id, owner, spec_fingerprint, input_fingerprint

        # Legacy callers have no authority to resume another execution. Give each
        # invocation an isolated identity instead of keying durable state by graph_id.
        isolated = uuid4().hex
        owner = OwnerRef(f"legacy-graph-{isolated}", "graph")
        return f"legacy-graph-run-{isolated}", owner, spec_fingerprint, spec_fingerprint

    @staticmethod
    def graph_spec_fingerprint(graph) -> str:
        payload = {
            "graph_id": graph.graph_id,
            "entry_node": graph.entry_node,
            "nodes": [CheckpointManager._safe_serialize(vars(node)) for node in graph.nodes],
            "edges": [CheckpointManager._safe_serialize(vars(edge)) for edge in graph.edges],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"

    def _non_empty_context_value(self, name: str) -> str | None:
        value = getattr(self._ctx, name, None)
        if not isinstance(value, str) or not value.strip():
            return None
        return value

    def _optional_context_value(self, name: str) -> str | None:
        value = getattr(self._ctx, name, None)
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            return None
        return value

    def _save_graph_checkpoint(
        self,
        run_id: str,
        owner: OwnerRef,
        spec_fingerprint: str,
        input_fingerprint: str,
        revision: int,
        *,
        ready: tuple[str, ...],
        running: tuple[str, ...],
        completed: set[str],
        results: dict[str, StepResult],
    ) -> int:
        next_revision = revision + 1
        completed_ids = set(completed)
        normalized_ready = tuple(node_id for node_id in dict.fromkeys(ready) if node_id not in completed_ids)
        ready_ids = set(normalized_ready)
        normalized_running = tuple(
            node_id for node_id in dict.fromkeys(running) if node_id not in completed_ids and node_id not in ready_ids
        )
        record = CheckpointRecord(
            run_id=run_id,
            owner=owner,
            spec_fingerprint=spec_fingerprint,
            input_fingerprint=input_fingerprint,
            revision=next_revision,
            frontier=CheckpointFrontier(
                ready=normalized_ready,
                running=normalized_running,
                completed=tuple(sorted(completed)),
            ),
            completed_commit_ids=frozenset(completed),
            branch_decisions=tuple(sorted(self._branch_decisions.items())),
            loop_counters=tuple(sorted(self._loop_counters.items())),
            hitl_results=tuple(sorted(self._hitl_results.items())),
            graph_results=tuple(
                sorted(
                    (node_id, CheckpointManager.serialize_step_result(result)) for node_id, result in results.items()
                )
            ),
        )
        self._checkpoint_manager.save_record(record)
        return next_revision

    @staticmethod
    def _deserialize_step_result(value: dict) -> StepResult:
        return StepResult(
            step_id=int(value["step_id"]),
            tool=str(value["tool"]),
            success=bool(value["success"]),
            message=str(value["message"]),
            data=value.get("data"),
            duration_ms=int(value.get("duration_ms", 0)),
            agent_instance_id=str(value.get("agent_instance_id", "")),
        )

    def execute(self, steps: list[dict]) -> list[StepResult]:
        """执行步骤列表：基于 depends_on 的 DAG 拓扑并行 + 线性兜底。

        C8 FIX: 不再忽略 step["depends_on"]。当任意步骤声明了 depends_on 时，
        通过 Kahn 拓扑排序构建层级 DAG，同级步骤并行执行（BFS）。
        无 depends_on 时回退到原有线性行为，保持向后兼容。
        """
        from .graph_types import ActionNode, EdgeSpec, GraphSpec

        if not steps:
            return []

        # 检查是否有任何步骤声明了 depends_on
        has_deps = any(s.get("depends_on") for s in steps)

        # ── 线性兜底：无依赖声明时保持原有行为 ──
        if not has_deps:
            nodes = []
            edges = []
            for i, s in enumerate(steps):
                nid = f"step_{s['id']}"
                nodes.append(
                    ActionNode(
                        node_id=nid,
                        node_type="action",
                        tool=s.get("tool", "?"),
                        args=s.get("args", {}),
                        agent=s.get("agent"),
                        retry=s.get("retry", True),
                    )
                )
                if i > 0:
                    edges.append(EdgeSpec(from_node=f"step_{steps[i - 1]['id']}", to_node=nid))
            graph = GraphSpec(
                graph_id=self._stable_graph_id("linear", steps),
                nodes=nodes,
                edges=edges,
                entry_node=nodes[0].node_id if nodes else "",
            )
            return self.execute_graph(graph)

        # ── DAG 拓扑模式：基于 depends_on 构建并行执行图 ──

        # 1. 构建节点 (step id → node_id: f"step_{id}")
        node_ids: set[str] = set()
        nodes = []
        for s in steps:
            nid = f"step_{s['id']}"
            node_ids.add(nid)
            nodes.append(
                ActionNode(
                    node_id=nid,
                    node_type="action",
                    tool=s.get("tool", "?"),
                    args=s.get("args", {}),
                    agent=s.get("agent"),
                    retry=s.get("retry", True),
                )
            )

        # 2. 解析 depends_on 并构建边 + 入度表
        adjacency: dict[str, list[str]] = {nid: [] for nid in node_ids}
        in_degree: dict[str, int] = {nid: 0 for nid in node_ids}
        edges = []
        for s in steps:
            nid = f"step_{s['id']}"
            deps = s.get("depends_on", [])
            if not isinstance(deps, list):
                deps = []
            for dep_id in deps:
                dep_nid = f"step_{dep_id}"
                if dep_nid not in node_ids:
                    logger.warning("步骤 %s 依赖未知步骤 %s，忽略该依赖", s["id"], dep_id)
                    continue
                edges.append(EdgeSpec(from_node=dep_nid, to_node=nid))
                adjacency.setdefault(dep_nid, []).append(nid)
                in_degree[nid] += 1

        # 3. Kahn 拓扑排序 + 环路检测
        temp_deg = dict(in_degree)
        queue = [nid for nid, deg in temp_deg.items() if deg == 0]
        sorted_nodes: list[str] = []
        while queue:
            current = queue.pop(0)
            sorted_nodes.append(current)
            for neighbor in adjacency.get(current, []):
                temp_deg[neighbor] -= 1
                if temp_deg[neighbor] == 0:
                    queue.append(neighbor)

        if len(sorted_nodes) != len(nodes):
            remaining = [nid for nid, deg in temp_deg.items() if deg > 0]
            raise ValueError(f"检测到步骤间循环依赖，涉及节点: {remaining}")

        # 4. level-0 节点（无入边，可并行执行）
        level_0 = [nid for nid, deg in in_degree.items() if deg == 0]

        # 5. 虚拟 __start__ 节点统一入口，使 BFS 能同时发现所有 level-0 节点
        start_nid = "__start__"
        start_node = ActionNode(
            node_id=start_nid,
            node_type="action",
            tool="__builtin__noop",
            args={},
        )
        nodes.insert(0, start_node)
        for nid in level_0:
            edges.append(EdgeSpec(from_node=start_nid, to_node=nid))

        graph = GraphSpec(
            graph_id=self._stable_graph_id("dag", steps),
            nodes=nodes,
            edges=edges,
            entry_node=start_nid,
        )
        raw_results = self.execute_graph(graph)
        # 过滤掉虚拟起始节点的结果
        return [r for r in raw_results if r.tool != "__builtin__noop"]

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()
        with self._pause_cv:
            self._pause_cv.notify_all()
