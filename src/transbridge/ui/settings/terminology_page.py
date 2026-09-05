"""Global terminology and dictionary defaults page."""

from __future__ import annotations

from PyQt6.QtWidgets import QAbstractItemView, QFormLayout, QLineEdit, QListWidget, QListWidgetItem, QSpinBox

from .page_common import SettingsPage, apply_if_present

_SOURCES = (
    ("dynamic", "动态术语库"),
    ("paratranz", "ParaTranz 术语"),
    ("json", "本地 JSON"),
    ("csv", "本地 CSV"),
    ("excel", "本地 Excel"),
)


class TerminologySettingsPage(SettingsPage):
    def __init__(self, config: object, parent=None) -> None:
        super().__init__(parent)
        self._config = config
        form = QFormLayout(self)
        self.priority_list = QListWidget(self)
        self.priority_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        labels = dict(_SOURCES)
        priorities = list(getattr(config, "term_priority", ()) or ())
        for key in (*priorities, *(key for key, _label in _SOURCES if key not in priorities)):
            if key in labels:
                item = QListWidgetItem(labels[key])
                item.setData(256, key)
                self.priority_list.addItem(item)
        form.addRow("来源优先级", self.priority_list)
        self.json_edit = QLineEdit(str(getattr(config, "local_json_path", "") or ""), self)
        form.addRow("本地 JSON", self.json_edit)
        self.csv_edit = QLineEdit(str(getattr(config, "local_csv_path", "") or ""), self)
        form.addRow("本地 CSV", self.csv_edit)
        self.excel_edit = QLineEdit(str(getattr(config, "local_excel_path", "") or ""), self)
        form.addRow("本地 Excel", self.excel_edit)
        self.original_col_edit = QLineEdit(str(getattr(config, "excel_original_col", "A") or "A"), self)
        self.original_col_edit.setMaximumWidth(80)
        form.addRow("Excel 原文列", self.original_col_edit)
        self.translation_col_edit = QLineEdit(str(getattr(config, "excel_translation_col", "B") or "B"), self)
        self.translation_col_edit.setMaximumWidth(80)
        form.addRow("Excel 译文列", self.translation_col_edit)
        self.max_terms_spin = QSpinBox(self)
        self.max_terms_spin.setRange(1, 10_000)
        self.max_terms_spin.setValue(int(getattr(config, "max_terms_per_batch", 50) or 50))
        form.addRow("每批术语上限", self.max_terms_spin)

    def apply_to_draft(self) -> None:
        cfg = self._config
        cfg.term_priority = [self.priority_list.item(index).data(256) for index in range(self.priority_list.count())]
        apply_if_present(cfg, "local_json_path", self.json_edit.text().strip())
        apply_if_present(cfg, "local_csv_path", self.csv_edit.text().strip())
        apply_if_present(cfg, "local_excel_path", self.excel_edit.text().strip())
        apply_if_present(cfg, "excel_original_col", self.original_col_edit.text().strip().upper())
        apply_if_present(cfg, "excel_translation_col", self.translation_col_edit.text().strip().upper())
        apply_if_present(cfg, "max_terms_per_batch", self.max_terms_spin.value())


__all__ = ["TerminologySettingsPage"]
