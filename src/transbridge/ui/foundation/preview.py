"""Isolated theme preview widgets that never mutate application state."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .qt_palette import qcolor
from .theme_service import ThemeSnapshot


class ThemePreviewWidget(QFrame):
    """Render a small component matrix under a candidate root palette."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("theme-preview")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setAutoFillBackground(True)
        self.setAccessibleName("主题预览")
        self._snapshot: ThemeSnapshot | None = None
        self._fingerprint: str | None = None
        self._apply_count = 0
        self._disposed = False
        self._build_ui()

    @property
    def snapshot(self) -> ThemeSnapshot | None:
        return self._snapshot

    @property
    def apply_count(self) -> int:
        return self._apply_count

    @property
    def disposed(self) -> bool:
        return self._disposed

    def show_snapshot(self, snapshot: ThemeSnapshot) -> bool:
        if self._disposed:
            raise RuntimeError("theme_preview_disposed")
        if snapshot.fingerprint == self._fingerprint:
            return False
        self._snapshot = snapshot
        self._fingerprint = snapshot.fingerprint
        self._apply_count += 1
        self.setPalette(QPalette(snapshot.palette))
        self._apply_domain_colors(snapshot)
        self.update()
        return True

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._snapshot = None
        self._fingerprint = None
        self.setUpdatesEnabled(False)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        title = QLabel("标题与正文")
        title.setAccessibleName("主题预览标题")
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)

        controls = QGroupBox("控件状态")
        grid = QGridLayout(controls)
        grid.addWidget(QLabel("输入"), 0, 0)
        self._input = QLineEdit("示例内容")
        self._input.setAccessibleName("预览输入框")
        grid.addWidget(self._input, 0, 1, 1, 2)
        grid.addWidget(QPushButton("主要操作"), 1, 0)
        disabled = QPushButton("已禁用")
        disabled.setEnabled(False)
        grid.addWidget(disabled, 1, 1)
        layout.addWidget(controls)

        status_row = QWidget(self)
        status_layout = QGridLayout(status_row)
        status_layout.setContentsMargins(0, 0, 0, 0)
        self._success = QLabel("✓ 成功")
        self._warning = QLabel("! 警告")
        self._error = QLabel("× 错误")
        for column, label in enumerate((self._success, self._warning, self._error)):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setAutoFillBackground(True)
            label.setMargin(5)
            status_layout.addWidget(label, 0, column)
        layout.addWidget(status_row)

        self._table = QTableWidget(2, 2, self)
        self._table.setAccessibleName("主题预览表格")
        self._table.setHorizontalHeaderLabels(("状态", "说明"))
        self._table.setItem(0, 0, QTableWidgetItem("已选择"))
        self._table.setItem(0, 1, QTableWidgetItem("当前行"))
        self._table.setItem(1, 0, QTableWidgetItem("普通"))
        self._table.setItem(1, 1, QTableWidgetItem("另一行"))
        self._table.selectRow(0)
        self._table.setMaximumHeight(105)
        layout.addWidget(self._table)

    def _apply_domain_colors(self, snapshot: ThemeSnapshot) -> None:
        semantic = snapshot.tokens.semantic
        values = (
            (self._success, semantic.success),
            (self._warning, semantic.warning),
            (self._error, semantic.error),
        )
        for label, foreground in values:
            palette = QPalette(snapshot.palette)
            palette.setColor(QPalette.ColorRole.WindowText, qcolor(foreground))
            palette.setColor(QPalette.ColorRole.Window, qcolor(semantic.surface_alt))
            label.setPalette(palette)


__all__ = ["ThemePreviewWidget"]
