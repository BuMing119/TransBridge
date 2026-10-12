"""Transient information and persistent, inspectable application failures."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging

from PyQt6.QtCore import QEvent, Qt, QTimer
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from transbridge.ui.foundation.components import ComponentKind, ComponentStyle, ElidedLabel, SemanticState


@dataclass
class _Failure:
    message: str
    retry: Callable[[], None] | None
    retry_label: str


class MessageToast(QFrame):
    """Keep the last 20 unacknowledged failures until explicitly dismissed."""

    _DISPLAY_MSEC = 5_000
    _MAXIMUM_WIDTH = 560
    _MARGIN = 20

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        ComponentStyle.apply_static(self, ComponentKind.NOTIFICATION)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAccessibleName("应用通知")
        self._failures: list[_Failure] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        self._label = ElidedLabel(parent=self)
        self._label.setAccessibleName("应用通知内容")
        layout.addWidget(self._label)
        self.history = QComboBox(self)
        self.history.setAccessibleName("最近未关闭的错误（最多 20 条）")
        self.history.setMinimumContentsLength(12)
        self.history.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.history.currentIndexChanged.connect(self._select_failure)
        layout.addWidget(self.history)
        self.actions = QWidget(self)
        row = QHBoxLayout(self.actions)
        row.setContentsMargins(0, 0, 0, 0)
        self.details_button = QPushButton("展开详情", self.actions)
        self.details_button.setCheckable(True)
        self.details_button.toggled.connect(self._toggle_details)
        row.addWidget(self.details_button)
        self.copy_button = QPushButton("复制详情", self.actions)
        self.copy_button.clicked.connect(lambda: QApplication.clipboard().setText(self.details.toPlainText()))
        row.addWidget(self.copy_button)
        self.retry_button = QPushButton("重试", self.actions)
        self.retry_button.clicked.connect(self._retry)
        row.addWidget(self.retry_button)
        row.addStretch()
        self.close_button = QPushButton("关闭此条", self.actions)
        self.close_button.clicked.connect(self._dismiss)
        row.addWidget(self.close_button)
        layout.addWidget(self.actions)
        self.details = QPlainTextEdit(self)
        self.details.setReadOnly(True)
        self.details.setAccessibleName("完整错误详情")
        self.details.setFixedHeight(160)
        layout.addWidget(self.details)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self._DISPLAY_MSEC)
        self._timer.timeout.connect(self.hide)
        parent.installEventFilter(self)
        self.hide()

    def show_message(self, message: str, *, error=False, retry=None, retry_label="重试") -> None:
        if error:
            self._timer.stop()
            self._failures.append(_Failure(message, retry, retry_label))
            self._failures = self._failures[-20:]
            self._rebuild_history(len(self._failures) - 1)
        elif not self._failures:
            self._render(message, failure=None)
            self._timer.start()

    def _rebuild_history(self, selected: int) -> None:
        self.history.blockSignals(True)
        self.history.clear()
        for index, failure in enumerate(self._failures, 1):
            summary = (failure.message.splitlines() or ["操作失败"])[0]
            self.history.addItem(f"{index}. {summary[:60]}")
            self.history.setItemData(index - 1, failure.message, Qt.ItemDataRole.ToolTipRole)
        self.history.setCurrentIndex(selected)
        self.history.blockSignals(False)
        self._select_failure(selected)

    def _select_failure(self, index: int) -> None:
        if 0 <= index < len(self._failures):
            failure = self._failures[index]
            self._render(failure.message, failure=failure)

    def _render(self, message: str, *, failure: _Failure | None) -> None:
        ComponentStyle.apply_state(self, SemanticState.ERROR if failure else SemanticState.INFO)
        self._label.set_full_text(message.splitlines()[0] if message else "操作失败")
        self._label.setToolTip(message)
        self.setAccessibleDescription(message)
        self.details.setPlainText(message)
        self.actions.setVisible(failure is not None)
        self.history.setVisible(len(self._failures) > 1)
        self.retry_button.setVisible(failure is not None and failure.retry is not None)
        if failure is not None:
            self.retry_button.setText(failure.retry_label)
        self.details.setVisible(failure is not None and self.details_button.isChecked())
        self._reposition()
        self.show()
        self.raise_()

    def _toggle_details(self, checked: bool) -> None:
        self.details.setVisible(checked)
        self.details_button.setText("收起详情" if checked else "展开详情")
        self._reposition()

    def _dismiss(self) -> None:
        index = self.history.currentIndex()
        if 0 <= index < len(self._failures):
            self._failures.pop(index)
        if self._failures:
            self._rebuild_history(min(index, len(self._failures) - 1))
        else:
            self.hide()

    def _retry(self) -> None:
        index = self.history.currentIndex()
        if not 0 <= index < len(self._failures):
            return
        retry = self._failures[index].retry
        if retry is not None:
            self._dismiss()
            try:
                retry()
            except Exception as exc:  # GUI callback boundary; preserve the original error in logs.
                logging.getLogger(__name__).exception("Notification recovery failed")
                self.show_message(f"重试失败：{exc}", error=True)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt override
        if watched is self.parentWidget() and event.type() in {QEvent.Type.Resize, QEvent.Type.Show}:
            self._reposition()
        return super().eventFilter(watched, event)

    def _reposition(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        self.setFixedWidth(min(self._MAXIMUM_WIDTH, max(1, parent.width() - self._MARGIN * 2)))
        self.adjustSize()
        self.move(max(0, parent.width() - self.width() - self._MARGIN), self._MARGIN)

    def shutdown(self) -> None:
        self._timer.stop()
        self._failures.clear()
        parent = self.parentWidget()
        if parent is not None:
            parent.removeEventFilter(self)
        self.hide()
