from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLabel

from transbridge.application.contracts import DomainError, ErrorCategory, OperationResult
from transbridge.application.io import EntryKey, SourceNamespace
from transbridge.ui.coordinators.guided_project_coordinator import (
    GuidedDraftPhase,
    GuidedProjectCoordinator,
    GuidedProjectDraftState,
)
from transbridge.ui.coordinators.project_coordinator import ProjectCoordinator
from transbridge.ui.shell.action_catalog import IntentId
from transbridge.ui.shell.project_open_choice_dialog import ProjectOpenChoiceDialog
from transbridge.ui.shell.start_center import (
    RecentProjectViewState,
    RecoveryItemViewState,
    StartCenterViewState,
    StartCenterWidget,
    StartDestinationState,
)
from transbridge.ui.shell.start_center_controller import StartCenterController

_APP = QApplication.instance() or QApplication([])


class _Commands:
    def __init__(self) -> None:
        self.prepared = []
        self.committed = []
        self.discarded = []

    def prepare_create(self, request, context):
        self.prepared.append((request, context))
        return OperationResult.completed({
            "token": "preview-1",
            "request_fingerprint": request.request_fingerprint,
            "entry_count": 42,
            "source_count": 1,
        })

    def commit_create(self, token, context, *, request_fingerprint=None):
        self.committed.append((token, context, request_fingerprint))
        return OperationResult.completed({"project_id": "project-1", "variant_id": "variant-1"})

    def discard_create(self, token, context):
        self.discarded.append((token, context))
        return OperationResult.completed()


def test_guided_draft_prepare_commit_uses_authoritative_commands_once() -> None:
    commands = _Commands()
    created = []
    states = []
    coordinator = GuidedProjectCoordinator(
        commands,
        "gui-context",
        on_state=states.append,
        on_created=created.append,
    )

    coordinator.begin("D:/mods/MyMod.esp")
    assert coordinator.prepare()
    assert coordinator.state.phase is GuidedDraftPhase.PREPARED
    assert coordinator.state.preview_entry_count == 42
    assert coordinator.commit()

    assert len(commands.prepared) == 1
    assert len(commands.committed) == 1
    assert created == [{"project_id": "project-1", "variant_id": "variant-1"}]
    assert coordinator.state.phase is GuidedDraftPhase.COMPLETED
    assert states[-1] == coordinator.state


def test_guided_create_runs_prepare_and_commit_as_one_user_action() -> None:
    commands = _Commands()
    created = []
    states = []
    coordinator = GuidedProjectCoordinator(
        commands,
        "gui-context",
        on_state=states.append,
        on_created=created.append,
    )
    coordinator.begin("D:/mods/MyMod.esp")

    assert coordinator.create()

    assert len(commands.prepared) == 1
    assert len(commands.committed) == 1
    assert created == [{"project_id": "project-1", "variant_id": "variant-1"}]
    assert coordinator.state.phase is GuidedDraftPhase.COMPLETED
    assert GuidedDraftPhase.PREPARED not in {state.phase for state in states}


def test_guided_create_chains_commit_after_async_prepare() -> None:
    commands = _Commands()
    pending = []
    created = []

    def dispatch(operation, message, on_result, on_error):
        pending.append((operation, message, on_result, on_error))
        return True

    coordinator = GuidedProjectCoordinator(
        commands,
        "gui-context",
        dispatch=dispatch,
        on_created=created.append,
    )
    coordinator.begin("D:/mods/MyMod.esp")

    assert coordinator.create()
    assert len(pending) == 1
    assert pending[0][1] == "正在创建本地翻译工程…"
    pending[0][2](pending[0][0]())
    assert len(pending) == 2
    assert coordinator.state.phase is GuidedDraftPhase.COMMITTING
    assert "已验证" not in coordinator.state.summary
    pending[1][2](pending[1][0]())

    assert len(commands.prepared) == 1
    assert len(commands.committed) == 1
    assert created == [{"project_id": "project-1", "variant_id": "variant-1"}]
    assert coordinator.state.phase is GuidedDraftPhase.COMPLETED


