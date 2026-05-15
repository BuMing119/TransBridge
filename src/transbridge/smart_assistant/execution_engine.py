from __future__ import annotations

import ast
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable

from src.transbridge.config.paratranz import ParatranzConfig

logger = logging.getLogger(__name__)


@dataclass
class StepResult:
    step_id: int
    tool: str
    success: bool
    message: str
    data: Any = None
    duration_ms: int = 0
    agent_instance_id: str = ""


class ExecutionEngine:
    """统一执行引擎：DAG 拓扑排序 + 层级并行执行 + 安全护栏中间件。

    ADR-008: 后端核心不依赖 PyQt6。信号机制替换为回调列表，调用方通过
    on_* 方法注册回调，跨线程安全由调用方保证。
    """

    _MAX_WORKERS = 5
    _CONFIRM_TIMEOUT = 300.0
    _MAX_EVAL_DEPTH = 20
    _MAX_DISPATCH_DEPTH = 50
    _SAFE_SERIALIZE_MAX_CHARS = 2000

    def __init__(self, tool_registry, ctx, middlewares=None):
        self._registry = tool_registry
        self._ctx = ctx
        self._cancelled = threading.Event()
        # M1: 实例化 RetryHandler 并注入 LLM 客户端以启用 Reflexion 自纠错
        try:
            from src.transbridge.smart_assistant.reflexion.retry_handler import RetryHandler
            from src.transbridge.infra.llm_client import create_llm_client
            from src.transbridge.config.llm import LLMConfig
            llm_cfg = LLMConfig.load_from_file()
            self._retry_handler = RetryHandler(llm_client=create_llm_client(llm_cfg))
        except ImportError:
            self._retry_handler = None
        # B3 FIX: 优先使用传入的 middlewares，无传参时 fallback 到默认链
        if middlewares:
            self._guards = list(middlewares)
        else:
            from src.transbridge.smart_assistant.tools.base import _build_guard_chain
            self._guards = _build_guard_chain() or []
        self._pending_decisions: dict[str, str] = {}
        # m9: Condition 替代忙等轮询
        self._decision_cv = threading.Condition()
        # M8: _paused 改为实例级属性，不同会话独立暂停
        self._paused = threading.Event()
        # m11: 复用 ThreadPoolExecutor
        self._executor = ThreadPoolExecutor(max_workers=self._MAX_WORKERS)
        # ADR-008: 回调列表替代 pyqtSignal
        self._on_step_started: list[Callable] = []
        self._on_step_finished: list[Callable] = []
        self._on_all_finished: list[Callable] = []
        self._on_progress: list[Callable] = []
        self._on_step_retrying: list[Callable] = []
        self._on_step_requires_confirmation: list[Callable] = []

    def cancel(self) -> None:
        self._cancelled.set()

    def shutdown(self):
        """关闭线程池，释放线程资源。"""
        if hasattr(self, '_executor') and self._executor:
            try:
                self._executor.shutdown(wait=True, cancel_futures=True)
            except RuntimeError:
                # Already shut down, ignore
                pass

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

    def _run_guard_chain(self, step: dict, exec_ctx, step_id: int,
                         tool_name: str, start_time: float,
                         agent_instance_id: str) -> tuple[StepResult | None, dict]:
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
                    self._emit(self._on_step_requires_confirmation,
                        node_id,
                        f"工具 '{tool_name}' 需要{perm_label}权限确认。是否继续？",
                        ["继续", "跳过"],
                    )
                    decision = self._await_decision(node_id, default="跳过")
                    if decision is None:
                        return (
                            StepResult(
                                step_id=step_id, tool=tool_name,
                                success=False, message="已取消",
                                duration_ms=int((time.monotonic() - start_time) * 1000),
                                agent_instance_id=agent_instance_id,
                            ),
                            current_step,
                        )
                    if decision != "继续":
                        return (
                            StepResult(
                                step_id=step_id, tool=tool_name,
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
                            step_id=step_id, tool=tool_name,
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
                step_id=step_id, tool="__builtin__noop",
                success=True, message="",
            )
        args = step.get("args", {})
        agent_instance = step.get("_instance")
        agent_instance_id = step.get("agent_instance_id", "")
        namespace = None
        if agent_instance is not None and hasattr(agent_instance, 'agent_spec'):
            namespace = agent_instance.agent_spec.namespace

        self._emit(self._on_step_started, step_id, tool_name)
        start = time.monotonic()

        spec = self._registry.get(tool_name, namespace=namespace)
        if spec is None:
            return StepResult(
                step_id=step_id, tool=tool_name,
                success=False, message=f"未知工具: {tool_name}",
                duration_ms=int((time.monotonic() - start) * 1000),
                agent_instance_id=agent_instance_id,
            )

        # Before 中间件链
        from src.transbridge.smart_assistant.tools.base import ExecutionContext
        from src.transbridge.smart_assistant.tools.task_manager import TaskManager
        raw_ctx = agent_instance.ctx if agent_instance is not None else self._ctx
        exec_ctx = ExecutionContext(app_context=raw_ctx, task_manager=TaskManager())

        early_result, current_step = self._run_guard_chain(
            step, exec_ctx, step_id, tool_name, start, agent_instance_id)
        if early_result is not None:
            return early_result

        # 工具执行（带 Reflexion 重试）
        raw_result, exec_error = self._execute_tool_with_retry(
            current_step, spec, exec_ctx, start, tool_name,
            agent_instance_id, args)

        if exec_error is not None:
            return exec_error

        final_result = StepResult(
            step_id=step_id, tool=tool_name,
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

    def _execute_tool_with_retry(self, current_step: dict, spec, exec_ctx,
                                   start: float, tool_name: str,
                                   agent_instance_id: str, args: dict):
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
                    step_id=step_id, tool=tool_name,
                    success=False, message="已取消",
                    duration_ms=int((time.monotonic() - start) * 1000),
                    agent_instance_id=agent_instance_id,
                )
            try:
                raw_result = spec.execute(current_step.get("args", args), exec_ctx)
                return raw_result, None
            except Exception as exc:
                if (self._retry_handler is None or
                        not self._retry_handler.should_retry(str(exc)) or
                        attempt >= self._retry_handler.MAX_RETRIES):
                    return None, StepResult(
                        step_id=step_id, tool=tool_name,
                        success=False, message=f"执行异常: {exc}",
                        duration_ms=int((time.monotonic() - start) * 1000),
                        agent_instance_id=agent_instance_id,
                    )
                adjusted = self._retry_handler.analyze_and_adjust(
                    current_step, str(exc), attempt)
                if adjusted is None:
                    return None, StepResult(
                        step_id=step_id, tool=tool_name,
                        success=False, message=f"执行异常: {exc}",
                        duration_ms=int((time.monotonic() - start) * 1000),
                        agent_instance_id=agent_instance_id,
                    )
                current_step = adjusted
                attempt += 1
                self._emit(self._on_step_retrying, step_id, attempt)

    # ── Graph 编排扩展 (S09/S10) ──────────────────────────────

    def _dispatch_node(self, node, node_map: dict, results: dict,
                       _visited: set | None = None, _depth: int = 0):
        """调度单个图节点：根据节点类型执行相应逻辑。

        C9: _visited 防止环路导致的无限递归，_depth 作为硬上限兜底。
        """
        from .graph_types import (ActionNode, ConditionNode, LoopNode,
                                   HumanConfirmNode)

        if _visited is None:
            _visited = set()

        # C9: 递归深度硬上限
        if _depth > self._MAX_DISPATCH_DEPTH:
            logger.warning("调度深度超限 (%d)，节点 %s 终止递归",
                           self._MAX_DISPATCH_DEPTH, getattr(node, 'node_id', '?'))
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
                "id": hash(node.node_id) % 1000000,
                "tool": node.tool, "args": node.args,
                "retry": node.retry, "agent": node.agent,
                "agent_instance_id": "", "_instance": None,
            }
            return self._run_single(step)
        elif isinstance(node, ConditionNode):
            cond_results = {nid: results.get(nid) for nid in results}
            cond = self._eval_condition(node.condition, cond_results)
            next_id = node.true_node if cond else node.false_node
            if next_id and next_id in node_map:
                return self._dispatch_node(node_map[next_id], node_map, results,
                                          _visited, _depth + 1)
            return None
        elif isinstance(node, LoopNode):
            last_result = None
            for i in range(node.max_iterations):
                if self._cancelled.is_set():
                    break
                # C9: 每轮迭代使用 _visited 副本，允许跨迭代重入但阻断单次内环路
                iter_visited = set(_visited)
                for sub_node in node.sub_nodes:
                    r = self._dispatch_node(sub_node, node_map, results,
                                            iter_visited, _depth + 1)
                    if r is not None:
                        results[sub_node.node_id] = r
                        last_result = r
                if last_result and self._eval_condition(
                        node.exit_condition,
                        {nid: results.get(nid) for nid in results}):
                    break
            return last_result
        elif isinstance(node, HumanConfirmNode):
            self._emit(self._on_step_requires_confirmation,
                node.node_id, node.prompt, node.choices)
            decision = self._await_decision(
                node.node_id, default=node.default_choice)
            if decision is None:
                return None
            return StepResult(
                step_id=hash(node.node_id) % 1000000,
                tool="human_confirm",
                success=decision != "终止",
                message=f"用户选择: {decision}",
                data={"decision": decision},
            )
        return None

    # ── BFS 层级迭代 ──────────────────────────────────────────

    def _bfs_one_level(self, pending: list, node_map: dict, visited: set,
                       graph, results: dict, completed: set,
                       total: int) -> list:
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
        futures = {
            self._executor.submit(self._dispatch_node, node, node_map, results): node
            for node in level_nodes
        }
        for future in as_completed(futures):
            if self._cancelled.is_set():
                break
            node = futures[future]
            try:
                r = future.result()
            except Exception as exc:
                r = StepResult(
                    step_id=hash(node.node_id) % 1000000,
                    tool=getattr(node, 'tool', '?'),
                    success=False, message=f"异常: {exc}",
                )
            if r is not None:
                results[node.node_id] = r
                completed.add(node.node_id)
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

    def execute_graph(self, graph) -> list[StepResult]:
        """执行有状态图：BFS 遍历 + 条件路由 + 循环 + HITL + checkpoint。"""
        self._cancelled.clear()
        self._paused.clear()

        from .graph_types import (ActionNode, ConditionNode, LoopNode,
                                   HumanConfirmNode, Checkpoint)

        node_map = {n.node_id: n for n in graph.nodes}
        results: dict[str, StepResult] = {}
        ckpt = self._load_checkpoint(graph.graph_id)
        completed = set(ckpt.completed_results.keys()) if ckpt else set()
        total = len(graph.nodes)

        pending = [graph.entry_node] if graph.entry_node else []
        visited: set = set()

        while pending:
            # 暂停检查
            if self._paused.is_set():
                self._paused.wait()
            if self._cancelled.is_set():
                break

            pending = self._bfs_one_level(
                pending, node_map, visited, graph, results, completed, total)

            # 自动保存 checkpoint
            try:
                self._save_checkpoint(graph.graph_id, graph.entry_node, results)
            except Exception:
                logger.warning(
                    "Checkpoint 自动保存失败 (graph_id=%s)", graph.graph_id)

        final = [results.get(n.node_id) for n in graph.nodes if n.node_id in results]
        self._emit(self._on_all_finished, final)
        return [r for r in final if r is not None]

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
                nodes.append(ActionNode(
                    node_id=nid, node_type="action",
                    tool=s.get("tool", "?"), args=s.get("args", {}),
                    agent=s.get("agent"), retry=s.get("retry", True),
                ))
                if i > 0:
                    edges.append(EdgeSpec(
                        from_node=f"step_{steps[i-1]['id']}", to_node=nid))
            graph = GraphSpec(
                graph_id=f"linear_{abs(hash(str(steps)))}",
                nodes=nodes, edges=edges,
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
            nodes.append(ActionNode(
                node_id=nid, node_type="action",
                tool=s.get("tool", "?"), args=s.get("args", {}),
                agent=s.get("agent"), retry=s.get("retry", True),
            ))

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
                    logger.warning(
                        "步骤 %s 依赖未知步骤 %s，忽略该依赖", s["id"], dep_id)
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
            raise ValueError(
                f"检测到步骤间循环依赖，涉及节点: {remaining}")

        # 4. level-0 节点（无入边，可并行执行）
        level_0 = [nid for nid, deg in in_degree.items() if deg == 0]

        # 5. 虚拟 __start__ 节点统一入口，使 BFS 能同时发现所有 level-0 节点
        start_nid = "__start__"
        start_node = ActionNode(
            node_id=start_nid, node_type="action",
            tool="__builtin__noop", args={},
        )
        nodes.insert(0, start_node)
        for nid in level_0:
            edges.append(EdgeSpec(from_node=start_nid, to_node=nid))

        graph = GraphSpec(
            graph_id=f"dag_{abs(hash(str(steps)))}",
            nodes=nodes, edges=edges,
            entry_node=start_nid,
        )
        raw_results = self.execute_graph(graph)
        # 过滤掉虚拟起始节点的结果
        return [r for r in raw_results if r.tool != "__builtin__noop"]

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    # ── AST 条件求值 ──────────────────────────────────────────

    def _eval_condition(self, condition: str, results: dict) -> bool:
        """AST 安全条件求值。仅允许 result.success / result.data['key'] 等白名单节点。"""
        if not condition.strip():
            return False
        last_result = None
        for r in results.values():
            if r is not None:
                last_result = r
        try:
            tree = ast.parse(str(condition), mode='eval')
            return bool(self._eval_ast_node(tree.body, last_result))
        except Exception:
            return False

    # AST 节点类型 → 求值处理器名称
    _AST_DISPATCH = {
        ast.Constant: '_eval_ast_constant',
        ast.Name: '_eval_ast_name',
        ast.Attribute: '_eval_ast_attribute',
        ast.Subscript: '_eval_ast_subscript',
        ast.Compare: '_eval_ast_compare',
        ast.BoolOp: '_eval_ast_boolop',
        ast.UnaryOp: '_eval_ast_unaryop',
        ast.Call: '_eval_ast_call',
    }

    def _eval_ast_node(self, node, result, depth: int = 0) -> object:
        """递归求值 AST 节点。depth 用于防止恶意嵌套导致栈溢出。"""
        if depth > self._MAX_EVAL_DEPTH:
            raise ValueError(f"AST 求值深度超限: {self._MAX_EVAL_DEPTH}")
        allowed_types = (ast.Constant, ast.Name, ast.Attribute, ast.Subscript,
                         ast.Compare, ast.BoolOp, ast.UnaryOp,
                         ast.Load, ast.Index, ast.Tuple,
                         ast.Call, ast.keyword)
        if not isinstance(node, allowed_types):
            raise ValueError(f"不允许的 AST 节点: {type(node).__name__}")
        handler_name = self._AST_DISPATCH.get(type(node))
        if handler_name is None:
            raise ValueError(f"不支持的 AST 节点: {type(node).__name__}")
        return getattr(self, handler_name)(node, result, depth)

    def _eval_ast_constant(self, node, _result, _depth: int) -> object:
        return node.value

    def _eval_ast_name(self, node, result, _depth: int) -> object:
        if node.id == "result":
            return result
        if node.id in ("True", "False"):
            return node.id == "True"
        if node.id == "None":
            return None
        raise ValueError(f"未知变量: {node.id}")

    def _eval_ast_attribute(self, node, result, depth: int) -> object:
        obj = self._eval_ast_node(node.value, result, depth + 1)
        if obj is None:
            return None
        return getattr(obj, node.attr, None)

    def _eval_ast_subscript(self, node, result, depth: int) -> object:
        obj = self._eval_ast_node(node.value, result, depth + 1)
        if isinstance(node.slice, ast.Constant):
            key = node.slice.value
        elif isinstance(node.slice, ast.Index):
            key = self._eval_ast_node(node.slice.value, result, depth + 1)
        else:
            key = None
        if isinstance(obj, dict) and key is not None:
            return obj.get(key)
        return None

    def _eval_ast_compare(self, node, result, depth: int) -> object:
        """C15: 链式比较 a < b < c 在 AST 中是单个 Compare 节点，
        ops=[Lt, Lt], comparators=[b, c]。逐对求值并 AND 短路。"""
        current = self._eval_ast_node(node.left, result, depth + 1)
        for op, comparator in zip(node.ops, node.comparators):
            right = self._eval_ast_node(comparator, result, depth + 1)
            if not self._eval_compare_op(op, current, right):
                return False
            current = right
        return True

    @staticmethod
    def _eval_compare_op(op, left, right) -> bool:
        """求值单个比较操作符。"""
        if isinstance(op, ast.Eq):
            return left == right
        if isinstance(op, ast.NotEq):
            return left != right
        if isinstance(op, ast.Lt):
            return left is not None and right is not None and left < right
        if isinstance(op, ast.LtE):
            return left is not None and right is not None and left <= right
        if isinstance(op, ast.Gt):
            return left is not None and right is not None and left > right
        if isinstance(op, ast.GtE):
            return left is not None and right is not None and left >= right
        if isinstance(op, ast.In):
            return (left in right) if right is not None else False
        if isinstance(op, ast.NotIn):
            return (left not in right) if right is not None else True
        return False

    def _eval_ast_boolop(self, node, result, depth: int) -> object:
        if isinstance(node.op, ast.And):
            for v in node.values:
                if not self._eval_ast_node(v, result, depth + 1):
                    return False
            return True
        if isinstance(node.op, ast.Or):
            for v in node.values:
                if self._eval_ast_node(v, result, depth + 1):
                    return True
            return False
        raise ValueError(f"不支持的布尔操作符: {type(node.op).__name__}")

    def _eval_ast_unaryop(self, node, result, depth: int) -> object:
        operand = self._eval_ast_node(node.operand, result, depth + 1)
        if isinstance(node.op, ast.Not):
            return not operand
        raise ValueError(f"不支持的一元操作符: {type(node.op).__name__}")

    # M4: 预定义安全类型白名单，供 isinstance 使用
    _SAFE_TYPE_WHITELIST = {
        'str': str, 'int': int, 'float': float, 'bool': bool,
        'list': list, 'dict': dict, 'tuple': tuple,
        'NoneType': type(None),
    }

    def _eval_ast_call(self, node, result, depth: int) -> object:
        depth_next = depth + 1

        # ── 方法调用：obj.method(args) ──
        if isinstance(node.func, ast.Attribute):
            obj = self._eval_ast_node(node.func.value, result, depth_next)
            method_name = node.func.attr

            if method_name == 'get':
                # dict.get(key, default=None)
                default = None
                if node.args:
                    key = self._eval_ast_node(node.args[0], result, depth_next) if len(node.args) > 0 else None
                    default = self._eval_ast_node(node.args[1], result, depth_next) if len(node.args) > 1 else None
                if isinstance(obj, dict) and key is not None:
                    return obj.get(key, default)
                return default

            if method_name in ('startswith', 'endswith'):
                # str.startswith(prefix) / str.endswith(suffix)
                if not node.args:
                    raise ValueError(f"{method_name}() 缺少参数")
                arg = self._eval_ast_node(node.args[0], result, depth_next)
                if isinstance(obj, str) and arg is not None:
                    meth = getattr(obj, method_name)
                    return meth(str(arg))
                return False

            raise ValueError(f"不支持的方法调用: .{method_name}()")

        # ── 内置函数调用：func(args) ──
        if isinstance(node.func, ast.Name):
            func_name = node.func.id

            # 单参数安全内置函数
            if func_name in ('len', 'str', 'int', 'float', 'bool', 'any', 'all'):
                if not node.args:
                    raise ValueError(f"{func_name}() 缺少参数")
                arg = self._eval_ast_node(node.args[0], result, depth_next)
                try:
                    if func_name == 'len':
                        return len(arg)
                    elif func_name == 'str':
                        return str(arg)
                    elif func_name == 'int':
                        return int(arg)
                    elif func_name == 'float':
                        return float(arg)
                    elif func_name == 'bool':
                        return bool(arg)
                    elif func_name in ('any', 'all'):
                        if not hasattr(arg, '__iter__'):
                            raise ValueError(f"{func_name}() 参数必须是可迭代对象")
                        return any(arg) if func_name == 'any' else all(arg)
                except (TypeError, ValueError):
                    if func_name in ('any', 'all'):
                        return False
                    return None

            # isinstance(obj, type) — 仅允许白名单中的类型
            if func_name == 'isinstance':
                if len(node.args) < 2:
                    raise ValueError("isinstance() 需要两个参数")
                obj = self._eval_ast_node(node.args[0], result, depth_next)
                allowed_type = self._resolve_isinstance_type(node.args[1])
                if allowed_type is None:
                    raise ValueError(f"isinstance() 不允许的类型: {ast.dump(node.args[1])}")
                return isinstance(obj, allowed_type)

            raise ValueError(f"不支持的函数调用: {func_name}()")

        raise ValueError(f"不支持的函数调用: {ast.dump(node.func)}")

    def _resolve_isinstance_type(self, type_node) -> type | tuple | None:
        """解析 isinstance 的类型参数，仅返回白名单中的类型或其元组。

        支持: Name(id='str'), Constant(value=None), Tuple(elts=[...])
        """
        if isinstance(type_node, ast.Name):
            type_name = type_node.id
            if type_name == 'None':
                return type(None)
            return self._SAFE_TYPE_WHITELIST.get(type_name)
        if isinstance(type_node, ast.Constant) and type_node.value is None:
            return type(None)
        if isinstance(type_node, ast.Tuple):
            resolved = []
            for elt in type_node.elts:
                t = None
                if isinstance(elt, ast.Name):
                    t = self._SAFE_TYPE_WHITELIST.get(elt.id)
                elif isinstance(elt, ast.Constant) and elt.value is None:
                    t = type(None)
                if t is None:
                    return None  # 白名单外的类型，拒绝整个元组
                resolved.append(t)
            return tuple(resolved) if resolved else None
        return None

    # ── Checkpoint ────────────────────────────────────────────

    def _save_checkpoint(self, graph_id: str, current_node_id: str,
                         results: dict) -> None:
        import json
        try:
            from .graph_types import Checkpoint
            serialized = {}
            for nid, r in results.items():
                serialized[nid] = {
                    "step_id": r.step_id, "tool": r.tool,
                    "success": r.success, "message": r.message,
                    "data": self._safe_serialize(r.data),
                    "duration_ms": r.duration_ms,
                }
            ckpt = Checkpoint(
                graph_id=graph_id, current_node_id=current_node_id,
                completed_results=serialized, graph_state={},
            )
            path = self._checkpoint_path(graph_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(ckpt.to_dict(), ensure_ascii=False, indent=2))
        except Exception as exc:
            logger.warning("Checkpoint 保存失败 (graph_id=%s): %s", graph_id, exc)

    def _load_checkpoint(self, graph_id: str):
        import json
        from pathlib import Path
        try:
            from .graph_types import Checkpoint
            path = self._checkpoint_path(graph_id)
            if not path.exists():
                return None
            data = json.loads(path.read_text())
            return Checkpoint.from_dict(data)
        except Exception as exc:
            logger.warning("Checkpoint 加载失败 (graph_id=%s): %s", graph_id, exc)
            return None

    def _checkpoint_path(self, graph_id: str):
        import re
        from pathlib import Path
        safe_id = re.sub(r'[^a-zA-Z0-9_.-]', '_', graph_id)
        project_dir = getattr(self._ctx, 'project_path', None) or Path(ParatranzConfig.get_data_dir())
        return Path(project_dir) / "checkpoints" / f"{safe_id}.json"

    @staticmethod
    def _safe_serialize(value):
        """仅允许 JSON 可序列化类型。不可序列化对象返回 None。"""
        if value is None:
            return None
        if isinstance(value, (dict, list, str, int, float, bool)):
            return value
        # m11: 避免对 Qt 对象调用 str() 泄露内存地址
        try:
            from PyQt6.QtCore import QObject
            if isinstance(value, QObject):
                return None
        except ImportError:
            pass
        return str(value)[:ExecutionEngine._SAFE_SERIALIZE_MAX_CHARS]
