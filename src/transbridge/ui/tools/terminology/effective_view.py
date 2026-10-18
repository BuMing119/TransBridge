"""Read-only view of the exact effective snapshot used by translation."""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableView, QVBoxLayout, QWidget

from .pagination_bar import PaginationBar
from .term_filters import TermFilterBar


class EffectiveTermsView(QWidget):
    query_changed = pyqtSignal(object)
    edit_requested = pyqtSignal(object)
    scheme_edit_requested = pyqtSignal(object)
    start_requested = pyqtSignal()

    def __init__(self, model, parent=None):
        super().__init__(parent)
        self._has_version = self._has_draft = self._has_profile = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.description = QLabel("后续翻译采用此处的译名。选中词条可修改基础术语，保存到草稿并发布后生效。", self)
        self.description.setWordWrap(True)
        layout.addWidget(self.description)
        self.filters = TermFilterBar(self)
        self.filters.changed.connect(self.query_changed)
        layout.addWidget(self.filters)
        self.table = QTableView(self)
        self.table.setModel(model)
        self.table.setProperty("tbComponentKind", "table")
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)
        self.empty_panel = QWidget(self)
        empty_layout = QVBoxLayout(self.empty_panel)
        empty_layout.addStretch(1)
        self.empty = QLabel(self.empty_panel)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        empty_layout.addWidget(self.empty)
        self.empty_action = QPushButton("从来源生成草稿", self.empty_panel)
        self.empty_action.setProperty("tbComponentKind", "button")
        self.empty_action.clicked.connect(self.start_requested)
        empty_layout.addWidget(self.empty_action, 0, Qt.AlignmentFlag.AlignHCenter)
        empty_layout.addStretch(1)
        layout.addWidget(self.empty_panel, 1)
        self.actions = QWidget(self)
        row = QHBoxLayout(self.actions)
        row.setContentsMargins(0, 0, 0, 0)
        self.edit_button = QPushButton("编辑基础术语…", self)
        self.edit_button.clicked.connect(lambda: self._edit(self.edit_requested))
        self.scheme_edit_button = QPushButton("编辑方案译名…", self)
        self.scheme_edit_button.clicked.connect(lambda: self._edit(self.scheme_edit_requested))
        row.addWidget(self.edit_button)
        row.addWidget(self.scheme_edit_button)
        row.addStretch(1)
        layout.addWidget(self.actions)
        self.pagination = PaginationBar(model, self)
        layout.addWidget(self.pagination)
        self.table.selectionModel().selectionChanged.connect(self._render)
        model.pagination_changed.connect(self._render)
        self.table.doubleClicked.connect(lambda _index: self._edit(self.edit_requested))
        self._render()

    def set_workflow(self, state, *, has_profile=False):
        self._has_version, self._has_draft, self._has_profile = state.has_version, state.has_draft, has_profile
        self.description.setText(
            "当前译名包含方案覆盖。“编辑基础术语”修改项目原始词条；修改方案译名请使用对应按钮。"
            if has_profile
            else "后续翻译采用此处的译名。选中词条可修改基础术语，保存到草稿并发布后生效。"
        )
        self._render()

    def _render(self, *_args):
        state = self.table.model().pagination
        empty = not state.loading and not state.error and state.has_query and state.total == 0
        self.table.setVisible(not empty)
        self.empty_panel.setVisible(empty)
        self.empty.setVisible(empty)
        self.description.setVisible(not empty)
        self.filters.setVisible(self._has_version or not empty)
        self.pagination.setVisible(not empty or bool(state.error))
        if not self._has_version:
            message = (
                "草稿尚未发布，因此当前没有生效术语。\n前往编辑草稿，检查并发布后会显示在这里。"
                if self._has_draft
                else ("还没有项目术语。\n从项目来源生成草稿，检查译名后发布，即可用于后续翻译。")
            )
        elif self.filters.search.text() or self.filters.scope.currentData() is not None:
            message = "没有符合当前筛选条件的术语。\n清空搜索或选择全部范围后重新筛选。"
        else:
            message = "当前没有生效术语。请检查草稿中的确认状态，以及译名方案的映射是否完整。"
        self.empty.setText(message)
        self.empty_action.setVisible(empty and not self._has_version)
        self.empty_action.setText("前往编辑草稿" if self._has_draft else "从来源生成草稿")
        self.actions.setVisible(bool(self.table.selectionModel().selectedRows()) and not empty)
        self.scheme_edit_button.setVisible(self._has_profile)

    def _edit(self, signal):
        rows = self.table.selectionModel().selectedRows()
        if rows:
            signal.emit(rows[0].data(Qt.ItemDataRole.UserRole))
