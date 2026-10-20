"""Sidebar navigation remains readable within its viewport at supported text sizes."""

from PyQt6.QtCore import QPoint, QRect
from PyQt6.QtGui import QPalette
import pytest

from tests.ui.responsive_layout_support import apply_layout_font, settle_layout, themed_layout_app as themed_layout_app
from tests.ui.tools.smart_assistant.test_theme_migration import _snapshot
from transbridge.ui.foundation.model import ThemeScheme
from transbridge.ui.foundation.qt_palette import qcolor
from transbridge.ui.tools.smart_assistant.session_list_widget import SessionListWidget
from transbridge.ui.tools.smart_assistant.theme_support import SmartAssistantTheme


def _sidebar(theme):
    widget = SessionListWidget(theme=theme)
    widget.set_workbench("current", "main")
    widget.set_sessions([
        {
            "session_id": "active",
            "name": "New conversation",
            "project_id": "current",
            "project_name": "蕾米尔的长名称工程",
            "variant_id": "main",
            "variant_name": "默认",
            "message_count": 123456,
            "last_active_at": "2026-10-19T11:12:00",
        },
        {
            "session_id": "other",
            "name": "检查术语与翻译风格并保留完整标题",
            "project_id": "current",
            "project_name": "蕾米尔的长名称工程",
            "variant_id": "preview",
            "variant_name": "下个版本的长名称预览分支",
        },
        {"session_id": "legacy", "name": "历史会话"},
    ])
    widget.set_active("active")
    return widget


@pytest.mark.parametrize("width", [180, 200, 260])
@pytest.mark.parametrize("point_size", [10, 14])
def test_rows_and_actions_fit_narrow_sidebar_without_horizontal_clipping(layout_app, width, point_size):
    snapshot = _snapshot(ThemeScheme.LIGHT, 1)
    apply_layout_font(layout_app, snapshot, point_size)
    sidebar = _sidebar(SmartAssistantTheme(snapshot))
    sidebar.resize(width, 600)
    sidebar.show()
    settle_layout(layout_app)
    try:
        viewport = sidebar._scroll.viewport()
        assert sidebar.width() == width
        assert sidebar._scroll.horizontalScrollBar().maximum() == 0
        for row in (sidebar._rows["active"], sidebar._rows["other"]):
            bounds = QRect(row.mapTo(viewport, QPoint()), row.size())
            assert viewport.rect().contains(bounds)
            assert bounds.right() <= viewport.width() - 5
            assert row.rect().contains(row._menu_btn.geometry())
            assert row._name_label.height() >= row._name_label.fontMetrics().height()
            assert row._name_label.width() >= row._name_label.fontMetrics().horizontalAdvance("新…")
            for label in (row._variant_label, row._scope_label):
                if label.isVisible():
                    assert row.rect().contains(QRect(label.mapTo(row, QPoint()), label.size()))
        group = sidebar._groups["current"]
        assert group.header.rect().contains(group._current_label.geometry())
        assert group._name_label.width() >= group._name_label.fontMetrics().horizontalAdvance("蕾…")
    finally:
        sidebar.close()
        sidebar.deleteLater()


def test_selection_fill_changes_with_theme_and_stays_distinct_from_other_rows(layout_app):
    theme = SmartAssistantTheme(_snapshot(ThemeScheme.LIGHT, 1))
    sidebar = _sidebar(theme)
    sidebar.resize(260, 600)
    sidebar.show()
    try:
        for index, scheme in enumerate((ThemeScheme.LIGHT, ThemeScheme.DARK, ThemeScheme.LIGHT), 1):
            snapshot = _snapshot(scheme, index)
            apply_layout_font(layout_app, snapshot, 10)
            theme.update(snapshot)
            sidebar.apply_theme(theme)
            settle_layout(layout_app)
            active, other = sidebar._rows["active"], sidebar._rows["other"]
            for row in (active, other):
                row.clearFocus()
                row._hovered = False
                row.apply_theme(theme)
            selected_pixel = active.grab().toImage().pixelColor(active.width() // 2, 3)
            neutral_pixel = other.grab().toImage().pixelColor(other.width() // 2, 3)
            assert selected_pixel == snapshot.palette.color(QPalette.ColorRole.Highlight)
            assert selected_pixel != neutral_pixel
            assert active._scope_label.isHidden()
            assert other._scope_label.isVisible()
            assert other.isEnabled()
            assert sidebar._active_sid == "active"
            pixels = sidebar.grab().toImage()
            surface = qcolor(snapshot.tokens.semantic.surface_alt)
            assert pixels.pixelColor(3, 3) == surface
            assert pixels.pixelColor(3, sidebar.height() - 3) == surface
    finally:
        sidebar.close()
        sidebar.deleteLater()


def test_search_uses_visible_default_title_and_unbound_history_has_no_switch_promise(layout_app):
    sidebar = _sidebar(SmartAssistantTheme(_snapshot(ThemeScheme.LIGHT, 1)))
    try:
        assert "未绑定" in sidebar._rows["legacy"].accessibleDescription()
        assert "切换" not in sidebar._rows["legacy"].accessibleDescription()
        sidebar._search_input.setText("新对话")
        assert list(sidebar._rows) == ["active"]
        assert sidebar._rows["active"]._name_label.full_text == "新对话"
        assert "新对话" in sidebar._rows["active"].accessibleName()
    finally:
        sidebar.deleteLater()
