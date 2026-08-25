"""Dedicated project-opening choice surface for the start center."""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from transbridge.ui.foundation.accessibility import configure_accessible_widget
from transbridge.ui.foundation.components import (
    ComponentDensity,
    ComponentKind,
    ComponentStyle,
    ElidedLabel,
    ThemedCard,
    configure_dialog,
)
from transbridge.ui.foundation.tabler_icons import tabler_icon


class _OpenModeButton(QPushButton):
    """Large two-line button used to choose one window-opening mode."""

    def __init__(self, title: str, description: str, icon_id: str, parent: QWidget) -> None:
        super().__init__(parent)
        self.setText("")
        self.setProperty("tbProjectOpenMode", True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        ComponentStyle.apply_static(self, ComponentKind.BUTTON)
        # The application skin gives ordinary buttons a compact height. This
        # choice card owns a two-line layout, so keep its geometry explicit.
        self.setFixedHeight(88)
        configure_accessible_widget(self, name=title, description=description)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 12, 16, 12)
        layout.setSpacing(14)
        icon = QLabel(self)
        icon.setPixmap(tabler_icon(icon, icon_id, 26, semantic="accent").pixmap(QSize(26, 26)))
        icon.setFixedSize(38, 38)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(icon)

        labels = QVBoxLayout()
        labels.setContentsMargins(0, 0, 0, 0)
        labels.setSpacing(4)
        title_label = QLabel(title, self)
        title_label.setProperty("tbProjectOpenModeTitle", True)
        title_font = QFont(title_label.font())
        title_font.setWeight(QFont.Weight.DemiBold)
        title_label.setFont(title_font)
        description_label = QLabel(description, self)
        description_label.setProperty("tbProjectOpenModeDescription", True)
        description_label.setProperty("tbSecondaryText", True)
        ComponentStyle.apply_static(description_label, ComponentKind.LABEL, ComponentDensity.COMPACT)
        for label in (title_label, description_label):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            labels.addWidget(label)
        layout.addLayout(labels, 1)

        arrow = QLabel(self)
        arrow.setPixmap(tabler_icon(arrow, "chevron-right", 20).pixmap(QSize(20, 20)))
        arrow.setFixedSize(28, 28)
        arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        arrow.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(arrow)


class ProjectOpenChoiceDialog(QDialog):
    """Present project context and two explicit opening modes."""

    current_window_requested = pyqtSignal(str)
    new_window_requested = pyqtSignal(str)

    def __init__(self, project_name: str, project_path: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        configure_dialog(self)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setModal(True)
        self.setWindowTitle("打开工程")
        self.setMinimumWidth(560)
        self._project_path = project_path
        configure_accessible_widget(
            self,
            name="选择工程打开方式",
            description=f"选择在当前窗口或新窗口打开工程 {project_name}",
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(16)
        title = QLabel("选择打开方式", self)
        title_font = QFont(title.font())
        title_font.setPointSizeF(16.0)
        title_font.setWeight(QFont.Weight.DemiBold)
        title.setFont(title_font)
        configure_accessible_widget(title, name="选择打开方式")
        layout.addWidget(title)

        subtitle = QLabel("请选择如何打开这个本地工程。", self)
        subtitle.setProperty("tbSecondaryText", True)
        ComponentStyle.apply_static(subtitle, ComponentKind.LABEL)
        layout.addWidget(subtitle)

        project_card = ThemedCard(self)
        project_layout = QHBoxLayout(project_card)
        project_layout.setContentsMargins(16, 12, 16, 12)
        project_layout.setSpacing(12)
        project_icon = QLabel(project_card)
        project_icon.setPixmap(tabler_icon(project_icon, "folder", 26, semantic="accent").pixmap(QSize(26, 26)))
        project_icon.setFixedSize(34, 34)
        project_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        project_layout.addWidget(project_icon)
        project_text = QVBoxLayout()
        project_text.setContentsMargins(0, 0, 0, 0)
        project_text.setSpacing(2)
        name_label = ElidedLabel(project_name, project_card)
        ComponentStyle.apply_static(name_label, ComponentKind.LABEL)
        path_label = ElidedLabel(project_path, project_card)
        path_label.setProperty("tbSecondaryText", True)
        ComponentStyle.apply_static(path_label, ComponentKind.LABEL, ComponentDensity.COMPACT)
        project_text.addWidget(name_label)
        project_text.addWidget(path_label)
        project_layout.addLayout(project_text, 1)
        layout.addWidget(project_card)

        self.current_window_button = _OpenModeButton(
            "在当前窗口打开",
            "关闭当前工程视图并切换到所选工程",
            "home",
            self,
        )
        self.new_window_button = _OpenModeButton(
            "在新窗口打开",
            "保留当前窗口，同时启动一个独立窗口",
            "plus",
            self,
        )
        self.current_window_button.clicked.connect(self._open_current_window)
        self.new_window_button.clicked.connect(self._open_new_window)
        layout.addWidget(self.current_window_button)
        layout.addWidget(self.new_window_button)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel, parent=self)
        cancel = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        cancel.setText("取消")
        cancel.setAccessibleName("取消打开工程")
        ComponentStyle.apply_static(cancel, ComponentKind.BUTTON)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.current_window_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def _open_current_window(self) -> None:
        self.current_window_requested.emit(self._project_path)
        self.accept()

    def _open_new_window(self) -> None:
        self.new_window_requested.emit(self._project_path)
        self.accept()


__all__ = ["ProjectOpenChoiceDialog"]
