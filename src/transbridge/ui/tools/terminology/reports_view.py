"""Quality report and version-log export page."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .build_view import TechnicalDetailsBox
from .view_models import TerminologyNotice


class ReportsView(QWidget):
    quality_report_requested = pyqtSignal()
    changelog_requested = pyqtSignal()
    retry_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 26, 26, 30)
        layout.setSpacing(18)
        title = QLabel("报告", self)
        title.setAccessibleName("报告")
        title.setProperty("tbTerminologyPageTitle", True)
        explanation = QLabel("按用途选择文件，系统自动使用对应的数据版本。", self)
        explanation.setWordWrap(True)
        explanation.setProperty("tbSecondary", True)
        layout.addWidget(title)
        layout.addWidget(explanation)

        exports = QFrame(self)
        exports.setProperty("tbTerminologyCard", True)
        exports_layout = QVBoxLayout(exports)
        exports_layout.setContentsMargins(22, 16, 22, 16)
        exports_layout.setSpacing(0)
        quality = QPushButton("导出 Excel", exports)
        quality.setProperty("tbTerminologyPrimary", True)
        changelog = QPushButton("导出更新日志…", exports)
        changelog.setProperty("tbComponentKind", "button")
        exports_layout.addWidget(
            _export_row(
                exports,
                "术语质量报告",
                "来源覆盖、不同译法、待复核项和质量诊断",
                quality,
            )
        )
        exports_layout.addWidget(
            _export_row(
                exports,
                "当前版本更新日志",
                "面向发布说明和维护审查，可导出 Markdown 与 Excel",
                changelog,
            )
        )
        layout.addWidget(exports)

        recent = QFrame(self)
        recent.setProperty("tbTerminologyCard", True)
        recent_layout = QVBoxLayout(recent)
        recent_layout.setContentsMargins(22, 18, 22, 18)
        recent_title = QLabel("最近生成", recent)
        recent_title.setProperty("tbTerminologySectionTitle", True)
        self.recent_status = QLabel("本次会话尚未生成报告。", recent)
        self.recent_status.setProperty("tbSecondary", True)
        recent_layout.addWidget(recent_title)
        recent_layout.addWidget(self.recent_status)
        layout.addWidget(recent)

        quality.clicked.connect(self.quality_report_requested)
        changelog.clicked.connect(self.changelog_requested)
        self.retry = QPushButton("重试生成更新日志", self)
        self.retry.setProperty("tbComponentKind", "button")
        self.retry.hide()
        self.retry.clicked.connect(self.retry_requested)
        self.notice_title = QLabel("", self)
        self.notice_title.setProperty("tbTerminologySectionTitle", True)
        self.notice_message = QLabel("", self)
        self.notice_message.setWordWrap(True)
        self.details = TechnicalDetailsBox(self)
        layout.addWidget(self.notice_title)
        layout.addWidget(self.notice_message)
        layout.addWidget(self.retry)
        layout.addWidget(self.details)
        layout.addStretch(1)

    def set_notice(self, notice: TerminologyNotice) -> None:
        self.notice_title.setText(notice.title)
        self.notice_message.setText(f"{notice.message}\n影响：{notice.impact}\n恢复方式：{notice.recovery}")
        self.retry.setVisible(notice.retry_label is not None)
        if notice.retry_label:
            self.retry.setText(notice.retry_label)
        self.details.set_details(notice.technical_details)


def _export_row(parent: QWidget, title: str, description: str, action: QPushButton) -> QFrame:
    row = QFrame(parent)
    row.setProperty("tbTerminologyListRow", True)
    row_layout = QHBoxLayout(row)
    row_layout.setContentsMargins(0, 12, 0, 12)
    identity = QVBoxLayout()
    identity.setSpacing(2)
    title_label = QLabel(title, row)
    description_label = QLabel(description, row)
    description_label.setProperty("tbSecondary", True)
    identity.addWidget(title_label)
    identity.addWidget(description_label)
    row_layout.addLayout(identity, 1)
    row_layout.addWidget(action)
    return row


__all__ = ["ReportsView"]