def test_guided_create_only_exposes_prepare_diagnostic_when_validation_fails() -> None:
    commands = _Commands()
    commands.prepare_create = lambda _request, _context: OperationResult.failed(
        DomainError(ErrorCategory.PREREQUISITE, "PROJECT_SOURCE_PARSE_FAILED", "插件无法解析。")
    )
    coordinator = GuidedProjectCoordinator(commands, "gui-context")
    coordinator.begin("D:/mods/broken.esp")

    assert coordinator.create()

    assert commands.committed == []
    assert coordinator.state.phase is GuidedDraftPhase.FAILED
    assert coordinator.state.diagnostic_code == "PROJECT_SOURCE_PARSE_FAILED"
    assert coordinator.state.diagnostic_message == "插件无法解析。"


def test_draft_primary_button_is_always_one_confirm_action() -> None:
    widget = StartCenterWidget()
    requested = []
    committed = []
    widget.prepare_requested.connect(lambda: requested.append(True))
    widget.commit_requested.connect(lambda: committed.append(True))
    widget.render_draft(GuidedProjectDraftState("D:/mods/MyMod.esp", "MyMod", revision=1))

    assert widget._draft_primary.text() == "确定"
    widget._draft_primary.click()
    widget.render_draft(
        GuidedProjectDraftState(
            "D:/mods/MyMod.esp",
            "MyMod",
            preview_token="preview-1",
            request_fingerprint="fingerprint-1",
            preview_entry_count=42,
            preview_source_count=1,
            revision=2,
            phase=GuidedDraftPhase.PREPARED,
        )
    )

    assert widget._draft_primary.text() == "确定"
    widget._draft_primary.click()
    assert requested == [True, True]
    assert committed == []
    widget.close()


def test_duplicate_intent_is_merged_while_prepare_is_in_flight() -> None:
    commands = _Commands()
    pending = []

    def dispatch(operation, _message, on_result, on_error):
        pending.append((operation, on_result, on_error))
        return True

    coordinator = GuidedProjectCoordinator(commands, object(), dispatch=dispatch)
    coordinator.begin("D:/mods/MyMod.esm")

    assert coordinator.prepare()
    assert not coordinator.prepare()
    assert len(pending) == 1
    result = pending[0][0]()
    pending[0][1](result)
    assert len(commands.prepared) == 1
    assert coordinator.state.phase is GuidedDraftPhase.PREPARED


def test_edit_after_preview_discards_token_and_preserves_draft_after_failure() -> None:
    commands = _Commands()
    coordinator = GuidedProjectCoordinator(commands, "owner")
    coordinator.begin(None)
    coordinator.set_project_name("Empty")
    assert coordinator.prepare()

    assert coordinator.set_variant_name("Main")
    assert commands.discarded == [("preview-1", "owner")]
    assert coordinator.state.preview_token is None
    assert coordinator.state.project_name == "Empty"

    commands.prepare_create = lambda _request, _context: OperationResult.failed(
        DomainError(ErrorCategory.CONFLICT, "PROJECT_NAME_CONFLICT", "工程名称已存在。")
    )
    assert coordinator.prepare()
    assert coordinator.state.phase is GuidedDraftPhase.FAILED
    assert coordinator.state.project_name == "Empty"
    assert coordinator.state.diagnostic_code == "PROJECT_NAME_CONFLICT"


def test_commit_failure_requires_a_fresh_preview_but_keeps_editable_input() -> None:
    commands = _Commands()
    coordinator = GuidedProjectCoordinator(commands, "owner")
    coordinator.begin("D:/mods/MyMod.esp")
    assert coordinator.prepare()
    commands.commit_create = lambda *_args, **_kwargs: OperationResult.failed(
        DomainError(ErrorCategory.CONFLICT, "PROJECT_PROVISIONING_STALE", "预览已过期。")
    )

    assert coordinator.commit()

    assert coordinator.state.phase is GuidedDraftPhase.FAILED
    assert coordinator.state.preview_token is None
    assert coordinator.state.project_name == "MyMod"


def test_start_center_renders_one_primary_action_and_unavailable_recent_reason() -> None:
    widget = StartCenterWidget()
    widget.render(
        StartCenterViewState(
            StartDestinationState.START_CENTER_EMPTY,
            revision=1,
            recent_projects=(
                RecentProjectViewState(
                    "missing",
                    "Missing",
                    "D:/missing/project.json",
                    available=False,
                    reason="工程记录不存在或不可访问",
                ),
            ),
        )
    )

    assert widget.choose_plugin_button.accessibleName() == "选择插件"
    assert widget._empty_button.text() == "高级：创建空工程（不导入插件）"
    assert widget._recent_list.count() == 1
    item = widget._recent_list.item(0)
    assert item.text() == ""
    assert "工程记录不存在或不可访问" in item.data(Qt.ItemDataRole.AccessibleTextRole)
    assert item.toolTip() == "工程记录不存在或不可访问"
    assert not item.flags() & Qt.ItemFlag.ItemIsEnabled
    widget.close()


