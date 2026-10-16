"""One user task keeps its controls, background counts and result destinations."""

from dataclasses import replace
from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
import pytest

from tests.ui.tools.smart_assistant.test_conversation_layout import _request
from tests.ui.tools.smart_assistant.test_request_lifecycle_panel import _until
from transbridge.application.contracts import RequestContext
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.shell.assistant_navigation import locate_assistant_entries
from transbridge.ui.tools.smart_assistant.request_list_view import RequestListView
from transbridge.ui.tools.smart_assistant.task_monitor_actions import apply_monitor_action
from transbridge.ui.tools.smart_assistant.task_presentation import related_tasks

pytest_plugins = ["tests.ui.tools.smart_assistant.test_request_lifecycle_panel"]


def test_request_task_groups_background_and_preserves_unrelated_work(environment):
    chat, binding = environment.panel.chat, environment.binding
    request = _request()
    binding.view.display((request,))
    monitor = environment.panel._task_monitor
    chat._presentation.refresh()
    monitor.refresh([
        {
            "task_id": "owned",
            "status": "running",
            "metadata": {"assistant_request_id": "r"},
            "progress": {"current": 7, "total": 24, "phase": "translating"},
        },
        {"task_id": "other", "status": "paused", "metadata": {}},
    ])
    assert "7/24" in chat._presentation.progress.cards["r"].status.text()
    assert "已完成 0/2 项" in chat._presentation.progress.cards["r"].status.text()
    assert set(monitor._tasks) == {"owned", "other"}
    assert [card._task_id for card in monitor._cards] == ["other"]
    assert "进行中 1" in chat._presentation.tasks_button.text()
    paused = {**monitor._tasks["owned"], "status": "paused"}
    monitor.refresh([paused, monitor._tasks["other"]])
    assert "已暂停 · 7/24" in chat._presentation.progress.cards["r"].status.text()
    completed = {**paused, "status": "completed"}
    monitor.refresh([completed, monitor._tasks["other"]])
    monitor._toggle_collapse()
    assert monitor._clear_all_btn.isHidden()
    binding.view.display(())
    assert {card._task_id for card in monitor._cards} == {"owned", "other"}


def test_dispatch_fallback_requires_both_job_and_run():
    request = SimpleNamespace(request_id="r", dispatches=[SimpleNamespace(job_id="j", run_id="run")])
    tasks = [{"task_id": "j", "run_id": "old"}, {"task_id": "j", "run_id": "run"}]
    assert related_tasks(request, tasks) == (tasks[1],)


def test_list_preserves_selected_identity_when_requests_reorder(environment):
    view = RequestListView()
    first, second = _request(), _request("second")
    view.display((first, second))
    view.items.setCurrentRow(1)
    view.display((second, first))
    actions = []
    view.control.connect(lambda rid, command: actions.append((rid, command)))
    view.controls["pause"].click()
    assert actions == [("second", "pause")]
    view.deleteLater()


def test_completed_result_and_failure_actions_use_original_task(environment, monkeypatch):
    binding = environment.binding
    reviews = []
    monkeypatch.setattr(
        binding.result_review, "show", lambda rid, failures_only=False: reviews.append((rid, failures_only))
    )
    request = replace(_request(), status="failed")
    binding.view.display((request,))
    card = environment.panel.chat._presentation.progress.cards["r"]
    assert card.pause.isHidden() and card.cancel.isHidden()
    card.review.click()
    card.failures.click()
    assert reviews == [("r", False), ("r", True)]


def test_disclosed_pause_and_collapsed_resume_control_the_saved_request(environment):
    panel, binding = environment.panel, environment.binding
    panel.resize(780, 680)
    panel.show()
    environment.client.block_goal = "检查任务卡暂停和继续"
    panel.chat.send_user_message(environment.client.block_goal)
    _until(environment.client.answer_started.is_set)
    presenter = panel.chat._presentation.progress
    _until(lambda: bool(presenter.cards))
    card = next(iter(presenter.cards.values()))
    request_id = card.request.request_id

    def saved():
        return next(
            request
            for request in environment.service.requests(environment.service.state(binding.context))
            if request.request_id == request_id
        )

    assert not card.pause.isVisible()
    QTest.mouseClick(card.expand, Qt.MouseButton.LeftButton)
    _until(card.pause.isVisible)
    QTest.mouseClick(card.pause, Qt.MouseButton.LeftButton)
    _until(lambda: saved().pause_reasons == ("user_paused",))
    _until(card.resume.isVisible)
    QTest.mouseClick(card.expand, Qt.MouseButton.LeftButton)
    assert not card.details.isVisible() and card.resume.isVisible()
    environment.client.block_goal = None
    QTest.mouseClick(card.resume, Qt.MouseButton.LeftButton)
    _until(lambda: saved().status == "completed")
    _until(card.review.isVisible)
    assert not card.details.isVisible() and not card.resume.isVisible()
    assert card.request.request_id == request_id


def test_locating_result_requires_original_version_and_unique_identity():
    located = []
    entry = TranslationEntry("one", "one", "source", "target", 1, None)
    host = SimpleNamespace(
        context=SimpleNamespace(active_version_identity=("p", "v"), collection=TranslationEntryCollection([entry])),
        workbench=SimpleNamespace(locate_entry=located.append),
        show_workbench=lambda: None,
        raise_=lambda: None,
        activateWindow=lambda: None,
    )
    context = RequestContext("owner", project_id="p", variant_id="v")
    locate_assistant_entries(host, context, ("one",))
    assert located == ["one"]
    collision = TranslationEntry(
        "one", "one", "other", "target", 1, None, entry_key=EntryKey(SourceNamespace("source:other"), "one")
    )
    host.context.collection = TranslationEntryCollection([entry, collision])
    with pytest.raises(ValueError, match="相同显示标识"):
        locate_assistant_entries(host, context, (entry.identity.serialize(),))
    host.context.active_version_identity = ("p", "new")
    with pytest.raises(ValueError, match="原来的项目和版本"):
        locate_assistant_entries(host, context, ("one",))
    assert located == ["one"]


def test_monitor_cleanup_and_control_recheck_ownership_and_visibility():
    called = []
    scope = {"owner_id": "owner", "session_id": "here", "project_id": "p", "variant_id": "v"}
    tasks = {
        "mine": {"status": "completed", "owner": scope},
        "changed": {"status": "completed", "owner": {**scope, "session_id": "elsewhere"}},
        "grouped": {"status": "completed", "owner": scope},
    }
    manager = SimpleNamespace(get_status=tasks.get, cleanup=called.append, cancel=called.append)
    monitor = SimpleNamespace(visible_task_ids=("mine", "changed"))
    apply_monitor_action(manager, monitor, scope, "__all__", "cleanup_completed")
    assert called == ["mine"]
    apply_monitor_action(manager, monitor, scope, "changed", "cancel")
    apply_monitor_action(manager, monitor, scope, "grouped", "cleanup")
    assert called == ["mine"]
