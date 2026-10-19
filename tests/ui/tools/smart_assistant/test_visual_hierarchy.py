"""Verify the painted assistant surfaces against the shared application skin."""

from PyQt6.QtCore import QCoreApplication, QEvent, QPoint
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QPushButton
import pytest

from tests.ui.responsive_layout_support import settle_layout, themed_layout_app as themed_layout_app
from tests.ui.tools.smart_assistant import test_v2_chat_recovery as fixtures
from tests.ui.tools.smart_assistant.test_conversation_layout import _request
from tests.ui.tools.smart_assistant.test_theme_migration import _FakeThemeService, _snapshot
from tests.ui.tools.smart_assistant.test_v2_chat_recovery import _until
from transbridge.ui.foundation.adapters import ThemeView
from transbridge.ui.foundation.components import ComponentKind, ComponentStyle, SemanticState
from transbridge.ui.foundation.model import ThemeScheme
from transbridge.ui.foundation.visual_style import compile_application_stylesheet
from transbridge.ui.tools.smart_assistant.panel import SmartAssistantPanel
from transbridge.ui.tools.smart_assistant.request_progress_view import RequestProgressCard

chat_environment = fixtures.chat_environment


@pytest.mark.parametrize("scheme", [ThemeScheme.LIGHT, ThemeScheme.DARK])
def test_window_sections_and_primary_actions_share_application_style(layout_app, chat_environment, monkeypatch, scheme):
    snapshot = _snapshot(scheme, 1)
    layout_app.setPalette(snapshot.palette)
    layout_app.setStyleSheet(compile_application_stylesheet(snapshot))
    theme_view = ThemeView(_FakeThemeService(snapshot))
    original = SmartAssistantPanel.__init__
    monkeypatch.setattr(
        SmartAssistantPanel,
        "__init__",
        lambda self, *args, **kwargs: original(self, *args, **kwargs, theme_view=theme_view),
    )
    panel = chat_environment.panel()
    _until(lambda: panel.chat.session_ready)
    panel.chat.load_history([
        {"role": "user", "content": "你好", "message_id": "message-a"},
        {"role": "assistant", "content": "你好！可以帮你检查翻译与术语。", "message_id": "message-b"},
    ])
    panel._session_list.set_sessions([{"session_id": "current", "name": "翻译检查", "message_count": 2}])
    panel._session_list.set_active("current")
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    progress = RequestProgressCard(
        _request(), control=lambda *_: None, timeline=lambda *_: None, undo=lambda: None, theme=panel._theme
    )
    panel.chat._message_list.add_widget(progress)
    panel.chat._presentation.refresh()
    composer = panel.chat._input_view
    composer.set_text("继续检查术语")
    shared = QPushButton("共享主按钮")
    ComponentStyle.apply_static(shared, ComponentKind.BUTTON)
    ComponentStyle.apply_state(shared, SemanticState.PRIMARY)
    shared.resize(140, 36)
    shared.show()
    panel.resize(1040, 740)
    panel.show()
    settle_layout(layout_app)
    try:
        pixels = panel.grab().toImage()

        def color(widget, x, y):
            return pixels.pixelColor(widget.mapTo(panel, QPoint(x, y)))

        border = snapshot.palette.color(QPalette.ColorRole.Mid)
        accent = snapshot.palette.color(QPalette.ColorRole.Link)
        assert color(panel._header, panel._header.width() // 2, 0) == border
        assert color(panel._body, 0, panel._body.height() // 2) == border
        sidebar = panel._session_list
        assert color(sidebar, sidebar.width() - 1, sidebar.height() // 2) == border
        row = sidebar._rows["current"]
        assert color(row, 1, row.height() // 2) == accent
        assert color(progress, 0, progress.height() // 2) == border
        # Compare actual pixels, since the former palette-only accent was reset by QSS.
        assert color(composer.send_button, 8, 8) == shared.grab().toImage().pixelColor(8, 8) == accent
        assert composer.send_button.palette().color(QPalette.ColorRole.ButtonText) == shared.palette().color(
            QPalette.ColorRole.ButtonText
        )
        assert composer.input.font().family() == panel._header._title.font().family() == "Microsoft YaHei UI"
        assert composer.input.toPlainText() == "继续检查术语"

        panel.resize(480, 400)
        settle_layout(layout_app)
        assert panel.size().width() == 480 and panel.size().height() == 400
        assert sidebar._collapsed
        for control in (composer.input, composer.send_button, composer.mode_button):
            assert panel.rect().contains(control.mapTo(panel, control.rect().bottomRight()))
    finally:
        shared.close()
        shared.deleteLater()
        panel.dispose()
        panel.close()
        theme_view.close()