def test_authoritative_startup_without_active_reference_shows_empty_center(monkeypatch) -> None:
    workspace = SimpleNamespace(active_project=None, projects={})
    monkeypatch.setattr(
        "transbridge.ui.coordinators.project_coordinator.WorkspaceState.load",
        lambda _path: workspace,
    )
    calls = []
    host = SimpleNamespace(
        context=SimpleNamespace(uses_authoritative_projection=True, workspace=None),
        current_project_opener=SimpleNamespace(has_active_reference=False),
        runtime_context=object(),
        show_message=calls.append,
        show_start_center_empty=lambda: calls.append("empty"),
    )

    ProjectCoordinator(host).init_workspace()

    assert calls == ["empty"]
    assert host.context.workspace is workspace


def test_return_to_start_center_only_changes_shell_display_context() -> None:
    shown = []
    view = StartCenterWidget()
    context = SimpleNamespace(
        project_name="ActiveMod",
        dirty=True,
        workspace=None,
    )
    host = SimpleNamespace(
        context=context,
        app_runtime=None,
        runtime_context=None,
        central_stack=SimpleNamespace(setCurrentWidget=shown.append),
    )
    controller = StartCenterController(host, view)

    controller.show(user_requested=True)

    assert shown == [view]
    assert "仍保持打开，有未保存修改" in view._status_label.text()
    view.close()


def test_start_center_buttons_forward_one_canonical_intent() -> None:
    requests = []
    view = StartCenterWidget()
    host = SimpleNamespace(
        project_commands=object(),
        runtime_context=object(),
        project_coordinator=SimpleNamespace(),
        tool_windows=SimpleNamespace(),
        mode_tabs=SimpleNamespace(setCurrentWidget=lambda _widget: None),
    )
    controller = StartCenterController(
        host,
        view,
        dispatch=lambda intent, payload=None: requests.append((intent, payload)),
    )
    controller.start()

    view.choose_plugin_button.click()
    view._open_button.click()
    view._fomod_button.click()
    view._task_center_button.click()
    view._empty_button.click()

    assert requests == [
        (IntentId.PROJECT_CREATE, {"mode": "plugin"}),
        (IntentId.PROJECT_OPEN, None),
        (IntentId.PUBLISH_FOMOD, None),
        (IntentId.TASK_OPEN_ACTIVITY, None),
    ]
    assert view._source_label.text() == "无源文件（空工程）"
    view.close()


def test_start_center_project_rows_and_recovery_summary_keep_real_action_semantics() -> None:
    widget = StartCenterWidget()
    returned = []
    opened = []
    task_center = []
    widget.return_to_current_requested.connect(lambda: returned.append(True))
    widget.open_recent_requested.connect(opened.append)
    widget.task_center_requested.connect(lambda: task_center.append(True))
    widget.render(
        StartCenterViewState(
            StartDestinationState.START_CENTER_USER_REQUESTED,
            revision=1,
            active_project_name="ActiveMod",
            recent_projects=(
                RecentProjectViewState("active", "ActiveMod", "D:/active.json", True, active=True),
                RecentProjectViewState("other", "OtherMod", "D:/other.json", True),
                RecentProjectViewState("missing", "MissingMod", "D:/missing.json", False, "工程记录不存在"),
            ),
            recovery_items=(
                RecoveryItemViewState("ready", "插件翻译", True, ""),
                RecoveryItemViewState("blocked", "旧任务", False, "来源文件已移动"),
            ),
        )
    )

    active_item = widget._project_list.item(0)
    other_item = widget._project_list.item(1)
    missing_item = widget._project_list.item(2)
    widget.resize(1_440, 900)
    widget.show()
    _APP.processEvents()

    QTest.mouseClick(
        widget._project_list.viewport(),
        Qt.MouseButton.LeftButton,
        pos=widget._project_list.visualItemRect(active_item).center(),
    )
    QTest.mouseClick(
        widget._project_list.viewport(),
        Qt.MouseButton.LeftButton,
        pos=widget._project_list.visualItemRect(other_item).center(),
    )
    project_dialog = widget._projects_panel._project_dialog
    assert project_dialog is not None
    assert project_dialog.isVisible()
    assert project_dialog.isModal()
    assert project_dialog.windowTitle() == "打开工程"
    assert project_dialog.current_window_button.accessibleName() == "在当前窗口打开"
    assert project_dialog.new_window_button.accessibleName() == "在新窗口打开"
    project_dialog.current_window_button.click()
    QTest.mouseClick(
        widget._project_list.viewport(),
        Qt.MouseButton.LeftButton,
        pos=widget._project_list.visualItemRect(missing_item).center(),
    )
    widget._task_center_button.click()

    assert returned == [True]
    assert opened == ["D:/other.json"]
    assert active_item.text() == other_item.text() == missing_item.text() == ""
    assert other_item.data(Qt.ItemDataRole.AccessibleTextRole) == "OtherMod，可打开"
    assert not missing_item.flags() & Qt.ItemFlag.ItemIsEnabled
    assert "工程记录不存在" in missing_item.data(Qt.ItemDataRole.AccessibleTextRole)
    assert not widget._recovery_banner.isHidden()
    assert widget._landing_page._recovery_label.text() == "有 1 个任务可以继续"
    assert task_center == [True]
    widget.close()


