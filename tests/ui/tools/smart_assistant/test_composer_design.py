"""Real Qt coverage for persistent modes, growing input and compact navigation."""

from pathlib import Path

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QFont, QFontDatabase
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QStyle, QStyleOptionButton, QVBoxLayout, QWidget
import pytest

from transbridge.ui.tools.smart_assistant.composer_editor import ComposerEditor
from transbridge.ui.tools.smart_assistant.input_view import ChatInputView
from transbridge.ui.tools.smart_assistant.panel_header import AssistantPanelHeader
from transbridge.ui.tools.smart_assistant.session_list_widget import SessionListWidget


@pytest.fixture
def qapp():
    application = QApplication.instance() or QApplication([])
    previous_font = application.font()
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    font_id = QFontDatabase.addApplicationFont(str(font_path)) if font_path.exists() else -1
    if font_id >= 0:
        application.setFont(QFont("Microsoft YaHei", 9))
    yield application
    application.setFont(previous_font)
    if font_id >= 0:
        QFontDatabase.removeApplicationFont(font_id)


def _composer(qapp, automatic=False):
    host = QWidget()
    layout = QVBoxLayout(host)
    modes, sent = [], []
    view = ChatInputView(
        set_input=lambda _: None,
        select_skill=lambda _: None,
        upload=lambda: None,
        clear=lambda: None,
        send=lambda: sent.append(view.input.toPlainText()),
        toggle_auto=modes.append,
        auto_mode=automatic,
    )
    view.build_toolbar(layout)
    view.build_editor(layout, host)
    host.resize(400, 300)
    host.show()
    qapp.processEvents()
    return host, view, modes, sent


def test_mode_is_always_visible_and_reflects_restore_without_emitting_change(qapp):
    host, view, modes, _sent = _composer(qapp, automatic=True)
    try:
        assert view.mode_button.isVisible()
        assert view.mode_button.text() == "自动执行"
        assert "自动执行常规操作" in view.mode_button.accessibleDescription()
        assert "高权限" in view.mode_button.toolTip() and "执行计划" in view.mode_button.toolTip()
        view.mode_button.confirm_action.trigger()
        assert modes == [False]
        assert view.mode_button.text() == "先确认"
        assert "执行前确认" in view.mode_button.toolTip()
        view.auto_checkbox.blockSignals(True)
        view.auto_checkbox.setChecked(True)
        view.auto_checkbox.blockSignals(False)
        view.refresh_mode()
        assert view.mode_button.text() == "自动执行"
        assert modes == [False]
    finally:
        host.close()


def test_editor_grows_shrinks_and_keeps_controls_visible_with_followup(qapp):
    host, view, _modes, sent = _composer(qapp)
    try:
        compact = view.input.height()
        assert compact == ComposerEditor.MINIMUM_HEIGHT
        view.set_text("第一行\n第二行\n第三行")
        qapp.processEvents()
        assert compact < view.input.height() <= ComposerEditor.MAXIMUM_HEIGHT
        long_request = "需要保留的中文要求\n" * 600
        view.set_text(long_request)
        view.set_running(True)
        qapp.processEvents()
        assert view.input.height() == ComposerEditor.MAXIMUM_HEIGHT
        assert view.input.toPlainText() == long_request
        assert view.send_button.text() == "发送补充"
        assert view.stop_button.isVisible()
        view.send_button.click()
        assert sent == [long_request]
        for control in (view.input, view.send_button, view.stop_button, view.mode_button):
            assert host.rect().contains(control.mapTo(host, QPoint()))
            assert host.rect().contains(control.mapTo(host, control.rect().bottomRight()))
        view.input.clear()
        qapp.processEvents()
        assert view.input.height() == compact
        assert not view.send_button.isEnabled()
    finally:
        host.close()


def test_collapsed_sidebar_retains_new_and_session_actions(qapp):
    sidebar = SessionListWidget()
    created = []
    sidebar.create_session.connect(created.append)
    sidebar.set_collapsed(True)
    sidebar.resize(48, 400)
    sidebar.show()
    qapp.processEvents()
    try:
        assert sidebar.compact_add_button.isVisible()
        assert sidebar.compact_sessions_button.isVisible()
        assert not sidebar.compact_add_button.icon().isNull()
        assert not sidebar.compact_sessions_button.icon().isNull()
        sidebar.compact_add_button.click()
        assert created == ["新对话"]
        sidebar.compact_sessions_button.click()
        assert not sidebar._collapsed
        assert sidebar._header.isVisible()
    finally:
        sidebar.close()


