# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Keep export options scrollable and reserve one area for execution feedback."""

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import (
    QWIDGETSIZE_MAX,
    QFrame,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)


class WriteFeedbackStack(QStackedWidget):
    def __init__(self, checks, details):
        super().__init__()
        self._empty = QWidget()
        self._checks = checks
        self._details = details
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        for widget in (checks, details):
            widget.setMinimumHeight(0)
            widget.setMaximumHeight(QWIDGETSIZE_MAX)
        checks.setFrameShape(QFrame.Shape.NoFrame)
        checks.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        checks.widget().setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.addWidget(self._empty)
        self.addWidget(checks)
        self.addWidget(details)

    def sizeHint(self):  # noqa: N802 - Qt override
        # Allow about four lines of feedback at the current font size.
        return QSize(0, self.fontMetrics().lineSpacing() * 4 + 16)

    def minimumSizeHint(self):  # noqa: N802 - Qt override
        return self.sizeHint()

    def show_status(self, visible):
        self.setCurrentWidget(self._checks if visible else self._empty)

    def show_details(self, visible):
        self.setCurrentWidget(self._details if visible else self._checks)


class WriteOptionsScroll(QScrollArea):
    def sizeHint(self):  # noqa: N802 - Qt override
        # The default viewport fits a typical form. Larger forms scroll inside it.
        content = self.widget()
        content_height = max(content.sizeHint().height(), content.minimumSizeHint().height()) if content else 0
        return QSize(640, min(content_height, self.fontMetrics().lineSpacing() * 28))


def install_write_layout(dialog):
    root = dialog.layout()
    footer = root.itemAt(root.count() - 1).layout()
    options = QWidget()
    content = QVBoxLayout(options)
    content.setContentsMargins(0, 0, 0, 0)
    content.addWidget(dialog._source_label)
    content.addWidget(dialog._encoding_label)
    content.addWidget(dialog._fields_group)
    content.addWidget(dialog._transfer_summary)
    content.addStretch()
    scroll = WriteOptionsScroll(dialog)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setWidgetResizable(True)
    scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    scroll.setAccessibleName("导出选项")
    scroll.setWidget(options)
    root.insertWidget(0, scroll, 1)

    feedback = WriteFeedbackStack(dialog._checks_scroll, dialog._details)
    dialog._details_button.toggled.disconnect(dialog._details.setVisible)
    dialog._details_button.toggled.connect(feedback.show_details)
    for widget in (dialog._progress, dialog._details_button):
        policy = widget.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        widget.setSizePolicy(policy)
    footer.insertWidget(0, dialog._details_button)
    root.insertWidget(root.indexOf(dialog._progress), feedback)
    return scroll, feedback
