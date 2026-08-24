"""Keyboard-accessible review surface for an inert safe-drop resolution."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from .drop_router import DropResolution, DropResolutionStatus


class DropReviewDialog(QDialog):
    """Show what a drop means before forwarding its canonical intent."""

    confirm_requested = pyqtSignal(object)
    dismiss_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("检查拖放内容")
        self.setAccessibleName("拖放候选计划")
        self.setModal(True)
        self._resolution = DropResolution.cancelled()
        self._decision_pending = False

        self._headline = QLabel(self)
        self._headline.setAccessibleName("拖放识别结果")
        self._headline.setWordWrap(True)
        self._details = QLabel(self)
        self._details.setAccessibleName("对象范围与恢复说明")
        self._details.setWordWrap(True)
        self._details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByKeyboard)

        self._buttons = QDialogButtonBox(self)
        self._confirm = self._buttons.addButton("打开候选计划", QDialogButtonBox.ButtonRole.AcceptRole)
        self._cancel = self._buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        self._confirm.setAccessibleName("确认并打开候选计划")
        self._cancel.setAccessibleName("取消拖放候选计划")
        self._confirm.setDefault(True)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self._headline)
        layout.addWidget(self._details)
        layout.addWidget(self._buttons)

    @property
    def resolution(self) -> DropResolution:
        return self._resolution

    def review(self, resolution: DropResolution) -> None:
        self._resolution = resolution
        self._decision_pending = True
        candidate = resolution.candidate
        if resolution.status is DropResolutionStatus.CANDIDATE and candidate is not None:
            path = candidate.payload_mapping().get("path", "未提供")
            self._headline.setText(candidate.summary)
            self._details.setText(
                f"对象：{path}\n范围：只打开现有功能的编辑/预检页面。"
                "\n恢复方式：取消不会执行网络、覆盖、解压或发布操作。"
            )
            self._confirm.setEnabled(True)
            self._confirm.setFocus(Qt.FocusReason.OtherFocusReason)
        else:
            diagnostic = resolution.diagnostics[0] if resolution.diagnostics else None
            self._headline.setText("无法提出安全的候选计划")
            self._details.setText(
                "没有执行任何操作。"
                if diagnostic is None
                else f"{diagnostic.code}：{diagnostic.message}\n恢复方式：{diagnostic.recovery}"
            )
            self._confirm.setEnabled(False)
            self._cancel.setText("关闭")
            self._cancel.setFocus(Qt.FocusReason.OtherFocusReason)

    def accept(self) -> None:
        if not self._decision_pending or self._resolution.status is not DropResolutionStatus.CANDIDATE:
            return
        self._decision_pending = False
        self.confirm_requested.emit(self._resolution)
        super().accept()

    def reject(self) -> None:
        if self._decision_pending:
            self._decision_pending = False
            self.dismiss_requested.emit()
        super().reject()


__all__ = ["DropReviewDialog"]
