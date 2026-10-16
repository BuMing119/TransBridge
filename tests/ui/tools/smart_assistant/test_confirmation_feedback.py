"""Accepted proposals retain their own outcomes, previews and cancellation state."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.ui.tools.smart_assistant.test_confirmation_lifecycle import _APP, _plan_binding, _view
from tests.ui.tools.smart_assistant.test_request_lifecycle_panel import _requests, _until, environment
from transbridge.smart_assistant.execution_engine import StepResult
from transbridge.smart_assistant.tool_execution_handler import DetachedToolResult, ToolExecutionHandler
from transbridge.smart_assistant.tool_registry import ToolRegistry, ToolSpec
from transbridge.smart_assistant.tools.base import ToolResult
from transbridge.ui.tools.smart_assistant.operation_preview import operation_summary
from transbridge.ui.tools.smart_assistant.react_execution_binding import ReactExecutionBinding
from transbridge.ui.tools.smart_assistant.tool_card import ToolCard

__all__ = ["environment"]


@pytest.mark.parametrize("success", [True, False])
def test_tool_result_returns_to_original_card_only(success):
    view, parent, _, _ = _view()
    step = {"tool": "same_tool", "tool_call_id": "call-1", "args": {"entry_id": "a"}}
    card = view.add_tool_card(step)
    card._exec_btn.click()
    result = ToolResult(success, "已修改" if success else "目标词条不存在")
    view.complete_tool(deepcopy(step), result)
    assert card._exec_btn.text() == "执行中..."
    view.complete_tool(step, result)
    assert card._exec_btn.text() == ("已完成" if success else "执行失败")
    assert result.message in card._result_label.text()
    view.close()
    parent.close()


def test_late_result_cannot_update_a_new_card_with_the_same_call_id():
    view, parent, _, _ = _view()
    first = {"tool": "read", "tool_call_id": "reused"}
    old = view.add_tool_card(first)
    old._exec_btn.click()
    view.invalidate_pending()
    second = deepcopy(first)
    current = view.add_tool_card(second)
    current._exec_btn.click()
    view.complete_tool(first, ToolResult.ok("old result"))
    assert old._exec_btn.text() == "结果待核对"
    assert current._exec_btn.text() == "执行中..."
    view.complete_tool(second, ToolResult.ok("new result"))
    assert current._exec_btn.text() == "已完成"
    assert "new result" in current._result_label.text()
    view.close()
    parent.close()


@pytest.mark.parametrize(
    ("result", "label"),
    [
        (ToolResult.ok("后台任务已启动", data={"task_id": "task-a"}), "已启动任务"),
        (ToolResult.fail("用户取消", data={"status": "cancelled"}), "已取消"),
        (ToolResult.fail("未收到最终结果", error_code="RESULT_UNAVAILABLE"), "结果待核对"),
    ],
)
def test_tool_outcomes_distinguish_started_cancelled_and_unknown(result, label):
    view, parent, _, _ = _view()
    step = {"tool": "start_translation"}
    card = view.add_tool_card(step)
    card._exec_btn.click()
    view.complete_tool(step, result)
    assert card._exec_btn.text() == label
    view.close()
    parent.close()


def test_batch_results_remain_separate_for_repeated_tools():
    view, parent, _, _ = _view()
    steps = [{"tool": "edit_translation", "args": {"entry_id": key}} for key in ("a", "b")]
    card = view.add_batch_tool_card(steps)
    card._exec_btn.click()
    view.complete_tool(steps[1], ToolResult.fail("b 不存在"))
    assert card._exec_btn.text() == "执行中..."
    view.complete_tool(steps[0], ToolResult.ok("a 已更新"))
    assert card._exec_btn.text() == "存在失败"
    assert "a 已更新" in card._labels[1].text()
    assert "b 不存在" in card._labels[2].text()
    assert "2/2" in card._result_label.text()
    view.close()
    parent.close()


def test_rejected_confirmation_expires_instead_of_staying_running():
    view, parent, intents, _ = _view()
    view.intent_scope = lambda **kwargs: lambda: False
    card = view.add_tool_card({"tool": "read"})
    card._exec_btn.click()
    assert card._exec_btn.text() == "已失效"
    assert not intents
    view.close()
    parent.close()


def test_background_tool_uses_original_invocation_identity(monkeypatch):
    view, parent, _, _ = _view()
    spec = ToolSpec("read", "读取信息", "read", {}, execute=lambda args, ctx: ToolResult.ok("读取完成"))
    monkeypatch.setattr(ToolRegistry, "get", lambda name: spec)
    handler = ToolExecutionHandler(SimpleNamespace(), Mock(), on_tool_result=view.complete_tool)
    handler._middlewares = []
    binding = ReactExecutionBinding(parent=parent, handler=handler, controller=Mock, system_message=lambda _: None)
    view._tool_executed = lambda step: binding.execute([step])
    card = view.add_tool_card({"tool": "read", "args": {}, "tool_call_id": "native-call"})
    try:
        card._exec_btn.click()
        _until(lambda: card._exec_btn.text() == "已完成")
        assert "读取完成" in card._result_label.text()
    finally:
        binding.close()
        view.close()
        parent.close()


def test_background_worker_error_is_visible_on_original_card(monkeypatch):
    view, parent, _, _ = _view()
    handler = ToolExecutionHandler(SimpleNamespace(), Mock(), on_tool_result=view.complete_tool)
    monkeypatch.setattr(handler, "can_execute_detached", lambda steps: True)

    def fail(*args):
        raise RuntimeError("read connection closed")

    monkeypatch.setattr(handler, "execute_steps_detached", fail)
    binding = ReactExecutionBinding(parent=parent, handler=handler, controller=Mock, system_message=lambda _: None)
    view._tool_executed = lambda step: binding.execute([step])
    card = view.add_tool_card({"tool": "read"})
    try:
        card._exec_btn.click()
        _until(lambda: "未收到操作结果" in card._result_label.text())
        assert "connection closed" in card._result_label.toolTip()
        assert card._exec_btn.text() != "执行中..."
    finally:
        binding.close()
        view.close()
        parent.close()


@pytest.mark.parametrize("returned_indices", [(1,), (1, 0)])
def test_partial_or_reordered_results_retain_exact_invocation_identity(monkeypatch, returned_indices):
    view, parent, _, _ = _view()
    handler = ToolExecutionHandler(SimpleNamespace(), Mock(), on_tool_result=view.complete_tool)
    monkeypatch.setattr(handler, "can_execute_detached", lambda steps: True)

    def execute(steps, *_):
        return tuple(
            DetachedToolResult(deepcopy(steps[index]), ToolResult.ok(f"result-{index}"), source_step=steps[index])
            for index in returned_indices
        )

    monkeypatch.setattr(handler, "execute_steps_detached", execute)
    binding = ReactExecutionBinding(parent=parent, handler=handler, controller=Mock, system_message=lambda _: None)
    view._batch_executed = binding.execute
    card = view.add_batch_tool_card([{"tool": "read", "args": {"id": index}} for index in (0, 1)])
    try:
        card._exec_btn.click()
        _until(lambda: len(card._results) == 2)
        assert "result-1" in card._labels[2].text()
        if 0 in returned_indices:
            assert "result-0" in card._labels[1].text()
            assert card._exec_btn.text() == "已完成"
        else:
            assert "未收到此操作的结果" in card._labels[1].text()
            assert card._exec_btn.text() == "请核对结果"
    finally:
        binding.close()
        view.close()
        parent.close()


def test_handler_preserves_unknown_result_category_for_the_card():
    view, parent, _, _ = _view()
    handler = ToolExecutionHandler(SimpleNamespace(), Mock(), on_tool_result=view.complete_tool)
    step = {"tool": "read"}
    card = view.add_tool_card(step)
    card._exec_btn.click()
    handler._handle_result(step, ToolResult.fail("result unavailable", error_code="RESULT_UNAVAILABLE"))
    assert card._exec_btn.text() == "结果待核对"
    view.close()
    parent.close()


@pytest.mark.parametrize("kind", ["tool", "batch", "plan"])
def test_confirmation_previews_include_readable_scope_changes_and_complete_arguments(monkeypatch, kind):
    spec = ToolSpec("edit_translation", "修改译文", "edit", {}, permission="write")
    monkeypatch.setattr(ToolRegistry, "get", lambda name: spec)
    view, parent, _, _ = _view()
    text = "literal token=dragon\n" + "完整译文" * 100
    step = {"id": 1, "tool": "edit_translation", "args": {"entry_ids": ["a", "b"], "new_translation": text}}
    factory = getattr(view, f"add_{'batch_tool' if kind == 'batch' else kind}_card")
    card = factory(step if kind == "tool" else [step])
    details = card._preview._details.toPlainText()
    assert "修改译文" in details
    assert "2 条词条" in details
    assert "译文改为" in details
    assert text.replace("\n", "\\n") in details
    assert card._preview._details.isHidden()
    card._preview._toggle.click()
    assert not card._preview._details.isHidden()
    view.close()
    parent.close()


def test_dynamic_scope_does_not_claim_a_known_target_count():
    assert "数量待执行时确定" in operation_summary({"tool": "start_translation", "args": {}})


def test_late_plan_feedback_does_not_update_new_plan():
    view, parent, _, _ = _view()
    steps = [{"id": 1, "tool": "read"}]
    old = view.add_plan_card(steps)
    old._exec_btn.click()
    feedback = view.plan_feedback(steps)
    view.invalidate_pending()
    current = view.add_plan_card(deepcopy(steps))
    current._exec_btn.click()
    feedback("on_all_finished", [StepResult(1, "read", True, "old result")])
    assert old._exec_btn.text() == "结果待核对"
    assert current._exec_btn.text() == "执行中..."
    view.close()
    parent.close()


@pytest.mark.parametrize("outcome", ["success", "failure", "cancelled", "missing"])
def test_plan_completion_updates_card_through_engine_binding(monkeypatch, outcome):
    binding, *_ = _plan_binding()
    view, parent, _, _ = _view(lambda: binding.engine)
    engine = Mock()
    runtime = Mock(execution_context=SimpleNamespace(), task_id="parent", run_id="run")
    monkeypatch.setattr("transbridge.smart_assistant.execution_engine.ExecutionEngine", lambda *args, **kwargs: engine)
    monkeypatch.setattr("transbridge.smart_assistant.plan_task_runtime.PlanTaskRuntime", lambda *args: runtime)
    view._plan_confirmed = lambda steps: binding.confirm(steps, view)
    steps = [{"id": 1, "tool": "read", "args": {}}]
    card = view.add_plan_card(steps)
    try:
        card._exec_btn.click()
        engine.on_step_started.call_args.args[0](1, "read")
        _APP.processEvents()
        assert "执行中" in card._step_items[1].text()
        if outcome == "failure":
            runtime.start.call_args.kwargs["failed"](RuntimeError("cannot read project"))
        else:
            result = StepResult(
                1, "read", outcome == "success", "done", data={"status": "cancelled"} if outcome == "cancelled" else {}
            )
            runtime.start.call_args.kwargs["finished"]([] if outcome == "missing" else [result])
        _APP.processEvents()
        assert card._exec_btn.text() != "执行中..."
        expected = {"success": "完成", "failure": "cannot read project", "cancelled": "已取消", "missing": "结果不完整"}
        assert expected[outcome] in card._progress_label.text()
        assert not card._cancel_btn.isEnabled()
    finally:
        view.close()
        binding.close()
        parent.close()


def test_durable_request_completion_updates_its_confirmation_card(environment):
    environment.client.tool_goal = "Check the project"
    chat = environment.panel.chat
    chat.send_user_message("Check the project")
    _until(lambda: any(isinstance(widget, ToolCard) for widget in chat._message_list._owned_widgets))
    card = next(widget for widget in chat._message_list._owned_widgets if isinstance(widget, ToolCard))
    _until(card._exec_btn.isEnabled)
    card._exec_btn.click()
    _until(lambda: _requests(environment)[0].terminal)
    assert card._exec_btn.text() == "已完成"
    assert "Offline statistics" in card._result_label.text()
