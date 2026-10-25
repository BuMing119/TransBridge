"""Large multi-select filter menus remain compact and keep all choices accessible."""

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QAbstractButton, QPushButton, QScrollArea
import pytest

from tests.ui.responsive_layout_support import settle_layout, themed_layout_app as themed_layout_app
from transbridge.ui.tools.ai_translator._rule_editor_widget import _CheckableFilterButton
from transbridge.ui.tools.ai_translator.scope_view import _filter_menu, _new_button, _style_filter


@pytest.mark.parametrize("kind", ["scope", "rule"])
@pytest.mark.parametrize("count", [3, 78])
@pytest.mark.parametrize("point_size", [9, 14])
def test_filter_popup_is_bounded_and_can_select_and_reset_last_option(layout_app, kind, count, point_size):
    selected = []
    if kind == "scope":
        anchor = QPushButton("不限")
        _filter_menu(anchor, lambda: selected.append(None))
        for index in range(count):
            button = _new_button(anchor, lambda value=index: selected.append(value))
            button.setText(f"分类 {index:03}")
            _style_filter(button, False)
    else:
        anchor = _CheckableFilterButton([(index, f"分类 {index:03}") for index in range(count)], None)
        anchor.selection_changed.connect(selected.append)
    anchor.setStyleSheet(f"QWidget {{ font-size: {point_size}pt; }}")
    anchor.show()
    menu = anchor.menu()
    try:
        menu.popup(anchor.mapToGlobal(QPoint(0, anchor.height())))
        settle_layout(layout_app)
        assert menu.height() < 500
        scroll = menu.findChild(QScrollArea)
        assert scroll is not None
        options = scroll.widget().findChildren(QAbstractButton)
        assert len(options) == count
        assert scroll.viewport().height() <= sum(button.height() for button in options[:12])
        assert scroll.verticalScrollBar().isVisible() == (count > 12)

        QTest.keyClick(menu, Qt.Key.Key_End)
        settle_layout(layout_app)
        last = options[-1]
        center = last.mapTo(scroll.viewport(), last.rect().center())
        assert scroll.viewport().rect().contains(center)
        QTest.mouseClick(last, Qt.MouseButton.LeftButton)
        assert selected[-1] == (count - 1 if kind == "scope" else {count - 1})
        menu.hide()
        menu.popup(anchor.mapToGlobal(QPoint(0, anchor.height())))
        settle_layout(layout_app)
        assert menu.height() < 500
        reset = menu.actions()[0]
        QTest.mouseClick(menu, Qt.MouseButton.LeftButton, pos=menu.actionGeometry(reset).center())
        assert selected[-1] is None
        if kind == "rule":
            assert anchor.selected is None
            assert not any(button.isChecked() for button in options)

        menu.popup(anchor.mapToGlobal(QPoint(0, anchor.height())))
        settle_layout(layout_app)
        QTest.keyClick(menu, Qt.Key.Key_End)
        QTest.keyClick(last, Qt.Key.Key_Return)
        assert selected[-1] == (count - 1 if kind == "scope" else {count - 1})
        menu.hide()
        menu.popup(anchor.mapToGlobal(QPoint(0, anchor.height())))
        settle_layout(layout_app)
        QTest.keyClick(menu, Qt.Key.Key_Home)
        QTest.keyClick(options[0], Qt.Key.Key_Up)
        QTest.keyClick(menu, Qt.Key.Key_Return)
        assert selected[-1] is None
    finally:
        menu.hide()
        anchor.close()
        anchor.deleteLater()
        settle_layout(layout_app)
