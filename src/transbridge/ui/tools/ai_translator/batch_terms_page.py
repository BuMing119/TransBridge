"""Terminology overrides and source summary for a batch AI task."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFormLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from transbridge.ui.foundation.components import ComponentKind, ComponentStyle

_SOURCE_LABELS = {
    "dynamic": "动态术语库",
    "paratranz": "ParaTranz 项目术语",
    "json": "本地 JSON",
    "csv": "本地 CSV",
    "excel": "本地 Excel",
}


class BatchTermsPage(QWidget):
    def __init__(self, config: object, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        title = QLabel("术语库", self)
        title.setProperty("tbTaskSectionTitle", True)
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)
        hint = QLabel("拖拽调整本次任务的术语来源优先级；全局文件路径不会在这里被修改。", self)
        hint.setProperty("tbTaskHint", True)
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.retrieval_enabled = QCheckBox("启用术语检索", self)
        self.retrieval_enabled.setChecked(bool(getattr(config, "retrieval_enabled", True)))
        self.semantic_enabled = QCheckBox("启用语义匹配", self)
        self.semantic_enabled.setChecked(bool(getattr(config, "enable_semantic_match", True)))
        layout.addWidget(self.retrieval_enabled)
        layout.addWidget(self.semantic_enabled)

        self.priority_list = QListWidget(self)
        self.priority_list.setProperty("tbTaskList", True)
        self.priority_list.setAccessibleName("本次术语来源优先级")
        self.priority_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.priority_list.setMaximumHeight(150)
        priorities = list(getattr(config, "term_priority", ()))
        for key in priorities + [key for key in _SOURCE_LABELS if key not in priorities]:
            item = QListWidgetItem(f"{_SOURCE_LABELS.get(key, key)} · {self._source_state(config, key)}")
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.priority_list.addItem(item)
        layout.addWidget(self.priority_list)

        form = QFormLayout()
        self.max_terms = QSpinBox(self)
        ComponentStyle.apply_static(self.max_terms, ComponentKind.INPUT)
        self.max_terms.setRange(10, 500)
        self.max_terms.setValue(max(10, int(getattr(config, "max_terms_per_batch", 50))))
        self.max_terms.setToolTip("每个请求最多携带的术语数量")
        form.addRow("每批术语上限", self.max_terms)
        layout.addLayout(form)
        layout.addStretch(1)

        self.retrieval_enabled.toggled.connect(self._update_enabled)
        self._update_enabled(self.retrieval_enabled.isChecked())

    def apply_to(self, config: object) -> None:
        config.retrieval_enabled = self.retrieval_enabled.isChecked()
        config.enable_semantic_match = self.semantic_enabled.isChecked()
        config.max_terms_per_batch = self.max_terms.value()
        config.term_priority = [
            str(self.priority_list.item(index).data(Qt.ItemDataRole.UserRole))
            for index in range(self.priority_list.count())
        ]

    def _update_enabled(self, enabled: bool) -> None:
        self.semantic_enabled.setEnabled(enabled)
        self.priority_list.setEnabled(enabled)
        self.max_terms.setEnabled(enabled)

    @staticmethod
    def _source_state(config: object, key: str) -> str:
        if key == "dynamic":
            return "按插件自动读取"
        if key == "paratranz":
            return "按当前工程绑定"
        path = str(getattr(config, f"local_{key}_path", "") or "")
        return Path(path).name if path else "未配置"


__all__ = ["BatchTermsPage"]
