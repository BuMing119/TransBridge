"""Explicit per-entry choices for durable AI candidates and overwritten text."""

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableView,
    QTextEdit,
    QVBoxLayout,
)

from transbridge.ui.foundation.components import ComponentKind, ComponentStyle

from ._theme_support import AiThemeBinding
from .task_merge_review import TaskMergeReview
from .task_widget_style import configure_task_button, configure_task_host, configure_task_title

_REASONS = {
    "source_changed_or_missing": "原文、上下文已变化或词条已删除",
    "decision_stale": "当前内容已再次修改，请刷新后重新选择",
    "translation_stage_conflict": "当前译文或状态与任务输入不同",
}


class MergeRowsModel(QAbstractTableModel):
    headers = ("条目", "当前译文", "AI 结果", "最近覆盖前译文", "合入状态")

    def __init__(self, review):
        super().__init__(review)
        self.review, self.keys = review, ()

    def reset(self):
        self.beginResetModel()
        self.keys = tuple(self.review.rows)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.keys)

    def columnCount(self, parent=QModelIndex()):
        return len(self.headers)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.headers[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role not in {Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole}:
            return None
        key = self.keys[index.row()]
        row, current = self.review.rows[key], self.review.current.get(key)
        previous = self.review.previous.get(key, ())
        labels = {"applied": "已应用", "kept_current": "已保留当前", "pending": "待处理", "restored": "已恢复"}
        values = (
            key.local_key,
            current.translation if current is not None else "词条已不存在",
            row["text"],
            previous[0].translation if previous else "—",
            labels.get(row.get("merge_status", ""), "待应用")
            + (f" · {_REASONS.get(row['merge_reason'], '存在内容分歧')}" if row.get("merge_reason") else ""),
        )
        text = values[index.column()]
        return text[:2048] if role == Qt.ItemDataRole.ToolTipRole else text[:240].replace("\n", " ")


class TaskMergeDialog(QDialog):
    def __init__(self, ctx, directory, task_id, *, parent=None, store=None, theme_view=None):
        super().__init__(parent)
        self.setWindowTitle("处理已保存的 AI 结果")
        configure_task_host(self)
        self.resize(1080, 600)
        self.review = TaskMergeReview(ctx, directory, task_id, self, store=store)
        layout = QVBoxLayout(self)
        self.status = QLabel("正在读取已保存的结果…")
        self.status.setWordWrap(True)
        configure_task_title(self.status, "hint")
        layout.addWidget(self.status)
        self.table = QTableView()
        ComponentStyle.apply_static(self.table, ComponentKind.TABLE)
        self.model = MergeRowsModel(self.review)
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        for column in (1, 2, 3):
            self.table.setColumnWidth(column, 250)
        layout.addWidget(self.table)
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setAccessibleName("所选结果的完整当前译文、AI 结果与最近覆盖前译文")
        self.detail.setMaximumHeight(180)
        layout.addWidget(self.detail)
        self.table.selectionModel().currentRowChanged.connect(self._detail)
        actions = QHBoxLayout()
        self.buttons = []
        for text, action in (
            ("保留所选当前译文", lambda: self.review.resolve(self.selected_keys(), keep=True)),
            ("采纳所选 AI 结果", lambda: self.review.resolve(self.selected_keys())),
            ("恢复所选覆盖前译文", lambda: self.review.resolve(self.selected_keys(), restore=True)),
            ("刷新当前内容", self.review.refresh),
        ):
            button = QPushButton(text)
            configure_task_button(button, primary=text == "采纳所选 AI 结果")
            button.clicked.connect(action)
            actions.addWidget(button)
            self.buttons.append(button)
        close = QPushButton("关闭")
        configure_task_button(close)
        close.clicked.connect(self.close)
        actions.addWidget(close)
        layout.addLayout(actions)
        self.review.changed.connect(self._refresh)
        self._theme = AiThemeBinding(self, theme_view, lambda _: None)
        self.review.refresh()

    def selected_keys(self):
        return {self.model.keys[index.row()] for index in self.table.selectionModel().selectedRows()}

    @property
    def busy(self):
        return self.review.busy

    def _refresh(self):
        self.status.setText(
            self.review.error
            or ("选择词条后保留当前译文、采纳 AI 结果或恢复覆盖前译文；处理期间再次修改的词条会保留待处理。")
        )
        for button in self.buttons:
            button.setEnabled(not self.busy and not self.review.shutting_down)
        if self.busy:
            return
        self.model.reset()

    def _detail(self, current, _previous):
        if not current.isValid():
            self.detail.clear()
            return
        key = self.model.keys[current.row()]
        row, live = self.review.rows[key], self.review.current.get(key)
        previous = self.review.previous.get(key, ())
        self.detail.setPlainText(
            f"任务原文\n{row['before']['original']}\n\n当前原文\n{live.original if live else '词条已不存在'}"
            f"\n\n当前译文（状态 {live.stage if live else '—'}）\n{live.translation if live else '—'}"
            f"\n\nAI 结果（状态 {row['stage']}）\n{row['text']}"
            f"\n\n最近覆盖前译文\n{previous[0].translation if previous else '没有已确认的覆盖记录'}"
        )

    def shutdown(self):
        self.review.shutdown()

    def resume_exports(self):
        self.review.resume_exports()

    def closeEvent(self, event):
        # Keep the QObject and worker alive under the registry after hiding it.
        self.review.close()
        event.accept()
