"""Explicit pagination controls and local query feedback for terminology tables."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .paged_models import KeysetPagedTableModel


class PaginationBar(QWidget):
    def __init__(self, model: KeysetPagedTableModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.model = model
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        controls = QHBoxLayout()
        self.first_button = QPushButton("首页", self)
        self.previous_button = QPushButton("上一页", self)
        self.next_button = QPushButton("下一页", self)
        self.refresh_button = QPushButton("刷新本页", self)
        self.range_label = QLabel(self)
        self.range_label.setAccessibleName("当前页范围与总数")
        self.range_label.setTextFormat(Qt.TextFormat.PlainText)
        for button in (self.first_button, self.previous_button, self.next_button, self.refresh_button):
            button.setProperty("tbComponentKind", "button")
            controls.addWidget(button)
        controls.addWidget(self.range_label, 1)
        layout.addLayout(controls)
        self.notice = QLabel(self)
        self.notice.setWordWrap(True)
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        self.notice.setAccessibleName("列表加载状态与恢复操作")
        layout.addWidget(self.notice)
        self.first_button.clicked.connect(model.navigate_first)
        self.previous_button.clicked.connect(model.navigate_previous)
        self.next_button.clicked.connect(model.navigate_next)
        self.refresh_button.clicked.connect(model.refresh)
        model.pagination_changed.connect(self.render)
        self.render()

    def render(self) -> None:
        state = self.model.pagination
        self.first_button.setEnabled(state.can_first)
        self.previous_button.setEnabled(state.can_previous)
        self.next_button.setEnabled(state.can_next)
        self.refresh_button.setEnabled(state.has_query and not state.loading)
        self.refresh_button.setText("重试加载" if state.error else "刷新本页")
        total = "总数未知" if state.total is None else f"共 {state.total} 条"
        self.range_label.setText(f"{state.first_row}–{state.last_row} / {total}" if state.has_query else "尚未载入列表")
        message = state.notice or ""
        if state.loading:
            message = "正在加载…" + message
        elif state.error:
            message = f"加载失败：{state.error}。可点击“重试加载”，或返回其他页。"
        self.notice.setText(message)
        self.notice.setVisible(bool(message))


__all__ = ["PaginationBar"]
