"""
翻译目标选择对话框。

用户选择翻译当前插件还是批量翻译所有已加载插件。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from transbridge.ui.foundation.components import ElidedLabel

if TYPE_CHECKING:
    from transbridge.ui.context import AppContext


class _TranslationTargetDialog(QDialog):
    """翻译目标选择对话框：翻译当前插件 / 批量翻译已加载插件。"""

    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self.setWindowTitle("选择翻译目标")
        self.setMinimumWidth(380)
        self.setModal(True)

        self._init_ui()
        self._update_stats()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # 当前插件选项
        self._rb_current = QRadioButton("翻译当前插件")
        self._rb_current.setChecked(True)
        layout.addWidget(self._rb_current)

        current_info = QWidget()
        current_layout = QHBoxLayout(current_info)
        current_layout.setContentsMargins(20, 0, 0, 0)
        self._current_label = ElidedLabel()
        self._current_label.setAccessibleName("当前翻译内容")
        current_layout.addWidget(self._current_label, 1)
        layout.addWidget(current_info)

        # 间隔
        layout.addSpacing(8)

        # 批量翻译选项
        self._rb_batch = QRadioButton("批量翻译已加载插件")
        layout.addWidget(self._rb_batch)

        batch_info = QWidget()
        batch_layout = QHBoxLayout(batch_info)
        batch_layout.setContentsMargins(20, 0, 0, 0)
        self._batch_label = ElidedLabel()
        self._batch_label.setAccessibleName("批量翻译范围")
        batch_layout.addWidget(self._batch_label, 1)
        layout.addWidget(batch_info)

        # 按钮组
        self._btn_group = QButtonGroup(self)
        self._btn_group.addButton(self._rb_current, 0)
        self._btn_group.addButton(self._rb_batch, 1)

        # 按钮
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btn_box.button(QDialogButtonBox.StandardButton.Ok).setText("下一步")
        btn_box.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _update_stats(self):
        """更新统计信息。"""
        slots = self._ctx.slots

        # 当前插件信息
        active_slot = self._ctx.active_slot
        if active_slot:
            total = len(active_slot.collection) if active_slot.collection else 0
            untranslated = sum(1 for e in (active_slot.collection or []) if not e.translation or e.stage == 0)
            name = active_slot.label or Path(active_slot.esp_path or "").stem
            self._set_label_text(self._current_label, f"{name} — {total} 条（未翻 {untranslated} 条）")
            self._rb_current.setEnabled(True)
        else:
            self._set_label_text(self._current_label, "未加载插件")
            self._rb_current.setEnabled(False)
            self._rb_batch.setChecked(True)

        # 批量翻译信息
        if slots:
            total_plugins = len(slots)
            total_entries = 0
            total_untranslated = 0
            for slot in slots.values():
                if slot.collection:
                    total_entries += len(slot.collection)
                    total_untranslated += sum(1 for e in slot.collection if not e.translation or e.stage == 0)
            self._set_label_text(
                self._batch_label,
                f"共 {total_plugins} 个插件，{total_entries} 条（未翻 {total_untranslated} 条）",
            )
            self._rb_batch.setEnabled(True)
        else:
            self._set_label_text(self._batch_label, "无已加载插件")
            self._rb_batch.setEnabled(False)

        # 如果只有一个插件，禁用批量选项
        if len(slots) <= 1:
            self._rb_batch.setEnabled(False)
            self._rb_current.setChecked(True)

    def is_batch_mode(self) -> bool:
        """返回是否选择批量翻译模式。"""
        return self._rb_batch.isChecked()

    @staticmethod
    def _set_label_text(label: ElidedLabel, text: str) -> None:
        label.set_full_text(text)
        label.setToolTip(text)
        label.setAccessibleDescription(text)
