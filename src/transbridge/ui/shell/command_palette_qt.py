"""Thin Qt view for the Qt-free command palette controller."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from transbridge.ui.shell.command_palette import (
    CommandPaletteController,
    CommandSearchSnapshot,
)


class CommandPaletteDialog(QDialog):
    """Event-driven view; Ctrl+K ownership remains in the shell composition."""

    intent_requested = pyqtSignal(object)

    def __init__(self, controller: CommandPaletteController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._controller = controller
        self.setWindowTitle("查找命令")
        self.setAccessibleName("命令搜索窗口")
        self.setModal(False)

        self._search = QLineEdit(self)
        self._search.setPlaceholderText("查找功能、最近工程或翻译内容")
        self._search.setAccessibleName("命令搜索")
        self._results = QListWidget(self)
        self._results.setAccessibleName("命令搜索结果")
        self._reason = QLabel(self)
        self._reason.setWordWrap(True)
        self._reason.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._reason.setAccessibleName("命令可用性说明")
        close_button = QPushButton("关闭", self)
        close_button.setAccessibleName("关闭命令搜索")

        layout = QVBoxLayout(self)
        layout.addWidget(self._search)
        layout.addWidget(self._results)
        layout.addWidget(self._reason)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        self._search.textChanged.connect(self._on_query_changed)
        self._search.returnPressed.connect(self._activate_current)
        self._results.currentItemChanged.connect(self._on_current_changed)
        self._results.itemActivated.connect(self._on_item_activated)
        close_button.clicked.connect(self.close)

    def open_palette(self, query: str = "") -> None:
        snapshot = self._controller.open(query)
        self._search.setText(query)
        self._render(snapshot)
        self.show()
        self.raise_()
        self.activateWindow()
        self._search.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def _on_query_changed(self, query: str) -> None:
        if self._controller.is_open:
            self._render(self._controller.set_query(query))

    def _render(self, snapshot: CommandSearchSnapshot) -> None:
        self._results.clear()
        self._reason.clear()
        for result in snapshot.results:
            suffix = f" — {result.disabled_reason}" if result.disabled_reason else ""
            item = QListWidgetItem(f"{result.label}{suffix}", self._results)
            item.setData(Qt.ItemDataRole.UserRole, result.result_id)
            if result.disabled_reason:
                item.setToolTip(result.disabled_reason)
        if self._results.count():
            self._results.setCurrentRow(0)

    def _on_current_changed(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        self._reason.setText(current.toolTip() if current is not None else "")

    def _on_item_activated(self, item: QListWidgetItem) -> None:
        activation = self._controller.activate(str(item.data(Qt.ItemDataRole.UserRole)))
        if activation.request is None:
            self._reason.setText(activation.blocked_reason or "当前不可用")
            self._reason.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        self.intent_requested.emit(activation.request)
        self.accept()

    def _activate_current(self) -> None:
        item = self._results.currentItem()
        if item is not None:
            self._on_item_activated(item)

    def reject(self) -> None:
        """Esc closes only this secondary surface and its controller."""

        self._controller.close()
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt override
        self._controller.close()
        super().closeEvent(event)


__all__ = ["CommandPaletteDialog"]
