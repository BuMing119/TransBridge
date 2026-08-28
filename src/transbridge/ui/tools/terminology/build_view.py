"""Overview page for the project terminology workbench."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
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
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(self)
        scroll.setObjectName("terminologyOverviewScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(scroll)
        content = QWidget(scroll)
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(26, 26, 26, 32)
        layout.setSpacing(18)

        heading = QHBoxLayout()
        heading_text = QVBoxLayout()
        heading_text.setSpacing(3)
        page_title = QLabel("术语库", content)
        page_title.setAccessibleName("术语库")
        page_title.setProperty("tbTerminologyPageTitle", True)
        page_description = QLabel("项目中的推荐译名、使用范围和历史版本。", content)
        page_description.setProperty("tbSecondary", True)
        heading_text.addWidget(page_title)
        heading_text.addWidget(page_description)
        heading.addLayout(heading_text)
        heading.addStretch(1)
        self.preflight_button = QPushButton("重新检查", content)
        self.build_button = QPushButton("创建术语库", content)
        self.build_button.setProperty("tbTerminologyPrimary", True)
        self.build_button.setEnabled(False)
        self.cancel_button = QPushButton("停止", content)
        self.cancel_button.setEnabled(False)
        self.cancel_button.hide()
        for button in (self.preflight_button, self.cancel_button):
            _style_button(button)
        heading.addWidget(self.preflight_button)
        heading.addWidget(self.build_button)
        heading.addWidget(self.cancel_button)
        layout.addLayout(heading)

        self.alert = QFrame(content)
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

        self.progress = QProgressBar(content)
        self.progress.setProperty("tbComponentKind", "progress")
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.hide()
        self.progress_message = QLabel("", content)
        self.progress_message.setWordWrap(True)
        self.progress_message.hide()
        layout.addWidget(self.progress)
        layout.addWidget(self.progress_message)

        self.term_metric, self.term_caption = self._metric_card(layout, "术语", "—", "构建后显示项目可用术语")
        self.attention_metric, self.attention_caption = self._metric_card(layout, "需要关注", "—", "构建后显示不同译法")
        self.version_metric, self.version_caption = self._metric_card(layout, "当前版本", "尚无", "尚无已发布版本")

        self.result_group = _card(content)
        result_layout = QVBoxLayout(self.result_group)
        result_layout.setContentsMargins(20, 18, 20, 18)
        result_layout.setSpacing(8)
        result_title = QLabel("需要你关注", self.result_group)
        result_title.setProperty("tbTerminologySectionTitle", True)
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
        result_layout.addWidget(result_title)
        result_layout.addWidget(self.result)
        result_layout.addWidget(self.decisions)
        result_layout.addWidget(self.impact)
        result_layout.addWidget(self.next_action)
        result_layout.addLayout(result_actions)
        layout.addWidget(self.result_group)

        coverage = _card(content)
        coverage_layout = QVBoxLayout(coverage)
        coverage_layout.setContentsMargins(20, 18, 20, 18)
        coverage_layout.setSpacing(10)
        coverage_title = QLabel("覆盖范围", coverage)
        coverage_title.setProperty("tbTerminologySectionTitle", True)
        coverage_description = QLabel("系统会自动处理这些来源，无需逐步操作。", coverage)
        coverage_description.setProperty("tbSecondary", True)
        self.llm_enabled = QCheckBox("为普通文本补充 AI 术语提取（可选）", coverage)
        self.sources = QWidget(coverage)
        self.sources_layout = QVBoxLayout(self.sources)
        self.sources_layout.setContentsMargins(0, 4, 0, 0)
        self.sources_layout.setSpacing(0)
        coverage_layout.addWidget(coverage_title)
        coverage_layout.addWidget(coverage_description)
        coverage_layout.addWidget(self.llm_enabled)
        coverage_layout.addWidget(self.sources)
        layout.addWidget(coverage)
        layout.addStretch(1)

        self.project_label = QLabel("", content)
        self.variant_label = QLabel("", content)
        self.scope_label = QLabel("", content)
        self.current_version_label = QLabel("", content)
        self.scale_label = QLabel("", content)
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

    def _metric_card(self, parent_layout: QVBoxLayout, label: str, value: str, caption: str) -> tuple[QLabel, QLabel]:
        card = QFrame(self)
        card.setProperty("tbTerminologySoftCard", True)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 16, 18, 16)
        card_layout.setSpacing(3)
        label_widget = QLabel(label, card)
        label_widget.setProperty("tbSecondary", True)
        value_widget = QLabel(value, card)
        value_widget.setProperty("tbTerminologyMetric", True)
        caption_widget = QLabel(caption, card)
        caption_widget.setProperty("tbSecondary", True)
        card_layout.addWidget(label_widget)
        card_layout.addWidget(value_widget)
        card_layout.addWidget(caption_widget)
        parent_layout.addWidget(card)
        return value_widget, caption_widget

    def set_preflight(self, state: TerminologyPreflightViewState) -> None:
        self.title.setText(state.title)
        self.message.setText(state.message)
        self.project_label.setText(state.project_label or "当前工程 · 未就绪")
        self.variant_label.setText(state.variant_label or "翻译版本 · 未就绪")
        self.scope_label.setText(state.scope_label or "来源范围 · 未就绪")
        self.current_version_label.setText(state.current_version_label or "当前版本 · 未就绪")
        self.scale_label.setText(f"预计规模 · {state.expected_scale_label}")
        self.version_metric.setText(state.current_version_value or "尚无")
        self.version_caption.setText(state.current_version_label or "尚无已发布版本")
        self.build_button.setText(state.action_label)
        self.build_button.setAccessibleName(state.action_label)
        self.build_button.setEnabled(state.ready)
        self.details.set_details(state.technical_details)
        self._set_sources(state)

    def set_summary(self, state: TerminologySummaryViewState) -> None:
        self.result.setText(state.result)
        self.decisions.setText(state.decisions)
        self.impact.setText(f"发布影响：{state.impact}")
        self.next_action.setText(f"下一步：{state.next_action}")
        if state.term_count is not None:
            self.term_metric.setText(f"{state.term_count:,} 条")
            self.term_caption.setText("当前构建整理出的术语候选")
        if state.attention_count is not None:
            self.attention_metric.setText(f"{state.attention_count} 项")
            self.attention_caption.setText("存在多个译法" if state.attention_count else "当前没有需要决定的异译")
        self.details.set_details(state.technical_details)

    def set_task_progress(self, status: str, detail: str, *, completed: int, total: int, terminal: bool) -> None:
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

    def _set_sources(self, state: TerminologyPreflightViewState) -> None:
        while self.sources_layout.count():
            item = self.sources_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if not state.sources:
            empty = QLabel("当前没有可展示的已启用来源。", self.sources)
            empty.setProperty("tbSecondary", True)
            self.sources_layout.addWidget(empty)
            return
        for source in state.sources:
            row = QFrame(self.sources)
            row.setProperty("tbTerminologyListRow", True)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 10, 0, 10)
            identity = QVBoxLayout()
            identity.setSpacing(1)
            name = QLabel(source.name, row)
            detail = QLabel(source.format_label, row)
            detail.setProperty("tbSecondary", True)
            identity.addWidget(name)
            identity.addWidget(detail)
            status = QLabel("可用", row)
            status.setProperty("tbSemanticState", "success")
            row_layout.addLayout(identity, 1)
            row_layout.addWidget(status)
            self.sources_layout.addWidget(row)


def _card(parent: QWidget) -> QFrame:
    card = QFrame(parent)
    card.setProperty("tbTerminologyCard", True)
    return card


def _style_button(button: QPushButton) -> None:
    button.setProperty("tbComponentKind", "button")


__all__ = ["BuildView", "TechnicalDetailsBox"]
