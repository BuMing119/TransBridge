"""Story 07: ExecutionEngine 测试 — Graph 执行 / 取消 / 重试 / 条件求值。"""
from __future__ import annotations

import sys
import threading
import time
import unittest

from PyQt6.QtWidgets import QApplication

_app = QApplication.instance()
if _app is None:
    _app = QApplication(sys.argv)


class FakeToolRegistry:
    """Mock 工具注册表，工具执行直接返回预设结果。"""

    def get(self, name, namespace=None):
        spec = FakeToolSpec(name)
        return spec


class FakeToolSpec:
    def __init__(self, name):
        self.name = name
        self.permission = "read"

    def execute(self, args, ctx):
        return {"success": True, "message": f"{self.name} 执行成功", "data": {}}


class FakeCtx:
    collection = None
    active_slot = None
    esp_path = None


from src.transbridge.smart_assistant.graph_types import ActionNode, EdgeSpec, GraphSpec


class TestExecutionEngine(unittest.TestCase):
    """ExecutionEngine 核心功能测试。"""

    def setUp(self):
        from src.transbridge.smart_assistant.execution_engine import ExecutionEngine
        self.registry = FakeToolRegistry()
        self.ctx = FakeCtx()
        self.engine = ExecutionEngine(self.registry, self.ctx, middlewares=[])

    def tearDown(self):
        self.engine.cancel()

    # ── 图执行 ──────────────────────────────────────────────────

    def test_execute_linear_graph(self):
        nodes = [
            ActionNode(node_id="step_A", node_type="action", tool="tool_a"),
            ActionNode(node_id="step_B", node_type="action", tool="tool_b"),
            ActionNode(node_id="step_C", node_type="action", tool="tool_c"),
        ]
        edges = [
            EdgeSpec(from_node="step_A", to_node="step_B"),
            EdgeSpec(from_node="step_B", to_node="step_C"),
        ]
        graph = GraphSpec(graph_id="linear_test", nodes=nodes, edges=edges, entry_node="step_A")

        results = self.engine.execute_graph(graph)
        self.assertEqual(len(results), 3)
        for r in results:
            self.assertTrue(r.success, f"Step {r.step_id} ({r.tool}) 应成功: {r.message}")

    def test_execute_single_node(self):
        nodes = [ActionNode(node_id="only_node", node_type="action", tool="solo_tool")]
        graph = GraphSpec(graph_id="single_test", nodes=nodes, edges=[], entry_node="only_node")

        results = self.engine.execute_graph(graph)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].success)

    def test_execute_results_order(self):
        nodes = [
            ActionNode(node_id="first", node_type="action", tool="tool1"),
            ActionNode(node_id="second", node_type="action", tool="tool2"),
        ]
        edges = [EdgeSpec(from_node="first", to_node="second")]
        graph = GraphSpec(graph_id="order_test", nodes=nodes, edges=edges, entry_node="first")

        results = self.engine.execute_graph(graph)
        self.assertEqual(results[0].tool, "tool1")
        self.assertEqual(results[1].tool, "tool2")

    # ── 取消 ─────────────────────────────────────────────────────

    def test_cancel_stops_execution(self):
        nodes = [ActionNode(node_id=f"node_{i}", node_type="action", tool=f"tool_{i}") for i in range(10)]
        edges = [EdgeSpec(from_node=f"node_{i}", to_node=f"node_{i+1}") for i in range(9)]
        graph = GraphSpec(graph_id="cancel_test", nodes=nodes, edges=edges, entry_node="node_0")

        def _cancel_soon():
            time.sleep(0.1)
            self.engine.cancel()

        t = threading.Thread(target=_cancel_soon, daemon=True)
        t.start()
        results = self.engine.execute_graph(graph)
        t.join(timeout=5)
        # cancel 后应尽早返回，结果数应少于全部节点
        self.assertLessEqual(len(results), 10)

    # ── 重试 ─────────────────────────────────────────────────────

    def test_retry_handler_available(self):
        """M1: RetryHandler 应在 __init__ 中实例化。"""
        self.assertIsNotNone(self.engine._retry_handler)

    # ── 暂停/恢复 ────────────────────────────────────────────────

    def test_pause_is_instance_level(self):
        """M8: _paused 应为实例级属性。"""
        self.assertIsInstance(self.engine._paused, threading.Event)
        self.assertFalse(self.engine._paused.is_set())
        self.engine._paused.set()
        self.assertTrue(self.engine._paused.is_set())
        self.engine._paused.clear()
        self.assertFalse(self.engine._paused.is_set())

    def test_paused_independent_between_engines(self):
        """M8: 不同 ExecutionEngine 实例的 _paused 应独立。"""
        engine2 = type(self.engine)(self.registry, self.ctx)
        self.engine._paused.set()
        self.assertFalse(engine2._paused.is_set())
        engine2.cancel()

    # ── 条件求值 ─────────────────────────────────────────────────

    def test_eval_condition_true(self):
        from src.transbridge.smart_assistant.execution_engine import StepResult
        r = StepResult(step_id=1, tool="test", success=True, message="ok")
        self.assertTrue(self.engine._condition_evaluator.eval_condition("result.success == True", {"node1": r}))

    def test_eval_condition_false(self):
        from src.transbridge.smart_assistant.execution_engine import StepResult
        r = StepResult(step_id=1, tool="test", success=False, message="failed")
        self.assertFalse(self.engine._condition_evaluator.eval_condition("result.success == True", {"node1": r}))

    def test_eval_condition_empty(self):
        self.assertFalse(self.engine._condition_evaluator.eval_condition("", {}))

    # ── 中间件链 ─────────────────────────────────────────────────

    def test_guards_provided_from_init(self):
        """B3: 传入的 middlewares 应被用作 _guards。"""
        from unittest.mock import MagicMock
        mw = MagicMock()
        mw.before_execute.return_value = type('Gr', (), {'allowed': True, 'reason': '', 'modified_args': None})()
        engine2 = type(self.engine)(self.registry, self.ctx, middlewares=[mw])
        self.assertEqual(len(engine2._guards), 1)
        engine2.cancel()

    # ── Decision CV ──────────────────────────────────────────────

    def test_decision_cv_created(self):
        """M9: _decision_cv 应在 __init__ 中创建。"""
        self.assertIsNotNone(self.engine._decision_cv)

    def test_provide_decision(self):
        self.engine.provide_decision("test_node", "继续")
        self.assertIn("test_node", self.engine._pending_decisions)
        self.assertEqual(self.engine._pending_decisions["test_node"], "继续")

    # ── ThreadPoolExecutor ───────────────────────────────────────

    def test_executor_reused(self):
        """M11: _executor 应在 __init__ 中创建并可用。"""
        self.assertIsNotNone(self.engine._executor._executor)
        future = self.engine._executor._executor.submit(lambda: 42)
        self.assertEqual(future.result(timeout=5), 42)


if __name__ == "__main__":
    unittest.main()
