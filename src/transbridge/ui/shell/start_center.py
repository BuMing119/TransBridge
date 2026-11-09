# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Start-center state and the thin Qt view that renders it.

The immutable state is intentionally independent from repository and task
implementations.  The widget only emits stable user intents; callers own file
dialogs, application commands, and navigation side effects.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from transbridge.ui.foundation.accessibility import configure_accessible_widget

from .start_center_landing import StartCenterLanding


class StartDestinationState(StrEnum):
    RESTORING_LAST = "restoring-last"
    START_CENTER_EMPTY = "start-center-empty"
    START_CENTER_RECOVERY_FAILED = "start-center-recovery-failed"
    START_CENTER_USER_REQUESTED = "start-center-user-requested"


@dataclass(frozen=True, slots=True)
class RecentProjectViewState:
    project_key: str
    name: str
    path: str
    available: bool
    reason: str = ""
    active: bool = False

    def __post_init__(self) -> None:
        if not self.project_key.strip() or not self.name.strip():
            raise ValueError("recent project identity and name must not be empty")
        if not self.available and not self.reason.strip():
            raise ValueError("an unavailable recent project requires a reason")


@dataclass(frozen=True, slots=True)
class RecoveryItemViewState:
    storage_key: str
    title: str
    recoverable: bool
    reason: str
    run_id: str | None = None

    def __post_init__(self) -> None:
        if not self.storage_key.strip() or not self.title.strip():
            raise ValueError("recovery identity and title must not be empty")
        if not self.recoverable and not self.reason.strip():
            raise ValueError("an unavailable recovery item requires a reason")


@dataclass(frozen=True, slots=True)
class StartCenterViewState:
    destination: StartDestinationState
    revision: int
    recent_projects: tuple[RecentProjectViewState, ...] = ()
    recovery_items: tuple[RecoveryItemViewState, ...] = ()
    active_project_name: str | None = None
    dirty: bool = False
    diagnostic_code: str = ""
    diagnostic_message: str = ""
    recovery_diagnostic_message: str = ""

    def __post_init__(self) -> None:
        if self.revision < 0:
            raise ValueError("start-center revision must not be negative")
        if self.destination is StartDestinationState.START_CENTER_RECOVERY_FAILED:
            if not self.diagnostic_code.strip() or not self.diagnostic_message.strip():
                raise ValueError("restore failure requires a stable diagnostic")


