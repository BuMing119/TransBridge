from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QTableWidget

from transbridge.config.ui_preferences import GuidanceMode
from transbridge.converter.translation_entry import STAGE_LABELS, STAGE_QUESTIONABLE, TranslationEntry
from transbridge.ui.drop_review import DropReviewDialog
from transbridge.ui.drop_router import DropRouter
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
from transbridge.ui.shell.task_center import TaskCenterPanel
from transbridge.ui.workbench.filters_view import FiltersView
from transbridge.ui.workbench.table_presenter import RenderSession
from transbridge.ui.workbench.translation_table import COL_CONTEXT, TranslationTable
from transbridge.ui.workbench.workflow_actions_view import WorkflowActionsView
from transbridge.ui.workbench.workflow_presenter import WorkbenchWorkflowPresenter

_APP = QApplication.instance() or QApplication([])


def _available_actions():
    return tuple(
        DEFAULT_ACTION_CATALOG.availability(item.intent_id, enabled=True) for item in DEFAULT_ACTION_CATALOG.all()
    )


def test_start_center_default_focus_enter_and_escape_are_local_navigation() -> None:
    widget = StartCenterWidget()
    widget.show()
    widget.render(StartCenterViewState(StartDestinationState.START_CENTER_EMPTY, 1))
    _APP.processEvents()

    assert widget.accessibleName() == "开始中心"
    assert widget.focusWidget() is widget.choose_plugin_button
    assert widget._recent_list.accessibleName() == "本地工程"

    prepared: list[bool] = []
    returned: list[bool] = []
    widget.prepare_requested.connect(lambda: prepared.append(True))
    widget.return_to_landing_requested.connect(lambda: returned.append(True))
    widget._pages.setCurrentWidget(widget._draft_page)
    widget._draft_primary.setEnabled(True)
    widget._name_edit.setFocus()

    QTest.keyClick(widget._name_edit, Qt.Key.Key_Return)
    QTest.keyClick(widget._name_edit, Qt.Key.Key_Escape)

    assert prepared == [True]
    assert returned == [True]
    widget.close()


def test_palette_enter_activates_current_result_once_and_escape_only_closes_palette() -> None:
    controller = CommandPaletteController(CommandPaletteModel(_available_actions))
    dialog = CommandPaletteDialog(controller)
    requests = []
    dialog.intent_requested.connect(requests.append)
    dialog.open_palette("关于")

    QTest.keyClick(dialog._search, Qt.Key.Key_Return)

    assert len(requests) == 1
    assert requests[0].intent_id is IntentId.HELP_ABOUT
    assert not controller.is_open

    dialog.open_palette("AI")
    QTest.keyClick(dialog._search, Qt.Key.Key_Escape)

    assert not controller.is_open
    assert len(requests) == 1
    dialog.close()


def test_context_help_is_keyboard_reachable_and_escape_requests_only_panel_close() -> None:
    controller = ContextHelpController(DEFAULT_CONTEXT_HELP)
    panel = ContextHelpPanel(controller)
    close_requests: list[bool] = []
    panel.close_requested.connect(lambda: close_requests.append(True))
    panel.show_topic("task", context_identity="workbench:current")

    assert panel.accessibleName() == "功能与术语帮助"
    assert panel._title.focusPolicy() is Qt.FocusPolicy.StrongFocus
    QTest.keyClick(panel._title, Qt.Key.Key_Escape)

    assert close_requests == [True]
    assert controller.current is not None
    panel.close()


def test_guidance_exposes_disabled_reason_to_keyboard_and_screen_reader() -> None:
    state = GuidanceState(
        GuidanceContextIdentity(project_id="project-one"),
        1,
        1,
        GuidanceKind.MISSING_CONFIGURATION,
        "还不能开始 AI 翻译",
        "缺少服务配置",
        GuidanceIntent(IntentId.TRANSLATION_AI, "开始 AI 翻译", False, "请先配置 AI 服务"),
        (GuidanceIntent(IntentId.SETTINGS_SERVICES, "修复服务配置"),),
    )
    banner = GuidanceBanner()

    banner.render(present_guidance(state, GuidanceMode.GUIDED))

    assert not banner._primary.isEnabled()
    assert banner._primary.toolTip() == "请先配置 AI 服务"
    assert banner._primary.accessibleDescription() == "请先配置 AI 服务"
    assert banner._reason.focusPolicy() is Qt.FocusPolicy.StrongFocus
    banner.close()


