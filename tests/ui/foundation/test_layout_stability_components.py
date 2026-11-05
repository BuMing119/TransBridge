from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QPushButton, QVBoxLayout, QWidget
import pytest

from tests.ui.responsive_layout_support import settle_layout, themed_layout_app as themed_layout_app
from transbridge.ui.foundation.components import ElidedLabel, WrappingLabel, reserve_text_width

_APP = QApplication.instance() or QApplication([])


@pytest.mark.parametrize("text", ["FileName" * 25, "<b>literal path</b>" + "X" * 200, "目录" * 100])
def test_wrapping_label_preserves_text_and_allocates_height_without_widening_window(layout_app, text):
    view = QWidget()
    layout = QVBoxLayout(view)
    label = WrappingLabel("short", view)
    layout.addWidget(label)
    layout.addStretch()
    view.resize(400, 400)
    view.show()
    try:
        settle_layout(layout_app)
        minimum = view.minimumSizeHint().width()
        label.setText(text)
        settle_layout(layout_app)
        assert view.width() == 400
        assert view.minimumSizeHint().width() == minimum
        heights = []
        for width in (400, 250, 600):
            view.resize(width, 400)
            settle_layout(layout_app)
            assert view.width() == width
            assert label.text() == text
            assert label.textFormat() == Qt.TextFormat.PlainText
            assert label.height() >= label.heightForWidth(label.width())
            heights.append(label.heightForWidth(label.width()))
        assert heights[1] > heights[2]
        label.setText("short")
        settle_layout(layout_app)
        assert label.heightForWidth(label.width()) < heights[1]
    finally:
        view.close()


def test_elided_label_keeps_full_text_without_contributing_a_variable_minimum_width() -> None:
    label = ElidedLabel("短文本")
    label.resize(120, 24)
    label.show()
    _APP.processEvents()
    short_minimum_width = label.minimumWidth()

    full_text = "一个足以超过当前稳定布局配额的超长运行时状态说明" * 8
    label.set_full_text(full_text)
    _APP.processEvents()

    assert label.minimumWidth() == short_minimum_width == 0
    assert label.full_text == full_text
    assert label.accessibleDescription() == full_text
    assert label.text() != full_text
    assert "…" in label.text()
    label.close()


def test_reserve_text_width_keeps_button_width_stable_across_known_states() -> None:
    button = QPushButton("保存")
    reserved = reserve_text_width(button, ("保存", "已保存", "重试保存"))
    button.show()
    _APP.processEvents()

    widths = []
    for text in ("保存", "已保存", "重试保存"):
        button.setText(text)
        button.adjustSize()
        widths.append(button.width())

    assert reserved > 0
    assert widths == [reserved, reserved, reserved]
    button.close()
