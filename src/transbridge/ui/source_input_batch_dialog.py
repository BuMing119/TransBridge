"""Review missing inputs and explicitly confirm the scope of a pending operation."""

from __future__ import annotations

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from transbridge.application.projects.source_input_candidates import InputNeed
from transbridge.ui.foundation.components import (
    ComponentKind,
    ComponentStyle,
    SemanticState,
    configure_dialog,
    make_primary_button,
)


class _NeedsModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.needs: tuple[InputNeed, ...] = ()
        self.candidates: dict[str, tuple[str, ...]] = {}
        self.requirement = "写回模板"
        self.partial = False
        self.review = False

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.needs)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else 4

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return ("输入文件", "译文条数", "本次范围" if self.review else "状态", "说明")[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return None
        need = self.needs[index.row()]
        paths = self.candidates.get(need.key, ())
        status = "已补齐" if need.ready else "待补充"
        detail = f"缺少{self.requirement}；请选择对应的 {need.label} 文件。"
        if need.ready:
            detail = need.reference_path or "使用工程中已保存的输入，无需原来的来源路径。"
        elif paths:
            status = "需要确认" if len(paths) > 1 else "发现候选"
            detail = f"发现 {len(paths)} 个候选；选择后点击“使用所选文件补充”。"
        if not need.ready and self.partial:
            status = "本次跳过"
        if self.review:
            status = ("将写回" if self.requirement == "写回模板" else "将处理") if need.ready else "本次跳过"
            detail = "已保存译文保留在工程中。" if need.ready else "不纳入本次操作；译文仍保留在工程中。"
        return (need.label, need.entry_count, status, detail)[index.column()]

    def replace(self, needs):
        self.beginResetModel()
        self.needs = tuple(needs)
        self.endResetModel()


def _table(model, parent, name):
    table = QTableView(parent)
    table.setModel(model)
    table.setAccessibleName(name)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setWordWrap(False)
    table.verticalHeader().hide()
    table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
    table.setColumnWidth(0, 190)
    table.setColumnWidth(1, 85)
    table.setColumnWidth(2, 85)
    table.horizontalHeader().setStretchLastSection(True)
    ComponentStyle.apply_static(table, ComponentKind.TABLE)
    return table


class SourceInputBatchDialog(QDialog):
    files_requested = pyqtSignal()
    directory_requested = pyqtSignal()
    source_requested = pyqtSignal(str)
    candidate_requested = pyqtSignal(str, str)
    continued = pyqtSignal(tuple)

    def __init__(
        self,
        needs: tuple[InputNeed, ...],
        parent=None,
        *,
        requirement="template",
        allow_partial=False,
        operation_label="本次操作",
    ):
        super().__init__(parent)
        configure_dialog(self)
        self.setWindowTitle("补充处理所需的输入")
        self.resize(860, 610)
        self._needs = tuple(needs)
        self._initial_ready = {need.key for need in needs if need.ready}
        self._candidates: dict[str, tuple[str, ...]] = {}
        self._busy = False
        self._submitted = False
        self._allow_partial = allow_partial
        self._requirement = "写回模板" if requirement == "template" else "原文参考"
        self._operation_label = operation_label
        self._scope: tuple[str, ...] = ()
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        self.title = QLabel(self)
        title_font = self.title.font()
        title_font.setPointSize(title_font.pointSize() + 3)
        title_font.setBold(True)
        self.title.setFont(title_font)
        layout.addWidget(self.title)
        self.context = QLabel(f"{operation_label} · 已保存的译文仍保留在工程中。", self)
        self.context.setTextFormat(Qt.TextFormat.PlainText)
        self.context.setWordWrap(True)
        layout.addWidget(self.context)
        self.pages = QStackedWidget(self)
        layout.addWidget(self.pages, 1)
        self._build_preparation()
        self._build_confirmation()
        self.feedback = QLabel(self)
        self.feedback.setTextFormat(Qt.TextFormat.PlainText)
        self.feedback.setWordWrap(True)
        self.feedback.setAccessibleName("输入准备反馈")
        layout.addWidget(self.feedback)
        self.cancel_button = QPushButton("稍后处理", self)
        self.cancel_button.clicked.connect(self.reject)
        footer = QHBoxLayout()
        footer.addStretch()
        footer.addWidget(self.cancel_button)
        layout.addLayout(footer)
        self._refresh()

    def _build_preparation(self):
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        self.progress = QProgressBar(page)
        self.progress.setAccessibleName("本次操作输入准备进度")
        layout.addWidget(self.progress)
        toolbar = QHBoxLayout()
        self.files_button = make_primary_button("选择多个文件…", page)
        self.files_button.clicked.connect(self.files_requested.emit)
        self.directory_button = QPushButton("从文件夹查找…", page)
        self.directory_button.clicked.connect(self.directory_requested.emit)
        toolbar.addWidget(self.files_button)
        toolbar.addWidget(self.directory_button)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        self.model = _NeedsModel(self)
        self.model.requirement = self._requirement
        self.table = _table(self.model, page, "本次需要补充的输入清单")
        self.table.selectionModel().currentRowChanged.connect(self._selection_changed)
        layout.addWidget(self.table, 1)
        choices = QHBoxLayout()
        self.source_button = QPushButton("为选中项选择文件…", page)
        self.source_button.clicked.connect(self._request_source)
        choices.addWidget(self.source_button)
        self.candidate_selector = QComboBox(page)
        self.candidate_selector.setMinimumWidth(0)
        self.candidate_selector.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.candidate_selector.setAccessibleName("选中输入的候选完整路径")
        self.candidate_selector.currentIndexChanged.connect(self._candidate_changed)
        choices.addWidget(self.candidate_selector, 1)
        self.candidate_button = QPushButton("使用所选文件补充", page)
        self.candidate_button.clicked.connect(self._request_candidate)
        choices.addWidget(self.candidate_button)
        layout.addLayout(choices)
        self.known_toggle = QToolButton(page)
        self.known_toggle.setCheckable(True)
        self.known_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.known_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.known_toggle.toggled.connect(self._toggle_known)
        layout.addWidget(self.known_toggle)
        self.known_model = _NeedsModel(self)
        self.known_table = _table(self.known_model, page, "原本已有可用输入的项目")
        self.known_table.setMaximumHeight(150)
        self.known_table.hide()
        layout.addWidget(self.known_table)
        self.skip_checkbox = QCheckBox("找不到的输入本次先跳过；其余译文仍保留在工程中。", page)
        self.skip_checkbox.setVisible(self._allow_partial)
        self.skip_checkbox.toggled.connect(self._refresh)
        layout.addWidget(self.skip_checkbox)
        self.review_button = make_primary_button("确认本次操作范围", page)
        self.review_button.clicked.connect(self._review_scope)
        layout.addWidget(self.review_button, alignment=Qt.AlignmentFlag.AlignRight)
        self.pages.addWidget(page)

    def _build_confirmation(self):
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        self.scope_summary = QLabel(page)
        self.scope_summary.setWordWrap(True)
        self.scope_summary.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.scope_summary)
        self.scope_model = _NeedsModel(self)
        self.scope_model.review = True
        self.scope_model.requirement = self._requirement
        self.scope_table = _table(self.scope_model, page, "确认将处理和跳过的输入范围")
        layout.addWidget(self.scope_table, 1)
        preservation = QLabel("所有已保存译文继续保留；跳过的输入可以以后补充，再执行操作。", page)
        preservation.setWordWrap(True)
        layout.addWidget(preservation)
        actions = QHBoxLayout()
        actions.addStretch()
        self.back_button = QPushButton("返回调整", page)
        self.back_button.clicked.connect(self._back)
        actions.addWidget(self.back_button)
        self.confirm_button = make_primary_button("确认范围并继续", page)
        self.confirm_button.clicked.connect(self._continue)
        actions.addWidget(self.confirm_button)
        layout.addLayout(actions)
        self.pages.addWidget(page)

    def _selected_need(self):
        current = self.table.currentIndex()
        return self.model.needs[current.row()] if current.isValid() else None

    def _can_continue(self):
        ready = sum(need.ready for need in self._needs)
        return bool(ready) and (ready == len(self._needs) or (self._allow_partial and self.skip_checkbox.isChecked()))

    def _refresh(self):
        selected = self._selected_need()
        selected_key = selected.key if selected else None
        ready = sum(need.ready for need in self._needs)
        missing = len(self._needs) - ready
        title = f"本次操作还需要补充 {missing} 个输入" if missing else "本次操作所需输入已齐全"
        self.title.setText("确认本次操作范围" if self.pages.currentIndex() == 1 else title)
        self.progress.setRange(0, max(1, len(self._needs)))
        self.progress.setValue(ready)
        self.progress.setFormat(f"{ready} / {len(self._needs)} 个输入已准备好")
        self.model.partial = self._allow_partial and self.skip_checkbox.isChecked()
        self.model.candidates = self._candidates
        self.model.replace(need for need in self._needs if need.key not in self._initial_ready or not need.ready)
        self.known_model.replace(need for need in self._needs if need.key in self._initial_ready and need.ready)
        count = len(self.known_model.needs)
        self.known_toggle.setText(f"另有 {count} 个输入原本已准备好，无需补充")
        self.known_toggle.setVisible(bool(count))
        self.known_table.setVisible(bool(count) and self.known_toggle.isChecked())
        if self.model.needs:
            row = next((i for i, need in enumerate(self.model.needs) if need.key == selected_key), 0)
            self.table.setCurrentIndex(self.model.index(row, 0))
        self._selection_changed()
        self.review_button.setEnabled(not self._busy and self._can_continue())

    def _selection_changed(self, *_args):
        need = self._selected_need()
        self.candidate_selector.blockSignals(True)
        self.candidate_selector.clear()
        self.candidate_selector.addItem("请选择候选文件…", None)
        paths = self._candidates.get(need.key, ()) if need else ()
        for path in paths:
            self.candidate_selector.addItem(path, path)
            self.candidate_selector.setItemData(self.candidate_selector.count() - 1, path, Qt.ItemDataRole.ToolTipRole)
        self.candidate_selector.setCurrentIndex(1 if len(paths) == 1 else 0)
        self.candidate_selector.blockSignals(False)
        self.source_button.setEnabled(bool(need) and not need.ready and not self._busy)
        self.source_button.setText("选中项已补充" if need and need.ready else "为选中项选择文件…")
        self.candidate_selector.setEnabled(bool(paths) and not self._busy)
        self._candidate_changed()

    def _candidate_changed(self, *_args):
        path = self.candidate_selector.currentData()
        self.candidate_selector.setToolTip(path or "请选择一个明确的候选文件。")
        self.candidate_button.setEnabled(bool(path) and not self._busy)

    def _request_source(self):
        need = self._selected_need()
        if need is not None and not need.ready and not self._busy:
            self.source_requested.emit(need.key)

    def _request_candidate(self):
        need = self._selected_need()
        path = self.candidate_selector.currentData()
        if need is not None and path and not self._busy:
            self.candidate_requested.emit(need.key, path)

    def _toggle_known(self, checked):
        self.known_toggle.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)
        self.known_table.setVisible(checked and bool(self.known_model.needs))

    def _review_scope(self):
        if self._busy or not self._can_continue():
            return
        self._scope = tuple(need.key for need in self._needs if need.ready)
        skipped = tuple(need for need in self._needs if not need.ready)
        verb = "将写回" if self._requirement == "写回模板" else "将处理"
        self.scope_summary.setText(
            f"{self._operation_label}：{verb} {len(self._scope)} 个输入、"
            f"{sum(need.entry_count for need in self._needs if need.ready)} 条译文；"
            f"本次跳过 {len(skipped)} 个输入、{sum(need.entry_count for need in skipped)} 条译文。"
        )
        self.scope_model.replace(self._needs)
        self._submitted = False
        self.confirm_button.setEnabled(True)
        self.pages.setCurrentIndex(1)
        self.title.setText("确认本次操作范围")

    def _back(self):
        if not self._busy:
            self._scope = ()
            self._submitted = False
            self.pages.setCurrentIndex(0)
            self._refresh()

    def _continue(self):
        if self._busy or self._submitted or self.pages.currentIndex() != 1 or not self._can_continue():
            return
        self._submitted = True
        self.confirm_button.setEnabled(False)
        self.continued.emit(self._scope)

    def set_needs(self, needs: tuple[InputNeed, ...]):
        self._needs = tuple(needs)
        self._scope = ()
        self._submitted = False
        self.confirm_button.setEnabled(not self._busy)
        self.pages.setCurrentIndex(0)
        self._refresh()

    def set_candidates(self, candidates: dict[str, tuple[str, ...]]):
        self._candidates = {key: tuple(paths) for key, paths in candidates.items()}
        self._refresh()

    def set_busy(self, busy: bool):
        self._busy = busy
        for widget in (self.files_button, self.directory_button, self.skip_checkbox, self.table, self.back_button):
            widget.setEnabled(not busy)
        self.confirm_button.setEnabled(not busy and not self._submitted)
        self._refresh()

    def set_feedback(self, message: str, error=False):
        self.feedback.setText(message)
        ComponentStyle.apply_state(self.feedback, SemanticState.ERROR if error else SemanticState.INFO)
        if error:
            self._submitted = False
            self.confirm_button.setEnabled(not self._busy)
