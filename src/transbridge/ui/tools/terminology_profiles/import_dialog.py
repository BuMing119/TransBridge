"""Preview one terminology-source snapshot before creating a naming scheme."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from transbridge.application.terminology.identity import normalize_original
from transbridge.ui.foundation.components import ComponentKind, ComponentStyle


class TerminologySourceImportDialog(QDialog):
    """Explain the snapshot boundary and show the complete generated mapping."""

    _MAX_VISIBLE_ROWS = 500

    def __init__(self, preview, default_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.preview = preview
        self.setWindowTitle("从术语来源创建译名方案")
        self.setAccessibleName("术语来源转译名方案预览")
        self.resize(980, 680)
        layout = QVBoxLayout(self)

        explanation = QLabel(
            "将以当前项目术语为完整基础，只采用来源中可唯一对应的译名。"
            "创建的是当前读取结果的独立副本，来源以后变化不会自动修改这个方案。",
            self,
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        form = QFormLayout()
        source_label = QLabel(preview.source.source_label, self)
        source_label.setTextInteractionFlags(source_label.textInteractionFlags())
        form.addRow("术语来源", source_label)
        self.name_edit = QLineEdit(default_name, self)
        self.name_edit.setAccessibleName("新译名方案名称")
        ComponentStyle.apply_static(self.name_edit, ComponentKind.INPUT)
        form.addRow("方案名称", self.name_edit)
        layout.addLayout(form)

        self.summary_label = QLabel(self._summary_text(), self)
        self.summary_label.setWordWrap(True)
        self.summary_label.setAccessibleName("术语来源导入统计")
        layout.addWidget(self.summary_label)

        self.table = QTableWidget(0, 4, self)
        self.table.setAccessibleName("译名方案创建预览")
        self.table.setHorizontalHeaderLabels(["原文术语", "当前项目译名", "新方案译名", "处理结果"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        header = self.table.horizontalHeader()
        for column in range(3):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        ComponentStyle.apply_static(self.table, ComponentKind.TABLE)
        layout.addWidget(self.table, 1)
        self._render_rows()

        self.select_check = QCheckBox("创建后设为当前方案（与工作台同步）", self)
        self.select_check.setChecked(False)
        layout.addWidget(self.select_check)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel, self)
        self.create_button = QPushButton("创建方案", self)
        self.create_button.setDefault(True)
        ComponentStyle.apply_static(self.create_button, ComponentKind.BUTTON)
        buttons.addButton(self.create_button, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.name_edit.textChanged.connect(lambda text: self.create_button.setEnabled(bool(text.strip())))
        layout.addWidget(buttons)

    @property
    def profile_name(self) -> str:
        return self.name_edit.text().strip()

    @property
    def select_after_create(self) -> bool:
        return self.select_check.isChecked()

    def _summary_text(self) -> str:
        preview = self.preview
        adopted = preview.matched_term_count - preview.conflict_count
        kept = preview.base_mapping_count - adopted
        return (
            f"读取 {preview.source_entry_count} 条（{preview.source_term_count} 个术语）；"
            f"采用来源译名 {adopted} 个，保持当前项目译名 {kept} 个；"
            f"冲突或作用域不明确 {preview.conflict_count} 个，来源独有且未导入 {preview.source_only_term_count} 个。"
            f"合并完全重复记录 {preview.duplicate_entry_count} 条。"
        )

    def _render_rows(self) -> None:
        conflicts = {item.normalized_original: item for item in self.preview.conflicts}
        source_originals = {normalize_original(item.original) for item in self.preview.source.entries}
        mappings = self.preview.content.mappings[: self._MAX_VISIBLE_ROWS]
        self.table.setRowCount(len(mappings))
        for row, mapping in enumerate(mappings):
            conflict = conflicts.get(normalize_original(mapping.original))
            if conflict is not None and conflict.kind.value == "source_translations":
                result = f"来源译名冲突（{' / '.join(conflict.source_translations)}），保持当前"
            elif conflict is not None and conflict.kind.value == "base_scopes":
                result = f"作用域不明确（{' / '.join(conflict.base_scopes)}），保持当前"
            elif normalize_original(mapping.original) in source_originals:
                result = "采用来源译名"
            else:
                result = "保持当前项目译名"
            for column, value in enumerate((mapping.original, mapping.base_translation, mapping.translation, result)):
                self.table.setItem(row, column, QTableWidgetItem(value))
        if self.preview.base_mapping_count > len(mappings):
            self.summary_label.setText(
                f"{self.summary_label.text()} 表格仅显示前 {self._MAX_VISIBLE_ROWS} 条，创建时仍会保存全部映射。"
            )


__all__ = ["TerminologySourceImportDialog"]
