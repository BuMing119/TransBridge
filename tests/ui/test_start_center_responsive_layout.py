"""Short windows keep the start-center actions reachable without growing the shell."""

from dataclasses import replace

from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QBoxLayout
import pytest

from tests.ui.responsive_layout_support import settle_layout, themed_layout_app as themed_layout_app
from transbridge.ui.coordinators.guided_project_coordinator import GuidedProjectDraftState
from transbridge.ui.shell.start_center import (
    RecentProjectViewState,
    RecoveryItemViewState,
    StartCenterViewState,
    StartCenterWidget,
    StartDestinationState,
)


@pytest.mark.parametrize("size", [(850, 650), (720, 580), (1130, 650)])
@pytest.mark.parametrize("has_projects", [False, True])
def test_short_start_center_keeps_window_size_and_bottom_actions_reachable(layout_app, size, has_projects):
    view = StartCenterWidget()
    projects = (RecentProjectViewState("p", "测试工程", "D:/example/demo.json", True),) if has_projects else ()
    view.render(
        StartCenterViewState(
            StartDestinationState.START_CENTER_EMPTY,
            revision=1,
            recent_projects=projects,
            recovery_items=(RecoveryItemViewState("task", "继续翻译", True, ""),),
        )
    )
    view.resize(*size)
    view.show()
    try:
        settle_layout(layout_app)
        assert (view.width(), view.height()) == size
        scroll = view._landing_page._scroll_area
        assert scroll.horizontalScrollBar().maximum() == 0
        assert scroll.verticalScrollBar().maximum() > 0
        scroll.ensureWidgetVisible(view._task_center_button)
        settle_layout(layout_app)
        button = view._task_center_button
        rectangle = button.rect().translated(button.mapTo(scroll.viewport(), QPoint()))
        assert scroll.viewport().rect().contains(rectangle)
    finally:
        view.close()


@pytest.mark.parametrize("path_length", [20, 80])
def test_long_translation_path_does_not_grow_or_lock_window_width(layout_app, path_length):
    view = StartCenterWidget()
    state = GuidedProjectDraftState("D:/mods/Base.esm", "Base")
    view.render_draft(state)
    view.resize(850, 750)
    view.show()
    try:
        settle_layout(layout_app)
        path = "D:/" + "很长的译文目录/" * path_length + "Plugin.esm"
        view.render_draft(replace(state, import_translations=True, translation_paths=(path,), revision=1))
        settle_layout(layout_app)
        assert view.width() == 850
        for width in (1200, 720, 850):
            view.resize(width, 750)
            settle_layout(layout_app)
            assert view.width() == width
            button = view._migration_button
            assert view.rect().contains(button.rect().translated(button.mapTo(view, QPoint())))
        assert view._migration_label.toolTip() == path
        assert view._migration_label.text() == path

        view.render(StartCenterViewState(StartDestinationState.START_CENTER_USER_REQUESTED, revision=1))
        view.resize(720, 750)
        settle_layout(layout_app)
        assert view.width() == 720

        view.render_draft(replace(state, revision=2))
        settle_layout(layout_app)
        assert view._migration_label.text() == ""
        assert view._migration_label.toolTip() == ""
    finally:
        view.close()


def test_start_center_reflows_from_viewport_and_settles_at_breakpoint(layout_app):
    view = StartCenterWidget()
    view.render(StartCenterViewState(StartDestinationState.START_CENTER_EMPTY, revision=1))
    view.show()
    try:
        landing = view._landing_page
        for width in (1920, 1050, 1039, 720, 1060, 1920):
            view.resize(width, 650)
            settle_layout(layout_app)
            scroll = landing._scroll_area
            compact = scroll.viewport().width() < 1040
            assert landing._is_compact == compact
            assert landing._body_layout.direction() == (
                QBoxLayout.Direction.TopToBottom if compact else QBoxLayout.Direction.LeftToRight
            )
            assert view.width() == width
            assert view.height() == 650
            assert scroll.horizontalScrollBar().maximum() == 0
            geometry = (scroll.viewport().size(), scroll.widget().size(), scroll.verticalScrollBar().maximum())
            settle_layout(layout_app)
            assert geometry == (scroll.viewport().size(), scroll.widget().size(), scroll.verticalScrollBar().maximum())
    finally:
        view.close()
