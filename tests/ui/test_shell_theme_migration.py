from __future__ import annotations

import os
from pathlib import Path
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.config.ui_preferences import GuidanceMode
from transbridge.ui.drop_review import DropReviewDialog
from transbridge.ui.drop_router import DropResolution
from transbridge.ui.foundation.builtins import DEFAULT_THEME_ID, create_builtin_registry
from transbridge.ui.foundation.model import ThemeScheme
from transbridge.ui.foundation.qt_palette import compile_palette
from transbridge.ui.guidance.models import (
    GuidanceContextIdentity,
    GuidanceIntent,
    GuidanceKind,
    GuidanceState,
)
from transbridge.ui.guidance.presentation import present_guidance
from transbridge.ui.guidance.qt import GuidanceBanner
from transbridge.ui.shell.action_catalog import DEFAULT_ACTION_CATALOG, IntentId
from transbridge.ui.shell.command_palette import CommandPaletteController, CommandPaletteModel
from transbridge.ui.shell.command_palette_qt import CommandPaletteDialog
from transbridge.ui.shell.context_help import DEFAULT_CONTEXT_HELP, ContextHelpController
from transbridge.ui.shell.context_help_qt import ContextHelpPanel
from transbridge.ui.shell.start_center import StartCenterViewState, StartCenterWidget, StartDestinationState
from transbridge.ui.shell.status_presenter import ApiStatusIndicator
from transbridge.ui.shell.task_center import TaskCenterPanel


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _available_actions():
    return tuple(
        DEFAULT_ACTION_CATALOG.availability(item.intent_id, enabled=True) for item in DEFAULT_ACTION_CATALOG.all()
    )


def _guidance() -> GuidanceState:
    return GuidanceState(
        GuidanceContextIdentity(project_id="project-one", content_id="content-one"),
        1,
        1,
        GuidanceKind.REVIEW_PENDING,
        "检查翻译问题",
        "有词条需要人工复核",
        GuidanceIntent(IntentId.TRANSLATION_REVIEW, "开始复核"),
        (GuidanceIntent(IntentId.WORKBENCH_MANAGE, "返回内容管理"),),
    )


def test_shell_sources_have_no_raw_theme_colours_or_colour_stylesheets() -> None:
    root = Path(__file__).resolve().parents[2]
    sources = [
        *sorted((root / "src/transbridge/ui/shell").glob("*.py")),
        *sorted((root / "src/transbridge/ui/guidance").glob("*.py")),
        root / "src/transbridge/ui/drop_review.py",
    ]
    colour = re.compile(r"#[0-9a-fA-F]{3,8}\b|\bQColor\s*\(|(?:background|foreground|selection|border)?-?color\s*:")

    findings = [
        f"{path.relative_to(root)}:{line_number}"
        for path in sources
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if colour.search(line)
    ]

    assert findings == []


def test_palette_switch_updates_existing_shell_surfaces_without_reconstruction_or_state_loss(qapp) -> None:
    original_palette = QPalette(qapp.palette())
    registry = create_builtin_registry()
    light = compile_palette(registry.resolve(DEFAULT_THEME_ID, ThemeScheme.LIGHT))
    dark = compile_palette(registry.resolve(DEFAULT_THEME_ID, ThemeScheme.DARK))
    qapp.setPalette(light)

    start_center = StartCenterWidget()
    start_center.show()
    start_center.render(StartCenterViewState(StartDestinationState.START_CENTER_EMPTY, 1))
    guidance = GuidanceBanner()
    guidance.render(present_guidance(_guidance(), GuidanceMode.GUIDED))
    task_center = TaskCenterPanel()
    palette_controller = CommandPaletteController(CommandPaletteModel(_available_actions))
    command_palette = CommandPaletteDialog(palette_controller)
    command_palette.open_palette("关于")
    context_controller = ContextHelpController(DEFAULT_CONTEXT_HELP)
    context_help = ContextHelpPanel(context_controller)
    context_help.show_topic("task", context_identity="project-one:content-one")
    drop_review = DropReviewDialog()
    drop_review.review(DropResolution.cancelled())
    qapp.processEvents()

    surfaces = (start_center, guidance, task_center, command_palette, context_help, drop_review)
    identities = tuple(id(surface) for surface in surfaces)
    window_colours = tuple(surface.palette().color(QPalette.ColorRole.Window) for surface in surfaces)
    result_id = command_palette._results.currentItem().data(Qt.ItemDataRole.UserRole)
    command_signature = _guidance().command_signature
    focus_widget = start_center.focusWidget()

    qapp.setPalette(dark)
    qapp.processEvents()

    try:
        assert tuple(id(surface) for surface in surfaces) == identities
        assert all(
            surface.palette().color(QPalette.ColorRole.Window) != before
            for surface, before in zip(surfaces, window_colours)
        )
        assert command_palette._results.currentItem().data(Qt.ItemDataRole.UserRole) == result_id
        assert _guidance().command_signature == command_signature
        assert start_center.focusWidget() is focus_widget is start_center.choose_plugin_button
        assert start_center._state_revision == 1
        assert palette_controller.is_open
        assert context_controller.current is not None
    finally:
        for surface in reversed(surfaces):
            surface.close()
        qapp.setPalette(original_palette)
        qapp.processEvents()


def test_shell_statuses_are_text_first_and_expose_semantic_accessible_state(qapp) -> None:
    indicator = ApiStatusIndicator()
    assert indicator.property("tbStatusId") == "healthy"
    assert indicator.property("tbSemanticState") == "success"
    assert indicator.text() == "● 正常"

    indicator.on_request_started()
    assert indicator.property("tbStatusId") == "requesting"
    assert "请求中" in indicator.text()
    assert indicator.accessibleDescription() == indicator.text()

    indicator.on_request_finished(False)
    assert indicator.property("tbStatusId") == "failed"
    assert indicator.property("tbSemanticState") == "error"
    assert indicator.text() == "⚠ 异常"

    guidance = GuidanceBanner()
    guidance.render(present_guidance(_guidance(), GuidanceMode.GUIDED))
    assert guidance.property("tbStatusId") == GuidanceKind.REVIEW_PENDING.value
    assert guidance.property("tbSemanticState") == "warning"
    assert "检查翻译问题" in guidance.accessibleDescription()

    start_center = StartCenterWidget()
    start_center.render(
        StartCenterViewState(
            StartDestinationState.START_CENTER_RECOVERY_FAILED,
            1,
            diagnostic_code="restore_failed",
            diagnostic_message="工程已移动",
        )
    )
    assert start_center._status_label.property("tbSemanticState") == "error"
    assert "工程已移动" in start_center._status_label.accessibleDescription()

    indicator._timer.stop()
    indicator.close()
    guidance.close()
    start_center.close()