def test_long_draft_uses_internal_scroll_instead_of_growing_compact_window(qapp):
    host, view, _modes, _sent = _composer(qapp)
    try:
        host.resize(400, 210)
        qapp.processEvents()
        height = host.height()
        request = "Keep this line in the draft.\n" * 20
        view.set_text(request)
        qapp.processEvents()
        assert host.height() == height
        assert ComposerEditor.MINIMUM_HEIGHT <= view.input.height() < ComposerEditor.MAXIMUM_HEIGHT
        assert view.input.verticalScrollBar().maximum() > 0
        assert view.input.toPlainText() == request
        assert view.send_button.isVisible()
    finally:
        host.close()


def test_header_keeps_current_session_identity_at_compact_width(qapp):
    header = AssistantPanelHeader()
    name = "检查中文地名和术语一致性的会话" * 4
    header.set_session_name(name)
    header.set_model_name("provider-long-model-identifier")
    header.resize(480, 44)
    header.show()
    qapp.processEvents()
    try:
        assert header._title.full_text == name
        assert header._title.toolTip() == name
        assert header._title.width() > 0
        assert not header.model_configured
        assert header.width() == 480
        assert header.height() == 44
        assert header._title.geometry().center().y() == header._model.geometry().center().y()
        for control in (header._title, header._model, header._minimize_button, header._close_button):
            assert header.rect().contains(control.geometry())
        header.set_model_name("")
        assert not header.model_configured
    finally:
        header.close()


@pytest.mark.parametrize("automatic", [False, True])
def test_compact_composer_has_one_toolbar_row_and_retains_all_running_actions(qapp, automatic):
    host, view, _modes, _sent = _composer(qapp, automatic=automatic)
    try:
        host.resize(400, 190)
        view.set_text("请核对所有中文地名，完整保留这些输入。\n" * 20)
        view.set_running(True)
        view.upload_label.set_full_text("本次附件：中文术语参考.xlsx")
        view.upload_label.show()
        qapp.processEvents()
        assert host.width() == 400
        controls = [view._upload_button, view.mode_button, view._more_button, view.stop_button, view.send_button]
        assert len({control.geometry().center().y() for control in controls}) == 1
        for index, control in enumerate(controls):
            assert control.isVisible()
            assert view._card.rect().contains(control.geometry())
            assert control.geometry().top() > view.input.geometry().bottom()
            option = QStyleOptionButton()
            control.initStyleOption(option)
            available = control.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, option, control)
            text_width = control.fontMetrics().horizontalAdvance(control.text())
            icon_width = control.iconSize().width() + (4 if control.text() else 0) if not control.icon().isNull() else 0
            assert available.width() >= text_width + icon_width
            if index:
                assert controls[index - 1].geometry().right() < control.geometry().left()
        assert not view._upload_button.icon().isNull()
        assert view._upload_button.accessibleName() == "上传参考文件"
        assert not view._more_button.icon().isNull()
        assert view._more_button.accessibleName() == "更多输入操作"
        assert view.input.verticalScrollBar().maximum() > 0
    finally:
        host.close()


def test_running_empty_composer_emphasizes_stop_then_followup_when_ready(qapp):
    host, view, _modes, sent = _composer(qapp)
    stopped = []
    view.configure_actions(stop=lambda: stopped.append(True), details=lambda: None)
    try:
        view.set_running(True)
        assert view.stop_button.property("tbSemanticState") == "primary"
        assert view.send_button.property("tbSemanticState") == "muted"
        assert not view.send_button.isEnabled()
        view.stop_button.click()
        assert stopped == [True]
        view.set_text("保留原来的标点")
        assert view.send_button.property("tbSemanticState") == "primary"
        assert view.stop_button.property("tbSemanticState") == "muted"
        assert view.send_button.text() == "发送补充"
        view.send_button.click()
        assert sent == ["保留原来的标点"]
        view.set_preparing_attachments(True)
        assert not view.send_button.isEnabled()
        assert view.stop_button.property("tbSemanticState") == "primary"
        view.set_preparing_attachments(False)
        assert view.send_button.isEnabled()
        view.set_running(False)
        assert not view.stop_button.isVisible()
        assert view.send_button.text() == "发送"
    finally:
        host.close()


def test_tab_reaches_composer_actions_and_shift_tab_returns_without_editing_draft(qapp):
    host, view, _modes, sent = _composer(qapp)
    draft = "保留这段尚未发送的说明"
    view.set_text(draft)
    view.input.setFocus()
    qapp.processEvents()
    try:
        QTest.keyClick(view.input, Qt.Key.Key_Tab)
        assert not view.input.hasFocus()
        assert view.input.toPlainText() == draft
        assert not sent
        QTest.keyClick(qapp.focusWidget(), Qt.Key.Key_Backtab)
        assert view.input.hasFocus()
        assert view.input.toPlainText() == draft
    finally:
        host.close()
