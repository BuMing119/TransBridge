from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QPushButton

from transbridge.ui.foundation.components import ElidedLabel, reserve_text_width

_APP = QApplication.instance() or QApplication([])


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
