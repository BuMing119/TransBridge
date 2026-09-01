from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from transbridge.smart_assistant.checkpoint_manager import CheckpointManager
from transbridge.smart_assistant.graph_executor import GraphExecutor
from transbridge.smart_assistant.guardrails.base import GuardResult
from transbridge.smart_assistant.tools.task_manager import TaskManager


class Guard:
    def before_execute(self, *_args):
        return GuardResult(allowed=True)

    def after_execute(self, *_args):
        return GuardResult(allowed=True)


@pytest.fixture
def manager():
    TaskManager.reset()
    yield TaskManager()
    TaskManager.reset()


def make_executor(tmp_path, functions):
    registry = SimpleNamespace(
        get=lambda name, **_kwargs: SimpleNamespace(execute=functions[name], is_long_running=True)
    )
    executor = GraphExecutor(registry, SimpleNamespace(), [Guard()], checkpoint_manager=CheckpointManager(tmp_path))
    executor._retry_handler = None
    return executor


@pytest.mark.parametrize("outcome", ["completed", "failed", "cancelled"])
def test_graph_waits_for_real_worker_terminal_and_blocks_failed_dependency(manager, tmp_path, outcome):
    entered = threading.Event()
    release = threading.Event()
    admitted = threading.Event()
    calls = []
    tasks = []

    def produce(_args, _ctx):
        task_id = manager.register()
        tasks.append(task_id)
        handle = manager.get_handle(task_id)

        def work():
            entered.set()
            assert release.wait(3)
            if outcome == "failed":
                raise ValueError("upstream failed")
            decision = handle.execution.commit(task_id, lambda: calls.append("produced"))
            if decision.accepted:
                manager.notify_completed(task_id, {"artifact": "ready"})

        manager.start_thread(task_id, work)
        admitted.set()
        return {"success": True, "message": "admitted", "data": {"task_id": task_id}}

    def consume(_args, _ctx):
        assert calls == ["produced"]
        calls.append("consumed")
        return {"success": True, "message": "consumed"}

    executor = make_executor(tmp_path, {"produce": produce, "consume": consume})
    results = []
    runner = threading.Thread(
        target=lambda: results.extend(
            executor.execute([
                {"id": 1, "tool": "produce"},
                {"id": 2, "tool": "consume", "depends_on": [1]},
            ])
        )
    )
    runner.start()
    try:
        assert admitted.wait(3) and entered.wait(3)
        runner.join(0.1)
        assert runner.is_alive()
        assert calls == []
        if outcome == "cancelled":
            handle = manager.get_handle(tasks[0])
            manager.runtime.cancel(handle.execution.ref, handle.execution.owner)
        release.set()
        runner.join(5)
        assert not runner.is_alive()
        assert len(results) == 2
        producer, consumer = results
        if outcome == "completed":
            assert producer.success and consumer.success
            assert producer.data["artifact"] == "ready"
            assert calls == ["produced", "consumed"]
        else:
            assert not producer.success and not consumer.success
            assert producer.data["status"] == outcome
            assert "未执行" in consumer.message
            assert calls == []
    finally:
        release.set()
        executor.cancel()
        runner.join(5)
        executor.shutdown()


def test_unequal_depth_dag_join_waits_for_all_predecessors(manager, tmp_path):
    calls = []

    def tool(name):
        def execute(_args, _ctx):
            if name == "join":
                assert "deep" in calls and "short" in calls
            calls.append(name)
            return {"success": True, "message": name}

        return execute

    executor = make_executor(tmp_path, {name: tool(name) for name in ("short", "long", "deep", "join")})
    try:
        results = executor.execute([
            {"id": 1, "tool": "short"},
            {"id": 2, "tool": "long"},
            {"id": 3, "tool": "deep", "depends_on": [2]},
            {"id": 4, "tool": "join", "depends_on": [1, 3]},
        ])
    finally:
        executor.shutdown()
    assert all(result.success for result in results)
    assert calls.index("join") > calls.index("deep")


def test_graph_cancel_reaches_running_job_without_waiting_on_gui_thread(manager, tmp_path):
    entered = threading.Event()
    release = threading.Event()
    tasks = []
    mutations = []

    def produce(_args, _ctx):
        task_id = manager.register()
        tasks.append(task_id)
        binding = manager.get_handle(task_id).execution

        def work():
            entered.set()
            assert release.wait(3)
            binding.commit(task_id, lambda: mutations.append("late"))

        manager.start_thread(task_id, work)
        return {"success": True, "data": {"task_id": task_id}}

    executor = make_executor(tmp_path, {"produce": produce})
    results = []
    runner = threading.Thread(target=lambda: results.extend(executor.execute([{"id": 1, "tool": "produce"}])))
    runner.start()
    try:
        assert entered.wait(3)
        executor.cancel()
        executor.shutdown(wait=False)
        handle = manager.get_handle(tasks[0])
        assert handle.stop_event.wait(3)
        assert runner.is_alive()
        release.set()
        runner.join(5)
        assert not runner.is_alive()
        assert len(results) == 1 and not results[0].success
        assert results[0].data["status"] == "cancelled"
        assert mutations == []
    finally:
        release.set()
        runner.join(5)
        executor.shutdown()
