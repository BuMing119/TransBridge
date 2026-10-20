"""Keyboard navigation keeps the draft intact across composer controls."""

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtTest import QTest
import pytest

from tests.ui.responsive_layout_support import settle_layout, themed_layout_app as themed_layout_app
from tests.ui.tools.smart_assistant import test_v2_chat_recovery as fixtures
from tests.ui.tools.smart_assistant.test_v2_chat_recovery import _until
from transbridge.ui.tools.smart_assistant.chat_widget import ChatWidget

chat_environment = fixtures.chat_environment


@pytest.mark.parametrize("automatic", [False, True])
def test_composer_keyboard_order_menu_escape_and_send_shortcut(layout_app, chat_environment, monkeypatch, automatic):
    sent, stopped = [], []
    monkeypatch.setattr(ChatWidget, "_on_send", lambda self: sent.append(self._input.toPlainText()))
    panel = chat_environment.panel()
    # Session discovery is asynchronous. Let its controller restoration finish
    # before entering the running state used to exercise the Tab order.
    _until(lambda: panel.chat.session_ready and panel._workspace.ready() and not panel._sessions.directory.pending)
    panel.resize(720, 560)
    panel.show()
    panel.activateWindow()
    _until(panel.isActiveWindow)
    view = panel.chat._input_view
    view.set_text("保留补充说明")
    view.configure_actions(stop=lambda: stopped.append(True), details=lambda: None)
    monkeypatch.setattr(panel.chat._controller, "_on_llm_round_start", lambda: None)
    panel.chat._controller.handle_user_message("Keyboard navigation exercise")
    # The periodic presenter derives composer state from the controller. Refresh
    # it explicitly so the test does not depend on beating its 150 ms timer.
    panel.chat._presentation.refresh()
    assert view.stop_button.isVisible() and view.stop_button.isEnabled()
    settle_layout(layout_app)

    QTest.keyClick(view.input, Qt.Key.Key_Return)
    draft = view.input.toPlainText()
    assert "\n" in draft and not sent
    QTest.keyClick(view.input, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert sent == [draft]
    sent.clear()

    QTest.keyClick(view.input, Qt.Key.Key_Tab)
    assert view.mode_button.hasFocus()
    menu_choices = []

    def navigate_menu():
        menu_was_visible = view.mode_button.menu.isVisible()
        if automatic:
            view.mode_button.menu.setActiveAction(view.mode_button.confirm_action)
            QTest.keyClick(view.mode_button.menu, Qt.Key.Key_Down)
            QTest.keyClick(view.mode_button.menu, Qt.Key.Key_Return)
        else:
            QTest.keyClick(view.mode_button.menu, Qt.Key.Key_Escape)
        menu_choices.append((menu_was_visible, view.auto_checkbox.isChecked()))

    QTimer.singleShot(0, navigate_menu)
    QTest.keyClick(view.mode_button, Qt.Key.Key_Space)
    _until(lambda: bool(menu_choices))
    settle_layout(layout_app)
    assert not view.mode_button.menu.isVisible()
    assert menu_choices == [(True, automatic)]
    assert view.auto_checkbox.isChecked() is automatic
    assert panel.focusWidget() is view.mode_button
    if layout_app.platformName() == "offscreen":
        # The offscreen plugin does not reactivate a window after closing a popup.
        panel.activateWindow()
        settle_layout(layout_app)
    assert view.mode_button.hasFocus()
    for button in (view._more_button, view.stop_button, view.send_button):
        QTest.keyClick(layout_app.focusWidget(), Qt.Key.Key_Tab)
        assert button.hasFocus()
    QTest.keyClick(view.send_button, Qt.Key.Key_Space)
    assert sent == [draft]
    assert view.input.toPlainText() == draft
    QTest.keyClick(view.send_button, Qt.Key.Key_Backtab)
    assert view.stop_button.hasFocus()
    QTest.keyClick(view.stop_button, Qt.Key.Key_Space)
    assert stopped == [True]
    assert view.input.toPlainText() == draft
    for button in (view._more_button, view.mode_button, view.input):
        QTest.keyClick(layout_app.focusWidget(), Qt.Key.Key_Backtab)
        assert button.hasFocus()
    QTest.keyClick(view.input, Qt.Key.Key_Escape)
    assert panel.isVisible() and view.input.toPlainText() == draft
