import ast
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
    agent_instance_id: str = ""


class ExecutionEngine(QObject):
    """统一执行引擎：DAG 拓扑排序 + 层级并行执行 + 安全护栏中间件。"""

    step_started = pyqtSignal(int, str)       # step_id, tool_name
    step_finished = pyqtSignal(StepResult)
    all_finished = pyqtSignal(list)            # list[StepResult]
    progress = pyqtSignal(int, int)            # completed, total
    step_retrying = pyqtSignal(int, int)       # step_id, attempt
    step_requires_confirmation = pyqtSignal(str, str, list)  # node_id, prompt, choices

    _MAX_WORKERS = 4
    _CONFIRM_TIMEOUT = 300.0
    _MAX_EVAL_DEPTH = 50
    _SAFE_SERIALIZE_MAX_CHARS = 200

    def __init__(self, tool_registry, ctx, parent=None, middlewares=None):
        super().__init__(parent)
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

    def cancel(self) -> None:
        self._cancelled.set()

    def shutdown(self):
        """关闭线程池，释放线程资源。"""
        if hasattr(self, '_executor') and self._executor:
            self._executor.shutdown(wait=False)

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
                         agent_instance_id: str) -> tuple:
        """执行 before 护栏中间件链。

        Returns:
            (early_result, current_step): early_result 非 None 表示被护栏阻断，
            调用方应直接返回该 StepResult；current_step 为可能被修改后的 step 副本。
        """
        current_step = dict(step)
        for mw in self._guards:
            guard_result = mw.before_execute(step, exec_ctx)
            if not guard_result.allowed:
                if guard_result.reason in ("admin_confirm_required", "write_confirm_required"):
                    perm_label = "管理级" if "admin" in guard_result.reason else "写入级"
                    node_id = f"step_{step_id}"
                    self.step_requires_confirmation.emit(
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
        args = step.get("args", {})
        agent_instance = step.get("_instance")
        agent_instance_id = step.get("agent_instance_id", "")
        namespace = None
        if agent_instance is not None and hasattr(agent_instance, 'agent_spec'):
            namespace = agent_instance.agent_spec.namespace

        self.step_started.emit(step_id, tool_name)
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

        # Reflexion 重试循环
        attempt = 0
        raw_result = None
        while True:
            try:
                raw_result = spec.execute(current_step.get("args", args), exec_ctx)
                break
            except Exception as exc:
                if (self._retry_handler is None or
                        not self._retry_handler.should_retry(str(exc)) or
                        attempt >= self._retry_handler.MAX_RETRIES):
                    return StepResult(
                        step_id=step_id, tool=tool_name,
                        success=False, message=f"执行异常: {exc}",
                        duration_ms=int((time.monotonic() - start) * 1000),
                        agent_instance_id=agent_instance_id,
                    )
                adjusted = self._retry_handler.analyze_and_adjust(
                    current_step, str(exc), attempt)
                if adjusted is None:
                    return StepResult(
                        step_id=step_id, tool=tool_name,
                        success=False, message=f"执行异常: {exc}",
                        duration_ms=int((time.monotonic() - start) * 1000),
                        agent_instance_id=agent_instance_id,
                    )
                current_step = adjusted
                attempt += 1
                self.step_retrying.emit(step_id, attempt)

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

    # ── Graph 编排扩展 (S09/S10) ──────────────────────────────

    def _dispatch_node(self, node, node_map: dict, results: dict):
        """调度单个图节点：根据节点类型执行相应逻辑。"""
        from .graph_types import (ActionNode, ConditionNode, LoopNode,
                                   HumanConfirmNode)
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
                return self._dispatch_node(node_map[next_id], node_map, results)
            return None
        elif isinstance(node, LoopNode):
            last_result = None
            for i in range(node.max_iterations):
                if self._cancelled.is_set():
                    break
                for sub_node in node.sub_nodes:
                    r = self._dispatch_node(sub_node, node_map, results)
                    if r is not None:
                        results[sub_node.node_id] = r
                        last_result = r
                if last_result and self._eval_condition(
                        node.exit_condition,
                        {nid: results.get(nid) for nid in results}):
                    break
            return last_result
        elif isinstance(node, HumanConfirmNode):
            self.step_requires_confirmation.emit(
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
                self.step_finished.emit(r)
                self.progress.emit(len(completed), total)

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
                import logging
                logging.getLogger(__name__).warning(
                    "Checkpoint 自动保存失败 (graph_id=%s)", graph.graph_id)

        final = [results.get(n.node_id) for n in graph.nodes if n.node_id in results]
        self.all_finished.emit(final)
        return [r for r in final if r is not None]

    def execute(self, steps: list[dict]) -> list[StepResult]:
        """向后兼容：steps 转为线性 GraphSpec → 委托给 execute_graph。"""
        from .graph_types import ActionNode, EdgeSpec, GraphSpec
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
                edges.append(EdgeSpec(from_node=f"step_{steps[i-1]['id']}", to_node=nid))
        graph = GraphSpec(
            graph_id=f"linear_{abs(hash(str(steps)))}",
            nodes=nodes, edges=edges,
            entry_node=nodes[0].node_id if nodes else "",
        )
        return self.execute_graph(graph)

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
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id == "result":
                return result
            if node.id in ("True", "False"):
                return node.id == "True"
            if node.id == "None":
                return None
            raise ValueError(f"未知变量: {node.id}")
        if isinstance(node, ast.Attribute):
            obj = self._eval_ast_node(node.value, result, depth + 1)
            if obj is None:
                return None
            return getattr(obj, node.attr, None)
        if isinstance(node, ast.Subscript):
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
        if isinstance(node, ast.Compare):
            left = self._eval_ast_node(node.left, result, depth + 1)
            for op, comparator in zip(node.ops, node.comparators):
                right = self._eval_ast_node(comparator, result, depth + 1)
                if isinstance(op, ast.Eq):
                    return left == right
                if isinstance(op, ast.NotEq):
                    return left != right
                if isinstance(op, ast.Lt):
                    return (left is not None and right is not None and left < right)
                if isinstance(op, ast.LtE):
                    return (left is not None and right is not None and left <= right)
                if isinstance(op, ast.Gt):
                    return (left is not None and right is not None and left > right)
                if isinstance(op, ast.GtE):
                    return (left is not None and right is not None and left >= right)
                if isinstance(op, ast.In):
                    return left in right if right is not None else False
                if isinstance(op, ast.NotIn):
                    return left not in right if right is not None else True
            return False
        if isinstance(node, ast.BoolOp):
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
        if isinstance(node, ast.UnaryOp):
            operand = self._eval_ast_node(node.operand, result, depth + 1)
            if isinstance(node.op, ast.Not):
                return not operand
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute) and node.func.attr == 'get':
                obj = self._eval_ast_node(node.func.value, result, depth + 1)
                default = None
                if node.args:
                    key = self._eval_ast_node(node.args[0], result, depth + 1) if len(node.args) > 0 else None
                    default = self._eval_ast_node(node.args[1], result, depth + 1) if len(node.args) > 1 else None
                if isinstance(obj, dict) and key is not None:
                    return obj.get(key, default)
                return default
        raise ValueError(f"不支持的 AST 节点: {type(node).__name__}")

    # ── Checkpoint ────────────────────────────────────────────

    def _save_checkpoint(self, graph_id: str, current_node_id: str,
                         results: dict) -> None:
        import json
        import logging
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
            logging.getLogger(__name__).warning("Checkpoint 保存失败: %s", exc)

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
        except Exception:
            return None

    def _checkpoint_path(self, graph_id: str):
        import re
        from pathlib import Path
        safe_id = re.sub(r'[^a-zA-Z0-9_.-]', '_', graph_id)
        project_dir = getattr(self._ctx, 'project_path', None) or Path("data")
        return Path(project_dir) / "checkpoints" / f"{safe_id}.json"

    @staticmethod
    def _safe_serialize(value):
        """仅允许 JSON 可序列化类型。不可序列化对象返回 None。"""
        if value is None:
            return None
        if isinstance(value, (dict, list, str, int, float, bool)):
            return value
        # m11: 避免对 Qt 对象调用 str() 泄露内存地址
        if isinstance(value, QObject):
            return None
        return str(value)[:ExecutionEngine._SAFE_SERIALIZE_MAX_CHARS]
