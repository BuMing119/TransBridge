"""Overview page for the project terminology workbench."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .view_models import TechnicalDetail, TerminologyPreflightViewState, TerminologySummaryViewState


class TechnicalDetailsBox(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 0)
        self.toggle = QPushButton("技术详情", self)
        self.toggle.setCheckable(True)
        _style_button(self.toggle)
        self.text = QPlainTextEdit(self)
        self.text.setReadOnly(True)
        self.text.setMaximumHeight(150)
        copy = QPushButton("复制技术详情", self)
        _style_button(copy)
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.text.toPlainText()))
        layout.addWidget(self.toggle, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.text)
        layout.addWidget(copy)
        self.toggle.toggled.connect(self.text.setVisible)
        self.toggle.toggled.connect(copy.setVisible)
        self.text.hide()
        copy.hide()
        self.hide()

    def set_details(self, details: tuple[TechnicalDetail, ...]) -> None:
        self.setVisible(bool(details))
        self.text.setPlainText("\n".join(f"{item.label}: {item.value}" for item in details))


class BuildView(QWidget):
    preflight_requested = pyqtSignal()
    build_requested = pyqtSignal()
    cancel_requested = pyqtSignal()
    terms_requested = pyqtSignal()
    versions_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.preflight_button = QPushButton("重新检查", self)
        self.preflight_button.hide()
        self.build_button = QPushButton("创建术语库", self)
        self.build_button.setProperty("tbTerminologyPrimary", True)
        self.build_button.setEnabled(False)
        self.cancel_button = QPushButton("停止", self)
        self.cancel_button.setEnabled(False)
        self.cancel_button.hide()
        for button in (self.preflight_button, self.cancel_button):
            _style_button(button)

        self.alert = QFrame(self)
        self.alert.setProperty("tbTerminologyAlert", True)
        alert_layout = QVBoxLayout(self.alert)
        alert_layout.setContentsMargins(18, 15, 18, 15)
        alert_layout.setSpacing(5)
        self.title = QLabel("尚未检查当前工程", self.alert)
        self.title.setProperty("tbTerminologySectionTitle", True)
        self.title.setAccessibleName("术语库状态")
        self.message = QLabel("检查后会显示当前项目、翻译版本和覆盖范围。", self.alert)
        self.message.setWordWrap(True)
        self.details = TechnicalDetailsBox(self.alert)
        alert_layout.addWidget(self.title)
        alert_layout.addWidget(self.message)
        alert_layout.addWidget(self.details)
        layout.addWidget(self.alert)
        self.alert.hide()

        self.progress = QProgressBar(self)
        self.progress.setProperty("tbComponentKind", "progress")
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.hide()
        self.progress_message = QLabel("", self)
        self.progress_message.setWordWrap(True)
        self.progress_message.hide()
        layout.addWidget(self.progress)
        layout.addWidget(self.progress_message)

        self.metrics = QWidget(self)
        metrics_layout = QHBoxLayout(self.metrics)
        metrics_layout.setContentsMargins(0, 0, 0, 0)
        metrics_layout.setSpacing(12)
        self.term_card, self.term_metric = self._metric_card(
            metrics_layout,
            "术语",
            "—",
        )
        self.attention_card, self.attention_metric = self._metric_card(
            metrics_layout,
            "待处理",
            "—",
        )
        self.metrics.hide()
        layout.addWidget(self.metrics)

        self.result_group = _card(self)
        result_layout = QVBoxLayout(self.result_group)
        result_layout.setContentsMargins(20, 18, 20, 18)
        result_layout.setSpacing(8)
        self.result_title = QLabel("整理结果", self.result_group)
        self.result_title.setProperty("tbTerminologySectionTitle", True)
        self.result = QLabel("尚未构建", self.result_group)
        self.decisions = QLabel("", self.result_group)
        self.impact = QLabel("", self.result_group)
        self.next_action = QLabel("", self.result_group)
        for label in (self.result, self.decisions, self.impact, self.next_action):
            label.setWordWrap(True)
        result_actions = QHBoxLayout()
        view_terms = QPushButton("查看术语", self.result_group)
        view_versions = QPushButton("查看版本", self.result_group)
        _style_button(view_terms)
        _style_button(view_versions)
        view_terms.clicked.connect(self.terms_requested)
        view_versions.clicked.connect(self.versions_requested)
        result_actions.addWidget(view_terms)
        result_actions.addWidget(view_versions)
        result_actions.addStretch(1)
        result_layout.addWidget(self.result_title)
        result_layout.addWidget(self.result)
        result_layout.addWidget(self.decisions)
        result_layout.addWidget(self.impact)
        result_layout.addWidget(self.next_action)
        self.result_details = TechnicalDetailsBox(self.result_group)
        result_layout.addWidget(self.result_details)
        result_layout.addLayout(result_actions)
        layout.addWidget(self.result_group)
        self.result_group.hide()
        layout.addStretch(1)

        self.project_label = QLabel("", self)
        self.variant_label = QLabel("", self)
        self.scope_label = QLabel("", self)
        self.current_version_label = QLabel("", self)
        self.scale_label = QLabel("", self)
        for label in (
            self.project_label,
            self.variant_label,
            self.scope_label,
            self.current_version_label,
            self.scale_label,
        ):
            label.hide()

        self.preflight_button.clicked.connect(self.preflight_requested)
        self.build_button.clicked.connect(self.build_requested)
        self.cancel_button.clicked.connect(self.cancel_requested)
        self.hide()

    def _metric_card(
        self,
        parent_layout: QHBoxLayout,
        label: str,
        value: str,
    ) -> tuple[QFrame, QLabel]:
        card = QFrame(self)
        card.setProperty("tbTerminologySoftCard", True)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 16, 18, 16)
        card_layout.setSpacing(3)
        label_widget = QLabel(label, card)
        label_widget.setProperty("tbSecondary", True)
        value_widget = QLabel(value, card)
        value_widget.setProperty("tbTerminologyMetric", True)
        card_layout.addWidget(label_widget)
        card_layout.addWidget(value_widget)
        parent_layout.addWidget(card, 1)
        return card, value_widget

    def set_preflight(self, state: TerminologyPreflightViewState) -> None:
        self.title.setText(state.title)
        self.message.setText(state.message)
        self.project_label.setText(state.project_label or "当前工程 · 未就绪")
        self.variant_label.setText(state.variant_label or "翻译版本 · 未就绪")
        self.scope_label.setText(state.scope_label or "来源范围 · 未就绪")
        self.current_version_label.setText(state.current_version_label or "当前版本 · 未就绪")
        self.scale_label.setText(f"预计规模 · {state.expected_scale_label}")
        self.build_button.setText(state.action_label)
        self.build_button.setAccessibleName(state.action_label)
        self.build_button.setEnabled(state.ready)
        self.preflight_button.setText("重试检查")
        self.preflight_button.setVisible(not state.ready)
        self.alert.setVisible(not state.ready)
        self.details.set_details(state.technical_details)
        self.setVisible(not state.ready or not self.result_group.isHidden())

    def set_summary(self, state: TerminologySummaryViewState) -> None:
        self.show()
        self.progress.hide()
        self.progress_message.hide()
        self.cancel_button.hide()
        self.result_group.show()
        self.result_title.setText("待处理" if state.attention_count else "已完成")
        self.result.setText(state.result)
        self.decisions.setText(state.decisions)
        self.decisions.setVisible(bool(state.attention_count))
        self.impact.setText(state.impact)
        self.impact.setVisible(state.is_partial or state.is_stale)
        self.next_action.setText(state.next_action)
        self.next_action.setVisible(state.is_partial or state.is_stale)
        self.term_card.setVisible(state.term_count is not None)
        self.attention_card.setVisible(state.attention_count is not None)
        if state.term_count is not None:
            self.term_metric.setText(f"{state.term_count:,} 条")
        if state.attention_count is not None:
            self.attention_metric.setText(f"{state.attention_count} 项")
        self.metrics.setVisible(state.term_count is not None or state.attention_count is not None)
        self.result_details.set_details(state.technical_details)

    def set_task_progress(self, status: str, detail: str, *, completed: int, total: int, terminal: bool) -> None:
        self.show()
        self.progress.show()
        self.progress_message.show()
        self.cancel_button.setVisible(not terminal)
        self.progress_message.setText(f"{status} · {detail}")
        if total:
            self.progress.setRange(0, total)
            self.progress.setValue(min(completed, total))
        else:
            self.progress.setRange(0, 0 if not terminal else 1)
            if terminal:
                self.progress.setValue(1)
        self.cancel_button.setEnabled(not terminal)


def _card(parent: QWidget) -> QFrame:
    card = QFrame(parent)
    card.setProperty("tbTerminologyCard", True)
    return card


def _style_button(button: QPushButton) -> None:
    button.setProperty("tbComponentKind", "button")


__all__ = ["BuildView", "TechnicalDetailsBox"]
