"""Cross-platform contracts for the assistant's standard system window frame."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QPushButton
import pytest

from tests.ui.tools.smart_assistant import test_v2_chat_recovery as fixtures
from tests.ui.tools.smart_assistant.test_theme_migration import _snapshot
from transbridge.ui.foundation.model import ThemeScheme

chat_environment = fixtures.chat_environment


@pytest.mark.parametrize("size", [(480, 400), (1024, 760)])
def test_assistant_uses_system_frame_and_internal_session_summary(chat_environment, size):
    panel = chat_environment.panel()
    fixtures._until(lambda: panel.chat.session_ready)
    panel.resize(*size)
    panel.show()
    fixtures._APP.processEvents()
    flags = panel.windowFlags()
    assert panel.titleBarWidget() is None
    assert not flags & Qt.WindowType.FramelessWindowHint
    for flag in (
        Qt.WindowType.WindowTitleHint,
        Qt.WindowType.WindowSystemMenuHint,
        Qt.WindowType.WindowMinimizeButtonHint,
        Qt.WindowType.WindowMaximizeButtonHint,
        Qt.WindowType.WindowCloseButtonHint,
    ):
        assert flags & flag
    assert panel.mask().isEmpty()
    assert panel.widget().layout().itemAt(0).widget() is panel._header
    assert not panel._header.findChildren(QPushButton)
    assert "AI 助手" in panel.windowTitle()
    assert panel.size().width() == size[0]
    assert panel.size().height() == size[1]
    panel.close()


def test_theme_change_keeps_native_frame_and_draft_selection(chat_environment):
    panel = chat_environment.panel()
    fixtures._until(lambda: panel.chat.session_ready)
    panel._on_theme_changed(_snapshot(ThemeScheme.LIGHT, 1))
    panel.chat.set_input("保留草稿和选区")
    editor = panel.chat._input_view.input
    editor.selectAll()
    before = panel._header.palette().color(QPalette.ColorRole.Window)
    flags = panel.windowFlags()
    panel._on_theme_changed(_snapshot(ThemeScheme.DARK, 2))
    assert panel._header.palette().color(QPalette.ColorRole.Window) != before
    assert editor.toPlainText() == editor.textCursor().selectedText() == "保留草稿和选区"
    assert panel.windowFlags() == flags
    assert panel.titleBarWidget() is None
    assert panel.mask().isEmpty()
