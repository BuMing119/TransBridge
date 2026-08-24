"""Thin Qt banner and event-driven adapter for state guidance."""

from __future__ import annotations

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from transbridge.config.ui_preferences import GuidanceMode
from transbridge.converter.translation_entry import STAGE_QUESTIONABLE, STAGE_UNTRANSLATED

from .controller import GuidanceController
from .models import GuidanceContextIdentity, GuidanceKind, GuidanceProjection
from .presentation import GuidancePresentation, GuidanceVisibility


class GuidanceBanner(QFrame):
    """A compact, embeddable surface; it owns no business decisions."""

    primary_requested = pyqtSignal(int)
    recovery_requested = pyqtSignal(str)
    collapse_requested = pyqtSignal()
    hide_requested = pyqtSignal()
    restore_requested = pyqtSignal()
    mode_requested = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("guidance-banner")
        self.setAccessibleName("当前任务引导")
        self._revision = -1
        self._recovery_intent = ""
        root = QHBoxLayout(self)
        copy = QVBoxLayout()
        self._headline = QLabel()
        self._headline.setObjectName("guidance-headline")
        self._headline.setAccessibleName("建议的下一步")
        self._reason = QLabel()
        self._reason.setWordWrap(True)
        self._reason.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._reason.setAccessibleName("建议原因")
        copy.addWidget(self._headline)
        copy.addWidget(self._reason)
        root.addLayout(copy, 1)
        self._primary = QPushButton()
        self._primary.setAccessibleName("执行建议的下一步")
        self._primary.clicked.connect(lambda: self.primary_requested.emit(self._revision))
        root.addWidget(self._primary)
        self._recovery = QPushButton()
        self._recovery.setAccessibleName("打开备用或恢复入口")
        self._recovery.clicked.connect(lambda: self.recovery_requested.emit(self._recovery_intent))
        root.addWidget(self._recovery)
        self._mode = QComboBox()
        self._mode.setAccessibleName("引导详细程度")
        self._mode.addItem("自动", GuidanceMode.AUTO.value)
        self._mode.addItem("详细", GuidanceMode.GUIDED.value)
        self._mode.addItem("紧凑", GuidanceMode.COMPACT.value)
        self._mode.currentIndexChanged.connect(self._emit_mode)
        root.addWidget(self._mode)
        self._collapse = QPushButton("收起")
        self._collapse.setAccessibleName("收起或展开引导")
        self._collapse.clicked.connect(self.collapse_requested)
        root.addWidget(self._collapse)
        self._close = QPushButton("隐藏")
        self._close.setAccessibleName("隐藏当前引导")
        self._close.clicked.connect(self.hide_requested)
        root.addWidget(self._close)

    def render(self, value: GuidancePresentation) -> None:
        state = value.state
        self._revision = state.revision
        self._headline.setText(state.headline)
        self._reason.setText("\n".join(value.explanation_lines))
        self._primary.setText(state.primary_intent.label)
        self._primary.setEnabled(state.primary_intent.enabled)
        primary_reason = state.primary_intent.enabled_reason or state.reason
        self._primary.setToolTip(primary_reason)
        self._primary.setAccessibleDescription(primary_reason)
        recovery = state.recovery_intents[0]
        self._recovery_intent = recovery.intent_id.value
        self._recovery.setText(recovery.label)
        self._recovery.setEnabled(recovery.enabled)
        recovery_reason = recovery.enabled_reason or state.reason
        self._recovery.setToolTip(recovery_reason)
        self._recovery.setAccessibleDescription(recovery_reason)
        self._mode.blockSignals(True)
        index = self._mode.findData(value.configured_mode.value)
        self._mode.setCurrentIndex(max(0, index))
        self._mode.blockSignals(False)
        hidden = value.visibility is GuidanceVisibility.HIDDEN
        collapsed = value.visibility is GuidanceVisibility.COLLAPSED
        self._reason.setVisible(not hidden and not collapsed)
        self._primary.setVisible(not hidden)
        self._recovery.setVisible(not hidden and not collapsed)
        self._mode.setVisible(not hidden and not collapsed)
        self._collapse.setText("展开" if collapsed or hidden else "收起")
        try:
            self._collapse.clicked.disconnect()
        except TypeError:
            pass
        self._collapse.clicked.connect(self.restore_requested if collapsed or hidden else self.collapse_requested)
        self._close.setVisible(not hidden)

    def _emit_mode(self) -> None:
        value = self._mode.currentData()
        if value:
            self.mode_requested.emit(str(value))


class GuidanceBinding(QObject):
    """Convert public AppContext events into monotonic guidance projections."""

    def __init__(self, context, view: GuidanceBanner, dispatch, *, preferences=None, parent=None) -> None:
        super().__init__(parent)
        self._context = context
        self._view = view
        self._generation = 0
        self._revision = 0
        self._identity: GuidanceContextIdentity | None = None
        self._controller = GuidanceController(view.render, dispatch, preferences=preferences)
        view.primary_requested.connect(lambda revision: self._controller.submit_primary(expected_revision=revision))
        view.recovery_requested.connect(dispatch)
        view.collapse_requested.connect(self._controller.collapse)
        view.hide_requested.connect(self._controller.hide)
        view.restore_requested.connect(self._controller.restore)
        view.mode_requested.connect(self._set_mode)
        for signal in (
            context.project_changed,
            context.variant_changed,
            context.collection_changed,
            context.collection_list_changed,
            context.dirty_changed,
        ):
            signal.connect(self.refresh)
        self.refresh()

    def refresh(self, *_args) -> None:
        identity = GuidanceContextIdentity(
            project_id=self._context.active_project_id or self._context.project_name,
            version_id=self._context.active_variant_id or self._context.active_variant,
            content_id=self._context.active_key,
        )
        if identity != self._identity:
            self._identity = identity
            self._generation += 1
            self._revision = 0
        else:
            self._revision += 1
        collection = self._context.collection
        if not self._context.project_name:
            kind = GuidanceKind.NO_PROJECT
        elif collection is None or len(collection) == 0:
            kind = GuidanceKind.EMPTY_PROJECT
        elif any(entry.stage == STAGE_QUESTIONABLE for entry in collection):
            kind = GuidanceKind.REVIEW_PENDING
        elif any(entry.stage == STAGE_UNTRANSLATED for entry in collection):
            kind = GuidanceKind.UNTRANSLATED
        else:
            kind = GuidanceKind.PUBLISH_PENDING
        self._controller.project(GuidanceProjection(identity, self._generation, self._revision, kind))

    def close(self) -> None:
        self._controller.close()

    def _set_mode(self, raw: str) -> None:
        result = self._controller.set_mode(GuidanceMode(raw))
        if not result.saved:
            self._view.setToolTip(result.message or "引导显示偏好未能保存")


__all__ = ["GuidanceBanner", "GuidanceBinding"]
