"""Existing chat surfaces follow the theme without freezing old palette roles."""

from types import SimpleNamespace

from PyQt6.QtCore import QCoreApplication, QEvent, QPoint
from PyQt6.QtGui import QPalette
import pytest

from tests.ui.responsive_layout_support import apply_layout_font, settle_layout, themed_layout_app as themed_layout_app
from tests.ui.tools.smart_assistant import test_v2_chat_recovery as fixtures
from tests.ui.tools.smart_assistant.test_v2_chat_recovery import _until
from transbridge.config.ui_preferences import DEFAULT_THEME_ID, ThemeMode
from transbridge.ui.foundation.adapters import ThemeView
from transbridge.ui.foundation.builtins import create_builtin_registry
from transbridge.ui.foundation.qt_palette import qcolor
from transbridge.ui.foundation.theme_service import ThemePreference, ThemeService
from transbridge.ui.tools.smart_assistant.panel import SmartAssistantPanel

chat_environment = fixtures.chat_environment


@pytest.mark.parametrize("point_size", [10, 14])
def test_same_chat_window_repaints_surfaces_and_keeps_draft(layout_app, chat_environment, monkeypatch, point_size):
    service = ThemeService(
        layout_app,
        create_builtin_registry(),
        SimpleNamespace(
            load=lambda: SimpleNamespace(theme_mode=ThemeMode.LIGHT, theme_id=DEFAULT_THEME_ID, diagnostics=())
        ),
    )
    service.start()
    # Select the real application text size before constructing local palette
    # consumers; changing QSS after their theme callbacks would reset their palettes.
    apply_layout_font(layout_app, service.snapshot(), point_size)
    stylesheet = layout_app.styleSheet()
    monkeypatch.setattr(service, "_compiled_stylesheet", lambda *_args: stylesheet)
    theme_view = ThemeView(service)
    original = SmartAssistantPanel.__init__
    monkeypatch.setattr(
        SmartAssistantPanel,
        "__init__",
        lambda self, *args, **kwargs: original(self, *args, **kwargs, theme_view=theme_view),
    )
    panel = chat_environment.panel()
    # The initial asynchronous restore must finish before installing synthetic history.
    _until(lambda: panel.chat.session_ready and panel._workspace.ready())
    panel.chat.load_history([{"role": "user", "content": "请检查本批合成译文。", "message_id": "one"}])
    panel.chat._input_view.set_text("保留尚未发送的补充说明")
    bubble = panel.chat._message_list._owned_widgets[0]
    panel.show()
    settle_layout(layout_app)
    try:
        for mode in (ThemeMode.LIGHT, ThemeMode.DARK, ThemeMode.LIGHT):
            service.set_preference(ThemePreference(mode, DEFAULT_THEME_ID), persist=False)
            panel.resize(720, 560)
            settle_layout(layout_app)
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            settle_layout(layout_app)
            assert panel.chat._input_view.input.font().pointSizeF() == point_size
            assert panel.chat._input_view.input.toPlainText() == "保留尚未发送的补充说明"
            pixels = panel.grab().toImage()
            expected = qcolor(service.snapshot().tokens.semantic.surface_alt)
            position = bubble._content_wrapper.mapTo(panel, QPoint(3, 3))
            assert pixels.pixelColor(position).name() == expected.name()
            title = panel._session_list._title
            assert title.palette().color(QPalette.ColorRole.WindowText) == qcolor(
                service.snapshot().tokens.semantic.text_primary
            )
            position = panel._session_list._header.mapTo(panel, QPoint(3, 3))
            assert pixels.pixelColor(position).name() == expected.name()
    finally:
        panel.dispose()
        panel.close()
        panel.deleteLater()
        theme_view.close()
        service.close()
