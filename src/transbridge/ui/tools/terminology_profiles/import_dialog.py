# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Preview one terminology-source snapshot before creating a naming scheme."""

from __future__ import annotations

from PyQt6.QtCore import QModelIndex, Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from transbridge.application.terminology_profiles import TerminologyProfileImportPolicy
from transbridge.application.terminology_profiles.import_preview import import_preview_rows
from transbridge.ui.foundation.components import ComponentKind, ComponentStyle

from .import_preview_model import OUTCOME_LABELS, ImportPreviewFilterModel, ImportPreviewTableModel, row_details


class TerminologySourceImportDialog(QDialog):
    """Explain the snapshot boundary and show the complete generated mapping."""

    def __init__(
        self, preview, default_name: str, parent: QWidget | None = None, *, select_after_create: bool = False
    ) -> None:
        super().__init__(parent)
        self.preview = preview
        target = preview.target_publication
        self.setWindowTitle("从外部来源创建译名方案")
        if target is not None:
            self.setWindowTitle(f"导入到方案：{target.name}")
        self.setAccessibleName("外部来源转译名方案预览")
        self.resize(1040, 760)
        layout = QVBoxLayout(self)

        explanation = QLabel(
            "有效术语将完整写入新方案，来源新增术语仅在选用此方案时生效。"
            "创建为独立副本，不随外部来源自动更新，也不修改项目基础术语或其他方案。"
            "搜索和筛选只影响预览；创建时会保存全部方案映射。",
            self,
        )
        explanation.setWordWrap(True)
        if target is not None:
            explanation.setText("确认后将合并到此方案并保存新版本；不会创建另一个方案。搜索和筛选只影响预览。")
        layout.addWidget(explanation)

        form = QFormLayout()
        source_label = QLineEdit(preview.source.source_label, self)
        source_label.setReadOnly(True)
        source_label.setAccessibleName("外部来源完整路径")
        source_label.setToolTip(preview.source.source_label)
        source_label.setCursorPosition(0)
        ComponentStyle.apply_static(source_label, ComponentKind.INPUT)
        form.addRow("外部来源", source_label)
        policy_label = QLabel(
            "保留已有译名" if preview.policy is TerminologyProfileImportPolicy.KEEP_EXISTING else "采用导入译名", self
        )
        policy_label.setAccessibleName("本次导入的批量处理方式")
        form.addRow("同名术语处理", policy_label)
        self.name_edit = QLineEdit(default_name, self)
        if target is not None:
            self.name_edit.setText(target.name)
            self.name_edit.setReadOnly(True)
        self.name_edit.setAccessibleName("新译名方案名称")
        ComponentStyle.apply_static(self.name_edit, ComponentKind.INPUT)
        form.addRow("方案名称", self.name_edit)
        layout.addLayout(form)

        self.summary_label = QLabel(self._summary_text(), self)
        self.summary_label.setWordWrap(True)
        self.summary_label.setAccessibleName("外部来源导入统计")
        layout.addWidget(self.summary_label)

        filters = QHBoxLayout()
        self.search_edit = QLineEdit(self)
        self.search_edit.setPlaceholderText("搜索全部预览：原词、译名、插件或诊断说明")
        self.search_edit.setAccessibleName("搜索全部导入预览")
        self.search_edit.setClearButtonEnabled(True)
        filters.addWidget(self.search_edit, 1)
        self.result_filter = QComboBox(self)
        self.result_filter.setAccessibleName("按导入处理结果筛选")
        self.result_filter.addItem("全部结果", "all")
        self.result_filter.addItem("来源重复（已按策略处理）", "problems")
        for outcome, label in OUTCOME_LABELS.items():
            self.result_filter.addItem(label, outcome.value)
        filters.addWidget(self.result_filter)
        layout.addLayout(filters)

        self.model = ImportPreviewTableModel(import_preview_rows(preview), self)
        self.filtered = ImportPreviewFilterModel(self.model, self)
        self.table = QTableView(self)
        self.table.setModel(self.filtered)
        self.table.setAccessibleName("译名方案创建预览")
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header = self.table.horizontalHeader()
        for column in range(3):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        ComponentStyle.apply_static(self.table, ComponentKind.TABLE)
        layout.addWidget(self.table, 1)
        self.count_label = QLabel(self)
        self.count_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.count_label)
        self.row_details = QPlainTextEdit(self)
        self.row_details.setReadOnly(True)
        self.row_details.setMaximumHeight(145)
        self.row_details.setAccessibleName("导入预览选中项的完整说明")
        layout.addWidget(self.row_details)
        self.search_edit.textChanged.connect(self._apply_filters)
        self.result_filter.currentIndexChanged.connect(self._apply_filters)
        self.table.selectionModel().currentChanged.connect(self._selected)
        self._selected_source = QModelIndex()
        self.filtered.rows_changing.connect(self._remember_selection)
        self.filtered.modelReset.connect(self._filters_ready)
        self.filtered.status_changed.connect(self._search_status)
        self.finished.connect(self.filtered.close)
        self._apply_filters()

        self.select_check = QCheckBox("创建后用于当前翻译版本", self)
        self.select_check.setChecked(select_after_create)
        self.select_check.setVisible(target is None)
        layout.addWidget(self.select_check)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel, self)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.create_button = QPushButton("创建并选用" if select_after_create else "创建译名方案", self)
        if target is not None:
            self.create_button.setText("确认导入")
        self.select_check.toggled.connect(
            lambda selected: self.create_button.setText("创建并选用" if selected else "创建译名方案")
        )
        self.create_button.setDefault(True)
        ComponentStyle.apply_static(self.create_button, ComponentKind.BUTTON)
        buttons.addButton(self.create_button, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.name_edit.textChanged.connect(lambda text: self.create_button.setEnabled(bool(text.strip())))
        self.create_button.setEnabled(bool(self.profile_name))
        layout.addWidget(buttons)

    @property
    def profile_name(self) -> str:
        return self.name_edit.text().strip()

    @property
    def select_after_create(self) -> bool:
        return self.select_check.isChecked()

    def _summary_text(self) -> str:
        preview = self.preview
        return (
            f"读取 {preview.source_entry_count} 条（{preview.source_term_count} 个术语）；"
            f"新增 {preview.source_only_term_count} 条术语，覆盖已有译名 {preview.overwritten_mapping_count} 条，"
            f"保留已有译名 {preview.kept_mapping_count} 条；"
            f"采用来源译名共 {preview.adopted_mapping_count} 条映射。"
            f"{preview.conflict_count} 个同名译名差异已按批量策略处理。"
            f"合并完全重复记录 {preview.duplicate_entry_count} 条。"
        )

    def _apply_filters(self, *_args) -> None:
        self.filtered.set_filters(
            self.search_edit.text(),
            str(self.result_filter.currentData()),
            debounce=bool(_args and isinstance(_args[0], str)),
        )

    def _remember_selection(self) -> None:
        self._selected_source = self.filtered.mapToSource(self.table.currentIndex())

    def _filters_ready(self) -> None:
        count = self.filtered.rowCount()
        self._update_count()
        index = self.filtered.mapFromSource(self._selected_source)
        if not index.isValid() and count:
            index = self.filtered.index(0, 0)
        self.table.setCurrentIndex(index)
        self._selected(index)

    def _update_count(self) -> None:
        self.count_label.setText(
            f"显示 {self.filtered.rowCount()} / {self.model.rowCount()} 项；"
            f"确认后保存全部 {self.preview.mapping_count} 条方案映射。"
        )

    def _search_status(self, pending, error) -> None:
        if pending or error:
            self.count_label.setText(error or "正在搜索全部导入预览…")
        else:
            self._update_count()

    def _selected(self, current, _previous=None) -> None:
        row = current.data(Qt.ItemDataRole.UserRole)
        self.row_details.setPlainText(
            "没有匹配项；调整搜索或处理结果筛选可查看其他内容。" if row is None else row_details(row)
        )


__all__ = ["TerminologySourceImportDialog"]