def test_start_center_project_rows_support_keyboard_activation() -> None:
    widget = StartCenterWidget()
    opened = []
    widget.open_recent_requested.connect(opened.append)
    widget.render(
        StartCenterViewState(
            StartDestinationState.START_CENTER_EMPTY,
            revision=1,
            recent_projects=(RecentProjectViewState("other", "OtherMod", "D:/other.json", True),),
        )
    )
    widget.show()
    _APP.processEvents()

    widget._project_list.setCurrentRow(0)
    widget._project_list.setFocus()
    QTest.keyClick(widget._project_list, Qt.Key.Key_Return)
    project_dialog = widget._projects_panel._project_dialog
    assert project_dialog is not None
    assert project_dialog.isVisible()
    project_dialog.current_window_button.click()

    assert opened == ["D:/other.json"]
    widget.close()


def test_empty_project_draft_exposes_icon_back_action_in_the_header() -> None:
    widget = StartCenterWidget()
    returned = []
    widget.return_to_landing_requested.connect(lambda: returned.append(True))

    assert widget._draft_back.text() == ""
    assert not widget._draft_back.icon().isNull()
    assert widget._draft_back.toolTip() == "返回开始"
    assert widget._draft_back.accessibleName() == "返回开始中心"

    widget._draft_back.click()

    assert returned == [True]
    widget.close()


def test_start_center_empty_and_restoring_states_do_not_leave_dead_sections_or_conflicting_actions() -> None:
    widget = StartCenterWidget()
    widget.render(StartCenterViewState(StartDestinationState.START_CENTER_EMPTY, revision=1))

    assert widget._project_list.isHidden()
    assert not widget._projects_empty.isHidden()
    assert widget._recovery_banner.isHidden()

    widget.render(StartCenterViewState(StartDestinationState.RESTORING_LAST, revision=2))

    assert not widget.choose_plugin_button.isEnabled()
    assert not widget._open_button.isEnabled()
    assert not widget._empty_button.isEnabled()
    assert not widget._project_list.isEnabled()
    assert not widget._fomod_button.isEnabled()
    widget.close()


def test_start_center_project_dialog_can_launch_a_new_window_and_show_thin_opening_progress() -> None:
    widget = StartCenterWidget()
    launched = []
    widget.open_recent_in_new_window_requested.connect(launched.append)
    widget.render(
        StartCenterViewState(
            StartDestinationState.START_CENTER_EMPTY,
            revision=1,
            recent_projects=(RecentProjectViewState("other", "OtherMod", "D:/other.json", True),),
        )
    )
    widget.show()
    _APP.processEvents()

    item = widget._project_list.item(0)
    QTest.mouseClick(
        widget._project_list.viewport(),
        Qt.MouseButton.LeftButton,
        pos=widget._project_list.visualItemRect(item).center(),
    )
    project_dialog = widget._projects_panel._project_dialog
    assert project_dialog is not None
    project_dialog.new_window_button.click()

    assert launched == ["D:/other.json"]
    widget.set_project_opening("正在校验并加载本地工程…")
    assert not widget._project_open_progress.isHidden()
    assert widget._project_open_progress_bar.minimum() == 0
    assert widget._project_open_progress_bar.maximum() == 0
    assert widget._project_open_progress_bar.height() == 4
    assert not widget._project_list.isEnabled()
    assert not widget.choose_plugin_button.isEnabled()
    assert not widget._open_button.isEnabled()
    assert not widget._fomod_button.isEnabled()

    widget.set_project_opening(None)
    assert widget._project_open_progress.isHidden()
    assert widget._project_list.isEnabled()
    assert widget.choose_plugin_button.isEnabled()
    widget.close()


