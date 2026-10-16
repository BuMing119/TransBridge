"""Card-based terminology and version pages."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .build_view import BuildView, TechnicalDetailsBox
from .conflicts_view import ConflictsView
from .draft_view import DraftView
from .history_view import HistoryView
from .source_browser import SourceBrowser


class TermsView(QWidget):
    """Present the draft and attention queue as views of one terminology set."""

    def __init__(
        self,
        draft: DraftView,
        conflicts: ConflictsView,
        build: BuildView,
        parent: QWidget | None = None,
        *,
        effective: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 12, 20, 12)
        layout.setSpacing(10)
        self._project_actions = QWidget(self)
        heading = QHBoxLayout(self._project_actions)
        heading.setContentsMargins(0, 0, 0, 0)
        for button in (build.preflight_button, build.cancel_button):
            heading.addWidget(button)
        heading.addWidget(draft.add_button)
        heading.addWidget(build.build_button)
        heading.addStretch(1)
        self.build_details_button = QPushButton("整理状态与结果", self)
        self.build_details_button.setCheckable(True)
        heading.addWidget(self.build_details_button)
        layout.addWidget(self._project_actions)

        self._project_panel = QScrollArea(self)
        self._project_panel.setWidgetResizable(True)
        self._project_panel.setMaximumHeight(210)
        self._project_panel.setWidget(build)
        self._project_panel.hide()
        self.build_details_button.toggled.connect(self._project_panel.setVisible)
        layout.addWidget(self._project_panel)

        choices = QHBoxLayout()
        choices.setSpacing(8)
        self._choice_group = QButtonGroup(self)
        self._choice_group.setExclusive(True)
        self.effective_button = QPushButton("当前生效", self)
        self.all_terms_button = QPushButton("草稿编辑", self)
        self.attention_button = QPushButton("异译审核", self)
        self.source_button = QPushButton("方案映射", self)
        for index, button in enumerate((
            self.effective_button,
            self.all_terms_button,
            self.attention_button,
            self.source_button,
        )):
            button.setCheckable(True)
            button.setProperty("tbTerminologyFilter", True)
            button.clicked.connect(lambda _checked=False, value=index: self._set_page(value))
            self._choice_group.addButton(button, index)
            choices.addWidget(button)
        self.review_button = QPushButton("待复核", self)
        self.review_button.clicked.connect(lambda: self._show_review(draft))
        choices.insertWidget(2, self.review_button)
        choices.addStretch(1)
        layout.addLayout(choices)

        card = QFrame(self)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        self.pages = QStackedWidget(card)
        self.pages.setAccessibleName("术语内容")
        self.source_browser = SourceBrowser(self)
        self.source_browser.draft_requested.connect(lambda: self._set_page(1))
        self.pages.addWidget(effective or QWidget(self))
        self.pages.addWidget(draft)
        self.pages.addWidget(conflicts)
        self.pages.addWidget(self.source_browser)
        card_layout.addWidget(self.pages)
        layout.addWidget(card, 1)
        self._set_page(0)

    def show_attention(self) -> None:
        self._set_page(2)

    def _set_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        button = self._choice_group.button(index)
        if button is not None:
            button.setChecked(True)

    def render_source(self, state) -> None:
        """Show profile state without misrepresenting it as the effective set."""
        self.source_browser.render(state)

    def _show_review(self, draft) -> None:
        self._set_page(1)
        draft.filters.show_review_required()


class VersionsView(QWidget):
    """Keep the effective version, publication, and history in one page."""

    publish_requested = pyqtSignal()
    discard_requested = pyqtSignal()

    def __init__(self, history: HistoryView, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea(self)
        self.scroll.setObjectName("terminologyVersionsScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(self.scroll)

        content = QWidget(self.scroll)
        self.scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(26, 26, 26, 30)
        layout.setSpacing(18)
        title = QLabel("版本", self)
        title.setAccessibleName("版本")
        title.setProperty("tbTerminologyPageTitle", True)
        description = QLabel("查看已发布内容，或把当前调整发布为新版本。", self)
        description.setWordWrap(True)
        description.setProperty("tbSecondary", True)
        layout.addWidget(title)
        layout.addWidget(description)

        current = QFrame(self)
        current.setProperty("tbTerminologySoftCard", True)
        current_layout = QVBoxLayout(current)
        current_layout.setContentsMargins(22, 20, 22, 20)
        current_layout.setSpacing(7)
        current_label = QLabel("当前使用", current)
        current_label.setProperty("tbSecondary", True)
        self.current_version = QLabel("尚无已发布版本", current)
        self.current_version.setProperty("tbTerminologyMetric", True)
        current_description = QLabel("发布后，后续翻译会优先采用此版本。", current)
        current_description.setProperty("tbSecondary", True)
        controls = QHBoxLayout()
        publish = QPushButton("检查影响并发布新版…", current)
        publish.setProperty("tbTerminologyPrimary", True)
        publish.clicked.connect(self.publish_requested)
        controls.addWidget(publish)
        self.discard_button = QPushButton("放弃当前草稿…", current)
        self.discard_button.clicked.connect(self.discard_requested)
        controls.addWidget(self.discard_button)
        controls.addStretch(1)
        self.publish_status = QLabel("", current)
        self.publish_status.setWordWrap(True)
        self.publish_details = TechnicalDetailsBox(current)
        current_layout.addWidget(current_label)
        current_layout.addWidget(self.current_version)
        current_layout.addWidget(current_description)
        current_layout.addLayout(controls)
        current_layout.addWidget(self.publish_status)
        current_layout.addWidget(self.publish_details)
        layout.addWidget(current)
        self._layout = layout
        self._sync_panel: QWidget | None = None

        history_card = QFrame(self)
        history_card.setProperty("tbTerminologyCard", True)
        history_layout = QVBoxLayout(history_card)
        history_layout.setContentsMargins(22, 18, 22, 18)
        history_title = QLabel("历史版本", history_card)
        history_title.setProperty("tbTerminologySectionTitle", True)
        history_description = QLabel("恢复历史内容会创建新版本，不会删除中间记录。", history_card)
        history_description.setProperty("tbSecondary", True)
        history_layout.addWidget(history_title)
        history_layout.addWidget(history_description)
        history_layout.addWidget(history, 1)
        layout.addWidget(history_card, 1)

    def set_sync_panel(self, panel: QWidget) -> None:
        if self._sync_panel is not None:
            self._layout.removeWidget(self._sync_panel)
        self._sync_panel = panel
        self._layout.insertWidget(3, panel)

    def set_context(self, version: str) -> None:
        self.current_version.setText("尚无已发布版本" if not version or version == "尚无" else f"版本 {version}")


__all__ = ["TermsView", "VersionsView"]
