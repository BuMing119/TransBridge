# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Progressive disclosure for Workbench project settings."""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from transbridge.ui.foundation.components import (
    ComponentDensity,
    ComponentKind,
    ComponentStyle,
    ElidedLabel,
    SemanticState,
)
from transbridge.ui.workbench.remote_target_presenter import RemoteTargetViewState
from transbridge.ui.workbench.remote_target_view import RemoteTargetView
from transbridge.ui.workbench.terminology_profile_bar import TerminologyProfileBar


class ProjectConfigurationView(QWidget):
    def __init__(self, remote_target: RemoteTargetView, profile_bar: TerminologyProfileBar | None, parent=None) -> None:
        super().__init__(parent)
        self._profile_bar = profile_bar
        self._remote_state = remote_target.state
        self.action = QAction("项目配置…", self)
        self.action.triggered.connect(self.expand)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self._profile_notice = ElidedLabel()
        self._profile_notice.setTextFormat(Qt.TextFormat.PlainText)
        self._profile_notice.setAccessibleName("译名方案与定位状态")
        layout.addWidget(self._profile_notice)
        self._target_notice = ElidedLabel()
        self._target_notice.setTextFormat(Qt.TextFormat.PlainText)
        self._target_notice.setAccessibleName("ParaTranz 同步目标异常")
        layout.addWidget(self._target_notice)
        self.panel = QFrame()
        ComponentStyle.apply_static(self.panel, ComponentKind.CARD)
        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(8, 4, 8, 4)
        panel_layout.setSpacing(6)
        panel_header = QHBoxLayout()
        panel_header.addStretch()
        self._collapse = QPushButton("收起项目配置")
        ComponentStyle.apply_static(self._collapse, ComponentKind.BUTTON, ComponentDensity.COMPACT)
        self._collapse.clicked.connect(self._close_panel)
        panel_header.addWidget(self._collapse)
        panel_layout.addLayout(panel_header)
        if profile_bar is not None:
            panel_layout.addWidget(profile_bar)
            profile_bar.status_changed.connect(self._render)
        panel_layout.addWidget(remote_target)
        remote_target.state_changed.connect(self._render_target)
        layout.addWidget(self.panel)
        self._close_panel()

    def expand(self) -> None:
        self.panel.show()
        self._render()
        self._collapse.setFocus()

    def _close_panel(self) -> None:
        self.panel.hide()
        self._render()

    def _render(self) -> None:
        collapsed = self.panel.isHidden()
        target = self._remote_state
        target_summary = target.summary or target.title
        target_alert = target.can_clear and target.semantic_state in {"warning", "error"}
        target_detail = f"{target_summary}\n{target.detail}" if target.detail else target_summary
        self._target_notice.set_full_text(target_summary)
        self._target_notice.setToolTip(target_detail)
        self._target_notice.setAccessibleDescription(target_detail)
        ComponentStyle.apply_state(self._target_notice, SemanticState(target.semantic_state))
        self._target_notice.setVisible(collapsed and target_alert)

        summary, profile_notice, profile_detail, semantic = self._profile_presentation()
        self.action.setToolTip("\n".join(filter(None, (summary, target_detail))))
        self._profile_notice.set_full_text(profile_notice)
        self._profile_notice.setToolTip(profile_detail)
        self._profile_notice.setAccessibleDescription(profile_detail)
        ComponentStyle.apply_state(self._profile_notice, semantic)
        self._profile_notice.setVisible(collapsed and bool(profile_notice))
        self.setVisible(not collapsed or bool(profile_notice) or target_alert)

    def _profile_presentation(self) -> tuple[str, str, str, SemanticState]:
        bar = self._profile_bar
        if bar is None:
            return "", "", "", SemanticState.DEFAULT
        state = bar.state
        selected = state.selected_revision.name if state.selected_revision is not None else "项目默认"
        applied = state.applied_revision.name if state.applied_revision is not None else "项目默认"
        summary = f"译名 · {applied}"
        if state.load_error or state.selection_error:
            return summary, state.detail, bar.detail.full_text, SemanticState.ERROR
        if state.loading:
            return summary, state.detail, bar.detail.full_text, SemanticState.INFO
        if state.is_preview:
            notice = f"译名预览未应用 · {selected}（当前版本使用 {applied}）"
            if bar.alignment_detail:
                notice = f"{notice} · {bar.alignment_detail}"
            return summary, notice, notice, SemanticState.INFO
        return summary, bar.alignment_detail, bar.detail.full_text, SemanticState.DEFAULT

    def _render_target(self, state: RemoteTargetViewState) -> None:
        self._remote_state = state
        self._render()