def test_project_open_dialog_mode_cards_keep_two_line_geometry_under_application_skin() -> None:
    from transbridge.config.ui_preferences import DEFAULT_THEME_ID, ThemeMode
    from transbridge.ui.foundation.builtins import create_builtin_registry
    from transbridge.ui.foundation.theme_service import ThemeService

    class Preferences:
        @staticmethod
        def load():
            return SimpleNamespace(theme_mode=ThemeMode.LIGHT, theme_id=DEFAULT_THEME_ID, diagnostics=())

        @staticmethod
        def save_theme_preference(_mode, _theme_id):
            return SimpleNamespace(saved=True, diagnostic_code=None, message="")

    service = ThemeService(_APP, create_builtin_registry(), Preferences())  # type: ignore[arg-type]
    service.start()
    dialog = ProjectOpenChoiceDialog("OtherMod", "D:/projects/other.json")
    try:
        dialog.show()
        _APP.processEvents()

        for button in (dialog.current_window_button, dialog.new_window_button):
            assert button.height() >= 88
            labels = [
                label
                for label in button.findChildren(QLabel)
                if label.property("tbProjectOpenModeTitle") or label.property("tbProjectOpenModeDescription")
            ]
            assert len(labels) == 2
            assert all(label.geometry().top() >= 0 for label in labels)
            assert all(label.geometry().bottom() < button.height() for label in labels)
    finally:
        dialog.close()
        service.close()


def test_start_center_controller_switches_pages_inside_the_persistent_workspace_shell() -> None:
    shown = []
    indices = []
    view = StartCenterWidget()
    host = SimpleNamespace(
        context=SimpleNamespace(project_name=None, dirty=False, workspace=None),
        app_runtime=None,
        runtime_context=None,
        mode_tabs=SimpleNamespace(setCurrentWidget=shown.append, setCurrentIndex=indices.append),
    )
    controller = StartCenterController(host, view)

    controller.show_empty()
    controller.show_workbench()

    assert shown == [view]
    assert indices == [0]
    view.close()


def test_begin_project_from_workbench_shows_the_guided_draft() -> None:
    shown = []
    view = StartCenterWidget()
    host = SimpleNamespace(
        project_commands=_Commands(),
        runtime_context=object(),
        mode_tabs=SimpleNamespace(setCurrentWidget=shown.append),
    )
    controller = StartCenterController(host, view)
    controller.start()

    controller.choose_source_path("D:/mods/MyMod.esp")

    assert shown == [view]
    assert view._pages.currentWidget() is view._draft_page
    assert view._source_label.text().replace("\\", "/") == "D:/mods/MyMod.esp"
    assert view._name_edit.text() == "MyMod"
    view.close()


def test_start_center_controller_launches_new_window_without_switching_current_context() -> None:
    messages = []
    launches = []
    context = SimpleNamespace(project_name="ActiveMod", dirty=True)
    view = StartCenterWidget()
    host = SimpleNamespace(
        context=context,
        project_commands=None,
        runtime_context=None,
        show_message=messages.append,
    )
    controller = StartCenterController(
        host,
        view,
        project_window_launcher=lambda path: launches.append(path) or True,
    )
    controller.start()

    view.open_recent_in_new_window_requested.emit("D:/other.json")

    assert launches == ["D:/other.json"]
    assert messages == ["已请求在新窗口打开工程。"]
    assert context.project_name == "ActiveMod"
    assert context.dirty is True
    view.close()


def test_start_center_controller_reports_new_window_launch_failure() -> None:
    messages = []
    view = StartCenterWidget()
    host = SimpleNamespace(
        context=SimpleNamespace(project_name="ActiveMod"),
        project_commands=None,
        runtime_context=None,
        show_message=messages.append,
    )
    controller = StartCenterController(host, view, project_window_launcher=lambda _path: False)
    controller.start()

    view.open_recent_in_new_window_requested.emit("D:/other.json")

    assert messages == ["PROJECT_WINDOW_LAUNCH_FAILED: 操作系统未能启动新窗口。"]
    view.close()