class StartCenterWidget(QWidget):
    """Render start/draft projections without accessing disk or services."""

    choose_plugin_requested = pyqtSignal()
    open_project_requested = pyqtSignal()
    open_recent_requested = pyqtSignal(str)
    open_recent_in_new_window_requested = pyqtSignal(str)
    recovery_details_requested = pyqtSignal(str)
    task_center_requested = pyqtSignal()
    create_empty_requested = pyqtSignal()
    open_fomod_requested = pyqtSignal()
    return_to_current_requested = pyqtSignal()
    project_name_changed = pyqtSignal(str)
    variant_name_changed = pyqtSignal(str)
    skip_empty_changed = pyqtSignal(bool)
    choose_migration_requested = pyqtSignal()
    add_sources_requested = pyqtSignal()
    source_paths_changed = pyqtSignal(object)
    source_format_changed = pyqtSignal(object)
    translation_options_changed = pyqtSignal(object)
    prepare_requested = pyqtSignal()
    commit_requested = pyqtSignal()
    return_to_landing_requested = pyqtSignal()
    delete_project_requested = pyqtSignal(str, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        configure_accessible_widget(self, name="开始中心", description="选择或恢复本地翻译工程")
        self._state_revision = -1
        self._draft_revision = -1
        self._pages = QStackedWidget(self)
        self._landing_page = self._build_landing_page()
        self._draft_page = self._build_draft_page()
        self._pages.addWidget(self._landing_page)
        self._pages.addWidget(self._draft_page)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._pages)

    def _build_landing_page(self) -> QWidget:
        page = StartCenterLanding(self)
        page.choose_plugin_requested.connect(self.choose_plugin_requested)
        page.open_project_requested.connect(self.open_project_requested)
        page.open_recent_requested.connect(self.open_recent_requested)
        page.open_recent_in_new_window_requested.connect(self.open_recent_in_new_window_requested)
        page.create_empty_requested.connect(self.create_empty_requested)
        page.open_fomod_requested.connect(self.open_fomod_requested)
        page.return_to_current_requested.connect(self.return_to_current_requested)
        page.task_center_requested.connect(self.task_center_requested)
        page.delete_project_requested.connect(self.delete_project_requested)

        # Compatibility names retained for existing coordinators and focused
        # UI contracts while the landing page owns their visual composition.
        self.choose_plugin_button = page.choose_plugin_button
        self._open_button = page._open_button
        self._empty_button = page._empty_button
        self._fomod_button = page._fomod_button
        self._status_label = page._status_label
        self._return_button = page._return_button
        self._recent_list = page._project_list
        self._project_list = page._project_list
        self._projects_empty = page._projects_empty
        self._recovery_banner = page._recovery_banner
        self._task_center_button = page._task_center_button
        self._content = page._content
        self._task_panel = page._task_panel
        self._projects_panel = page._projects_panel
        self._project_open_progress = page._projects_panel.progress_container
        self._project_open_progress_bar = page._projects_panel.progress_bar
        return page

    def set_creation_available(self, available: bool, reason: str = "") -> None:
        """Apply the guided-project capability without losing it on rerender."""

        self._landing_page.set_creation_available(available, reason)

    def set_project_opening(self, message: str | None) -> None:
        """Show or clear start-center feedback for a current-window switch."""

        self._landing_page.set_project_opening(message)

    def _build_draft_page(self) -> QWidget:
        from .project_creation_form import ProjectCreationForm

        page = ProjectCreationForm(self)
        page.back_requested.connect(self.return_to_landing_requested)
        page.add_sources_requested.connect(self.add_sources_requested)
        page.sources_changed.connect(self.source_paths_changed)
        page.source_format_changed.connect(self.source_format_changed)
        page.project_name_changed.connect(self.project_name_changed)
        page.variant_name_changed.connect(self.variant_name_changed)
        page.skip_empty_changed.connect(self.skip_empty_changed)
        page.choose_translation_requested.connect(self.choose_migration_requested)
        page.translation_options_changed.connect(self.translation_options_changed)
        page.create_requested.connect(self.prepare_requested)
        # Existing focused contracts and keyboard navigation keep these names.
        self._draft_back = page.back
        self._source_label = page.source_label
        self._source_list = page.source_list
        self._name_edit = page.name_edit
        self._variant_edit = page.variant_edit
        self._migration_label = page.translation_path
        self._migration_button = page.translation_browse
        self._skip_empty = page.skip_empty
        self._draft_summary = page.summary
        self._draft_error = page.error
        self._draft_primary = page.primary
        return page

    def render(self, state: StartCenterViewState) -> None:
        if state.revision < self._state_revision:
            return
        self._state_revision = state.revision
        self._pages.setCurrentWidget(self._landing_page)
        self._landing_page.render(state)

    def render_draft(self, state) -> None:
        """Render a GuidedProjectDraftState via its narrow public fields."""

        if state.revision < self._draft_revision:
            return
        entering_draft = self._pages.currentWidget() is not self._draft_page
        previous_diagnostic = self._draft_error.text()
        self._draft_revision = state.revision
        self._pages.setCurrentWidget(self._draft_page)
        self._draft_page.render(state)
        diagnostic = self._draft_error.text()
        # Ordinary draft updates must preserve the user's editing focus.
        if diagnostic and (entering_draft or diagnostic != previous_diagnostic):
            self._draft_error.setFocus(Qt.FocusReason.OtherFocusReason)
        elif entering_draft:
            if self._draft_primary.isEnabled():
                self._draft_primary.setFocus(Qt.FocusReason.OtherFocusReason)
            else:
                self._name_edit.setFocus(Qt.FocusReason.OtherFocusReason)

    def _emit_draft_primary(self) -> None:
        self.prepare_requested.emit()

    def _activate_draft_primary(self) -> None:
        if self._pages.currentWidget() is self._draft_page and self._draft_primary.isEnabled():
            self._draft_primary.click()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt override
        if event.key() == Qt.Key.Key_Escape and self._pages.currentWidget() is self._draft_page:
            if self._draft_back.isEnabled():
                self.return_to_landing_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


__all__ = [
    "RecentProjectViewState",
    "RecoveryItemViewState",
    "StartCenterViewState",
    "StartCenterWidget",
    "StartDestinationState",
]
