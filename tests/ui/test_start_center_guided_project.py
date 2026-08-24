from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from transbridge.application.contracts import DomainError, ErrorCategory, OperationResult
from transbridge.application.io import EntryKey, SourceNamespace
from transbridge.ui.coordinators.guided_project_coordinator import (
    GuidedDraftPhase,
    GuidedProjectCoordinator,
)
from transbridge.ui.coordinators.project_coordinator import ProjectCoordinator
from transbridge.ui.shell.action_catalog import IntentId
from transbridge.ui.shell.start_center import (
    RecentProjectViewState,
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

    assert widget.choose_plugin_button.text() == "选择插件开始翻译"
    assert widget._recent_list.count() == 1
    assert "工程记录不存在或不可访问" in widget._recent_list.item(0).text()
    assert not widget._recent_list.item(0).flags() & Qt.ItemFlag.ItemIsEnabled
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
    assert "仍保持打开；有未保存修改" in view._status_label.text()
    view.close()


def test_start_center_buttons_forward_one_canonical_intent() -> None:
    requests = []
    view = StartCenterWidget()
    host = SimpleNamespace(
        project_commands=object(),
        runtime_context=object(),
        project_coordinator=SimpleNamespace(),
        tool_windows=SimpleNamespace(),
    )
    controller = StartCenterController(
        host,
        view,
        dispatch=lambda intent, payload=None: requests.append((intent, payload)),
    )
    controller.start()

    view.choose_plugin_button.click()
    view._open_button.click()
    view._empty_button.click()

    assert requests == [
        (IntentId.SOURCE_PARSE, None),
        (IntentId.PROJECT_OPEN, None),
        (IntentId.PROJECT_CREATE, None),
    ]
    view.close()


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