def test_creation_capability_remains_disabled_after_landing_rerender() -> None:
    view = StartCenterWidget()
    host = SimpleNamespace(
        project_commands=None,
        runtime_context=None,
        context=SimpleNamespace(project_name=None),
    )
    controller = StartCenterController(host, view)
    controller.start()

    view.render(StartCenterViewState(StartDestinationState.START_CENTER_EMPTY, revision=1))

    assert not view.choose_plugin_button.isEnabled()
    assert not view._empty_button.isEnabled()
    assert view._open_button.isEnabled()
    assert view.choose_plugin_button.toolTip() == "建项服务不可用"
    view.close()


def test_recovery_projection_failure_is_distinguishable_from_an_empty_catalog() -> None:
    class Registry:
        def resolve(self, name):
            assert name == "task_recovery"
            raise RuntimeError("catalog unavailable")

    runtime_context = SimpleNamespace(
        metadata={},
        owner_id="owner",
        project_id=None,
        variant_id=None,
        session_id="session",
        permissions=(),
    )
    host = SimpleNamespace(
        app_runtime=SimpleNamespace(use_cases=Registry()),
        runtime_context=runtime_context,
    )
    controller = StartCenterController.__new__(StartCenterController)
    controller._host = host
    controller._recovery_diagnostic_message = ""

    assert controller._recovery_projection() == ()
    assert controller._recovery_diagnostic_message == "任务恢复状态暂时不可用。"


def test_recent_projects_prefer_v2_read_only_catalog() -> None:
    snapshot = SimpleNamespace(
        projects=(
            SimpleNamespace(
                project_id="project-1",
                name="MyMod",
                path="D:/data/projects/project-1.json",
                active=True,
                available=True,
                reason=None,
            ),
        )
    )

    class Registry:
        def names(self):
            return ("project_catalog",)

        def resolve(self, name):
            assert name == "project_catalog"
            return SimpleNamespace(list_projects=lambda: snapshot)

    host = SimpleNamespace(
        app_runtime=SimpleNamespace(use_cases=Registry()),
        context=SimpleNamespace(workspace=None),
    )
    controller = StartCenterController.__new__(StartCenterController)
    controller._host = host

    recent = controller._recent_project_projection()

    assert len(recent) == 1
    assert recent[0].project_key == "project-1"
    assert recent[0].active and recent[0].available


def test_created_project_relies_on_projection_and_does_not_parse_source_again() -> None:
    calls = []
    slots = []
    entry_key = EntryKey(SourceNamespace("source:test"), "entry-1")
    hydrated_entry = SimpleNamespace(
        legacy_id="legacy-1",
        entry_key=entry_key,
        original="Original text",
        translation="",
        stage=0,
        context="FULL",
        external_refs=(),
        revision=0,
        provenance=(),
        metadata=(),
    )
    source = SimpleNamespace(
        location="D:/mods/MyMod.esp",
        entries=(hydrated_entry,),
        source_snapshot=object(),
        format_id=object(),
    )
    host = SimpleNamespace(
        project_coordinator=SimpleNamespace(restore_parse_esp=lambda _path: calls.append("duplicate-parse")),
        project_commands=SimpleNamespace(
            consume_create_hydration=lambda project_id, context: SimpleNamespace(
                is_success=True,
                value=SimpleNamespace(source=source),
            )
        ),
        runtime_context=object(),
        context=SimpleNamespace(add_slot=lambda key, slot: slots.append((key, slot))),
        show_message=lambda message: calls.append(message),
    )
    controller = StartCenterController.__new__(StartCenterController)
    controller._host = host
    controller.guided_project = SimpleNamespace(
        state=SimpleNamespace(source_path="D:/mods/MyMod.esp", project_name="MyMod")
    )
    controller.show_workbench = lambda: calls.append("workbench")

    controller._on_created({"project_id": "project-1"})

    assert calls == ["workbench", "本地工程“MyMod”已创建"]
    assert len(slots) == 1
    assert len(slots[0][1].collection) == 1
    assert next(iter(slots[0][1].collection)).original == "Original text"
