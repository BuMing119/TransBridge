from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QPoint
from PyQt6.QtGui import QFontDatabase
from PyQt6.QtWidgets import QApplication
import pytest

from tests.ui.tools.smart_assistant.test_v2_chat_recovery import _until
from transbridge.ui.tools.smart_assistant.composer_editor import ComposerEditor
from transbridge.ui.tools.smart_assistant.submission_scope import SubmissionScope

pytest_plugins = ["tests.ui.tools.smart_assistant.test_v2_chat_recovery"]


@pytest.fixture(autouse=True)
def chinese_font_metrics():
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    font_id = QFontDatabase.addApplicationFont(str(font_path)) if font_path.exists() else -1
    yield
    if font_id >= 0:
        QFontDatabase.removeApplicationFont(font_id)


@pytest.mark.parametrize("requested_size", [(480, 400), (480, 620), (1100, 760)])
@pytest.mark.parametrize("draft", ["继续处理", "请逐条核对下面的中文要求。\n" * 8])
def test_window_protects_composer_when_small_and_task_pane_is_expanded(
    chat_environment, requested_size, draft, monkeypatch
):
    panel = chat_environment.panel()
    _until(lambda: panel.chat.session_ready and panel._workspace.ready())
    panel.chat.load_history([
        {"role": "user", "content": f"核对译文 {index}", "message_id": f"message-{index}"} for index in range(105)
    ])
    panel.chat._message_list.set_scopes({
        "message-104": SubmissionScope("提交时：很长的中文项目名称 · 选中 20 条 · 版本 v1", "选中的原始词条范围")
    })
    panel.chat._presentation._pending(1, 0)
    view = panel.chat._input_view
    view.upload_label.set_full_text("本次附件：中文术语参考.xlsx")
    view.upload_label.show()
    view.set_text(draft)
    panel.show()
    panel.resize(*requested_size)
    QApplication.processEvents()
    assert panel.minimumWidth() >= 480
    assert panel.minimumHeight() >= 400
    assert panel.width() >= 480
    assert panel.height() >= 400
    monitor = panel._task_monitor
    monitor.refresh([{"task_id": "task", "status": "running", "progress": {"current": 1, "total": 20}}])
    monitor._collapse_btn.click()
    splitter = monitor.parentWidget()
    splitter.setSizes([1, panel.height()])
    QApplication.processEvents()
    monkeypatch.setattr(panel.chat._controller, "_on_llm_round_start", lambda: None)
    panel.chat._controller.handle_user_message("继续处理")
    panel.chat._presentation.refresh()
    _until(lambda: panel.chat.height() >= panel.chat.minimumSizeHint().height())
    QApplication.processEvents()
    assert panel.width() == requested_size[0]
    assert panel.height() <= max(620, requested_size[1])
    navigation = panel.chat._message_list.navigation
    assert navigation.earlier.isVisible()
    controls = [
        view.input,
        view.send_button,
        view.stop_button,
        view.mode_button,
        view._upload_button,
        view._more_button,
        navigation.earlier,
    ]
    for control in controls:
        assert control.isVisible()
        ancestor = control.parentWidget()
        while ancestor is not None:
            top_left = control.mapTo(ancestor, QPoint())
            bottom_right = top_left + QPoint(control.width() - 1, control.height() - 1)
            assert ancestor.rect().contains(top_left)
            assert ancestor.rect().contains(bottom_right), (control.accessibleName(), ancestor, ancestor.size())
            if ancestor is panel:
                break
            ancestor = ancestor.parentWidget()
    assert ComposerEditor.MINIMUM_HEIGHT <= view.input.height() <= ComposerEditor.MAXIMUM_HEIGHT
    assert view.input.toPlainText() == draft


def test_normal_conversation_and_long_draft_fit_actual_480_by_400(chat_environment):
    panel = chat_environment.panel()
    _until(lambda: panel.chat.session_ready and panel._workspace.ready())
    panel.chat.load_history([{"role": "user", "content": f"History {index}"} for index in range(105)])
    panel.chat.set_input("需要保留的长要求。\n" * 20)
    panel.show()
    panel.resize(480, 400)
    QApplication.processEvents()
    assert panel.width() == 480
    assert panel.height() == 400
    view = panel.chat._input_view
    for control in (
        view.input,
        view.mode_button,
        view.send_button,
        panel.chat._message_list.navigation.earlier,
        panel._session_list.compact_add_button,
        panel._session_list.compact_sessions_button,
    ):
        assert control.isVisible()
        assert panel.rect().contains(control.mapTo(panel, QPoint()))
        assert panel.rect().contains(control.mapTo(panel, control.rect().bottomRight()))
    assert view.input.verticalScrollBar().maximum() > 0


def test_resizing_from_desktop_does_not_retain_expanded_sidebar_minimum(chat_environment, monkeypatch):
    panel = chat_environment.panel()
    _until(lambda: panel.chat.session_ready and panel._workspace.ready())
    monkeypatch.setattr(panel.chat._controller, "_on_llm_round_start", lambda: None)
    panel.chat._controller.handle_user_message("核对中文项目")
    panel.chat._presentation.refresh()
    panel.resize(1024, 760)
    panel.show()
    QApplication.processEvents()
    assert not panel._session_list._collapsed
    panel.resize(480, 620)
    _until(lambda: panel._session_list._collapsed)
    QApplication.processEvents()
    assert panel.width() == 480
    assert panel.minimumWidth() == 480
    assert panel.chat._input_view.stop_button.isVisible()
