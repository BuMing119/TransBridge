"""The default conversation stays quiet while management and search remain reachable."""

from dataclasses import replace

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMenu, QPlainTextEdit

from tests.ui.tools.smart_assistant.test_conversation_layout import _request
from tests.ui.tools.smart_assistant.test_request_lifecycle_panel import _until
from transbridge.application.assistant_requests.models import ItemStatus, RequestStatus
from transbridge.ui.tools.smart_assistant.submission_scope import SubmissionScope, SubmissionScopeView

pytest_plugins = ["tests.ui.tools.smart_assistant.test_request_lifecycle_panel"]


def test_context_and_active_task_share_one_row_and_completed_results_use_menu(environment, monkeypatch):
    panel, binding = environment.panel, environment.binding
    panel.show()
    binding.set_active(False)
    _until(lambda: binding._accepting == 0 and not binding.management._refresh._pending)
    chrome = panel.chat._presentation
    chrome._requests_changed(())
    assert chrome.tasks_button.isHidden()
    assert chrome.dialog.isHidden()
    assert panel.chat._message_list.navigation.isHidden()
    chrome._requests_changed((_request(),))
    QApplication.processEvents()
    assert chrome.tasks_button.isVisible()
    assert chrome.context_label.parentWidget() is chrome.tasks_button.parentWidget() is chrome.toolbar
    assert chrome.toolbar.height() <= 32
    chrome.tasks_button.click()
    assert chrome.dialog.isVisible()
    chrome.tasks_button.click()
    assert chrome.dialog.isHidden()
    completed = replace(
        _request(),
        status=RequestStatus.COMPLETED,
        items=tuple(replace(item, status=ItemStatus.SATISFIED) for item in _request().items),
    )
    chrome._requests_changed((completed,))
    assert chrome.tasks_button.isHidden()

    def choose_tasks(menu, *_):
        next(action for action in menu.actions() if action.text() == "全部任务与结果（1）").trigger()

    monkeypatch.setattr(QMenu, "exec", choose_tasks)
    chrome.menu_button.click()
    assert chrome.dialog.isVisible()


def test_history_search_can_be_opened_from_menu_or_local_shortcut(environment, monkeypatch):
    panel = environment.panel
    environment.binding.set_active(False)
    panel.show()
    panel.activateWindow()
    chat, navigation = panel.chat, panel.chat._message_list.navigation

    def choose_search(menu, *_):
        next(action for action in menu.actions() if action.text() == "搜索会话历史").trigger()

    monkeypatch.setattr(QMenu, "exec", choose_search)
    chat._presentation.menu_button.click()
    assert navigation.search_panel.isVisible()
    navigation.close_search()
    assert navigation.isHidden()
    chat._input.setFocus()
    QApplication.processEvents()
    QTest.keyClick(chat._input, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
    _until(lambda: navigation.search_panel.isVisible())
    assert navigation.query.hasFocus()
    QTest.keyClick(navigation.query, Qt.Key.Key_Escape)
    assert navigation.search_panel.isHidden()
    assert chat._input.hasFocus()
    panel.resize(480, 620)
    navigation.open_search()
    QApplication.processEvents()
    assert panel.width() == 480
    for control in (
        navigation.query,
        navigation.find,
        navigation.previous_match,
        navigation.next_match,
        navigation.close_search_button,
        chat._input,
        chat._send_btn,
    ):
        assert control.isVisible()
        assert panel.rect().contains(control.mapTo(panel, QPoint()))
        assert panel.rect().contains(control.mapTo(panel, control.rect().bottomRight()))


def test_attention_and_routing_remain_visible_in_narrow_conversation(environment):
    panel = environment.panel
    panel.resize(480, 620)
    panel.show()
    binding = environment.binding
    binding.set_active(False)
    _until(lambda: binding._accepting == 0 and not binding.management._refresh._pending)
    chrome = panel.chat._presentation
    failed = replace(_request(), status=RequestStatus.FAILED)
    chrome._requests_changed((failed,))
    chrome._pending(1, 0)
    panel.chat._ctx.project_name = "一个用于验证省略显示的非常长的项目名称" * 5
    chrome.refresh()
    QApplication.processEvents()
    assert chrome.tasks_button.text() == "待处理 1"
    for control in (chrome.context_label, chrome.tasks_button, chrome.menu_button, chrome.notice):
        assert control.isVisible()
        assert panel.rect().contains(control.mapTo(panel, QPoint()))
        assert panel.rect().contains(control.mapTo(panel, control.rect().bottomRight()))
    chrome.notice.click()
    assert chrome.dialog.isVisible()


def test_compact_message_scope_keeps_frozen_details_accessible(environment):
    scope = SubmissionScope("提交时：原项目 · 选中 2 条", "原项目范围：entry-a、entry-b")
    view = SubmissionScopeView(environment.panel.chat._theme, compact=True)
    view.set_scope(scope)
    view.show()
    try:
        assert view.summary.isHidden()
        assert view.details_button.isVisible()
        assert view.details_button.toolTip() == scope.summary
        view.details_button.setFocus()
        QTest.keyClick(view.details_button, Qt.Key.Key_Space)
        content = view.findChild(QPlainTextEdit)
        assert content is not None and content.isReadOnly()
        assert content.toPlainText() == scope.details
    finally:
        view.close()
        view.deleteLater()