def test_workbench_controls_expose_reasons_and_filter_state_without_color_only_semantics() -> None:
    actions = WorkbenchWorkflowPresenter.actions(
        has_context=False,
        visible_entries=0,
        needs_review=0,
        write_supported=False,
    )
    action_view = WorkflowActionsView()
    action_view.set_actions(actions)
    assert "当前翻译内容没有可操作词条" in action_view._action_reason.text()
    assert action_view._buttons[IntentId.TRANSLATION_AI].accessibleDescription()

    filters = FiltersView(on_changed=lambda: None, on_manage_labels=lambda: None)
    entry = TranslationEntry("one", "key-one", "Original", "", STAGE_QUESTIONABLE, "NPC_:FULL")
    filters.set_content_visible(True)
    filters.build_stages((entry,))
    stage_button = filters.stage_container.itemAt(filters.stage_container.count() - 1).widget()
    assert stage_button.isCheckable()
    assert stage_button.accessibleName().startswith("筛选：")
    assert stage_button.accessibleDescription() in {"已启用", "未启用"}
    assert filters.search_translation.accessibleName() == "译文搜索"
    action_view.close()
    filters.close()


def test_translation_table_supports_keyboard_edit_and_visible_text_status() -> None:
    entry = TranslationEntry("one", "key-one", "Original", "", STAGE_QUESTIONABLE, "NPC_:FULL")
    table = TranslationTable(on_progress=lambda *_: None, on_batch=lambda: None)
    table.start_render(RenderSession(1, 1, (entry,)), {}, {})

    assert table.accessibleName() == "翻译词条表"
    assert table.editTriggers() & QTableWidget.EditTrigger.EditKeyPressed
    assert STAGE_LABELS[STAGE_QUESTIONABLE] in table.item(0, COL_CONTEXT).text()
    assert table.horizontalHeaderItem(COL_CONTEXT).text() == "类型 / 状态"
    table.close_rendering()
    table.close()


def test_task_center_escape_never_stops_task_and_stop_description_names_object_and_recovery() -> None:
    panel = TaskCenterPanel()
    cancelled = []
    panel.cancel_requested.connect(lambda *args: cancelled.append(args))
    state = SimpleNamespace(
        run_id="run-one",
        revision=3,
        state=SimpleNamespace(value="running"),
        display_context=SimpleNamespace(title="AI 翻译 · Demo"),
        available_actions=SimpleNamespace(pause=False, resume=False, stop=True, cancel=True),
    )
    panel.render_activity(state)
    panel._current.setCurrentRow(0)

    QTest.keyClick(panel._current, Qt.Key.Key_Escape)

    assert cancelled == []
    assert "AI 翻译 · Demo" in panel._cancel.accessibleDescription()
    assert "是否可恢复" in panel._cancel.accessibleDescription()
    assert "Run ID run-one" in panel._reason.text()
    panel.close()


def test_safe_drop_review_enter_confirms_candidate_and_escape_dismisses_without_confirmation(tmp_path: Path) -> None:
    source = Path(tmp_path, "Demo.esp")
    source.write_bytes(b"TES4" + b"\0" * 12)
    resolution = DropRouter().resolve((source,))
    dialog = DropReviewDialog()
    confirmed = []
    dismissed = []
    dialog.confirm_requested.connect(confirmed.append)
    dialog.dismiss_requested.connect(lambda: dismissed.append(True))
    dialog.review(resolution)
    dialog.show()
    _APP.processEvents()

    assert dialog.focusWidget() is dialog._confirm
    assert "对象：" in dialog._details.text()
    assert "恢复方式：" in dialog._details.text()
    QTest.keyClick(dialog, Qt.Key.Key_Return)
    dialog.accept()

    assert confirmed == [resolution]
    assert dismissed == []

    dialog.review(resolution)
    dialog.show()
    QTest.keyClick(dialog, Qt.Key.Key_Escape)

    assert confirmed == [resolution]
    assert dismissed == [True]
    dialog.close()
