from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QFileDialog
import pytest

from tests.ui.responsive_layout_support import settle_layout, themed_layout_app as themed_layout_app
from transbridge.application.contracts import DomainError, ErrorCategory, OperationResult
from transbridge.application.io import FormatId
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.projections import ProjectionSnapshot, ProjectionStore
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.ui.context import AppContext
from transbridge.ui.coordinators.guided_project_coordinator import GuidedDraftPhase, GuidedProjectCoordinator
from transbridge.ui.shell.start_center import StartCenterViewState, StartCenterWidget, StartDestinationState
from transbridge.ui.shell.start_center_controller import StartCenterController

_APP = QApplication.instance() or QApplication([])
_PATHS = ("D:/mods/Base.esm", "D:/mods/Addon.esp", "D:/mods/Patch.esl")


class _Commands:
    def __init__(self):
        self.requests = []
        self.commits = []
        self.fail_prepare = False

    def prepare_create(self, request, _context):
        self.requests.append(request)
        if self.fail_prepare:
            return OperationResult.failed(
                DomainError(ErrorCategory.INPUT, "PROJECT_SOURCE_PARSE_FAILED", "Addon.esp 无法解析。")
            )
        return OperationResult.completed({
            "token": "preview",
            "request_fingerprint": request.request_fingerprint,
            "source_count": len(request.sources),
            "entry_count": len(request.sources),
        })

    def commit_create(self, token, _context, *, request_fingerprint):
        self.commits.append((token, request_fingerprint))
        entries = [
            {"entry_key": entry.entry_key.to_dict(), "translation": entry.translation, "stage": entry.stage}
            for source in self.consume_create_hydration("project", _context).value.sources
            for entry in source.entries
        ]
        self.projection.rebuild(
            ProjectionSnapshot(
                "project:project", 1, 1, {"project_id": "project", "project_name": "Base", "entries": entries}
            )
        )
        return OperationResult.completed({"project_id": "project", "variant_id": "variant"})

    def discard_create(self, _token, _context):
        return OperationResult.completed()

    def consume_create_hydration(self, project_id, _context):
        assert project_id == "project"
        return SimpleNamespace(
            is_success=True,
            value=SimpleNamespace(
                sources=tuple(
                    SimpleNamespace(
                        location=source.location,
                        format_id=FormatId.PLUGIN_SSE,
                        source_snapshot=object(),
                        entries=(
                            TranslationEntry(
                                "same-legacy-id",
                                "same-local-key",
                                Path(source.location).name,
                                "",
                                0,
                                "FULL",
                                string_id=100 + index,
                                entry_key=EntryKey(SourceNamespace(f"source-{index}"), "same-local-key"),
                            ).snapshot(),
                        ),
                    )
                    for index, source in enumerate(self.requests[-1].sources)
                )
            ),
        )


@pytest.fixture
def creation(monkeypatch):
    monkeypatch.setattr("transbridge.ui.context.ParatranzConfig.create_or_load", lambda: SimpleNamespace(token=""))
    view = StartCenterWidget()
    commands = _Commands()
    commands.projection = ProjectionStore()
    context = AppContext(project_projection=commands.projection)
    pending, messages, shown = [], [], []

    def dispatch(operation, **callbacks):
        before_start = callbacks.pop("before_start", None)
        if before_start is not None:
            before_start()
        pending.append((operation, callbacks))
        return True

    host = SimpleNamespace(
        project_commands=commands,
        runtime_context=object(),
        context=context,
        app_runtime=SimpleNamespace(use_cases=SimpleNamespace(resolve=lambda _: commands.projection)),
        mode_tabs=SimpleNamespace(setCurrentWidget=shown.append, setCurrentIndex=shown.append),
        start_foreground_task=dispatch,
        show_message=messages.append,
    )
    controller = StartCenterController(host, view)
    controller.start()
    yield SimpleNamespace(
        view=view,
        commands=commands,
        controller=controller,
        pending=pending,
        context=context,
        messages=messages,
        shown=shown,
    )
    view.close()
    context.close_projection()


def test_choose_multiple_plugins_creates_one_project_and_loads_every_collection(monkeypatch, creation):
    selected = []

    def choose(_parent, title, _directory, _filter):
        selected.append(title)
        return list(_PATHS), ""

    monkeypatch.setattr(QFileDialog, "getOpenFileNames", choose)
    creation.view.choose_plugin_button.click()

    state = creation.controller.guided_project.state
    assert selected == ["选择要翻译的插件（可多选）"]
    assert state.project_name == "Base"
    assert state.source_paths == tuple(str(Path(path)) for path in _PATHS)
    assert creation.view._source_label.text() == "已选择 3 个插件"
    assert creation.view._source_list.count() == 3
    assert not creation.view._source_list.isHidden()
    assert "3 个插件" in creation.view._draft_summary.text()
    assert "同一个工程" in creation.view._draft_summary.text()
    assert [creation.view._source_list.item(i).toolTip() for i in range(3)] == list(state.source_paths)

    creation.view._draft_primary.click()
    creation.view._draft_primary.click()
    assert len(creation.pending) == 1
    assert not creation.view._draft_primary.isEnabled()
    assert creation.commands.requests == []
    prepare, callbacks = creation.pending.pop(0)
    callbacks["on_result"](prepare())
    commit, callbacks = creation.pending.pop(0)
    callbacks["on_result"](commit())

    assert len(creation.commands.requests) == len(creation.commands.commits) == 1
    assert creation.commands.requests[0].migration_sources == ()
    assert tuple(creation.context.slots) == state.source_paths
    assert [next(iter(slot.collection)).original for slot in creation.context.slots.values()] == [
        "Base.esm",
        "Addon.esp",
        "Patch.esl",
    ]
    assert len({next(iter(slot.collection)).identity for slot in creation.context.slots.values()}) == 3
    assert [next(iter(slot.collection)).string_id for slot in creation.context.slots.values()] == [100, 101, 102]
    assert creation.shown[-1] == 0
    assert creation.messages == ["本地工程“Base”已创建"]


def test_cancelling_file_selection_keeps_the_current_draft(monkeypatch, creation):
    creation.controller.choose_source_path(_PATHS[0])
    state = creation.controller.guided_project.state
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *_args: ([], ""))

    creation.controller.choose_source()

    assert creation.controller.guided_project.state is state
    assert creation.commands.requests == creation.commands.commits == []
    assert creation.pending == []


def test_reselecting_plugins_refreshes_the_draft_after_edits(monkeypatch, creation):
    choices = iter((list(_PATHS), [_PATHS[1]]))
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *_args: (next(choices), ""))
    creation.controller.choose_source()
    coordinator = creation.controller.guided_project
    coordinator.set_project_name("Changed")
    creation.view.render(StartCenterViewState(StartDestinationState.START_CENTER_USER_REQUESTED, 1))

    creation.controller.choose_source()

    assert creation.view._name_edit.text() == "Addon"
    assert creation.view._source_label.text() == str(Path(_PATHS[1]))
    assert creation.view._source_list.isHidden()
    assert coordinator.state.source_paths == (str(Path(_PATHS[1])),)
    creation.controller.create_empty()
    assert coordinator.state.source_paths == ()
    assert creation.view._source_label.text() == "无源文件（空工程）"
    assert creation.view._source_list.count() == 0


def test_multi_plugin_draft_preserves_options_and_paths_when_prepare_fails():
    commands = _Commands()
    coordinator = GuidedProjectCoordinator(commands, object())
    coordinator.begin_sources(_PATHS)
    coordinator.set_project_name("多个插件")
    coordinator.set_parse_option("skip_empty", True)
    coordinator.set_migration_sources(("D:/translated/Addon.esp",))
    commands.fail_prepare = True

    assert coordinator.create()

    request = commands.requests[0]
    assert tuple(source.location for source in request.sources) == coordinator.state.source_paths
    assert len(request.additional_sources) == 2
    assert request.parse_options == (("skip_empty", True),)
    assert len(request.migration_sources) == 1
    assert commands.commits == []
    assert coordinator.state.phase is GuidedDraftPhase.FAILED
    assert coordinator.state.project_name == "多个插件"
    assert coordinator.state.diagnostic_message == "Addon.esp 无法解析。"
    assert len(coordinator.state.source_paths) == 3


def test_many_selected_plugins_use_a_scrollable_list(creation, layout_app):
    coordinator = creation.controller.guided_project
    paths = tuple(f"D:/mods/Plugin-{index}.esp" for index in range(50))
    coordinator.begin_sources(paths)
    view = creation.view
    view.resize(850, 650)
    view.show()
    settle_layout(layout_app)

    assert (view.width(), view.height()) == (850, 650)
    assert view._source_list.count() == 50
    assert view._source_list.height() <= 144
    assert view._source_list.verticalScrollBar().maximum() > 0
    assert view._source_list.item(49).data(Qt.ItemDataRole.AccessibleTextRole) == str(Path(paths[49]))
    assert view.rect().contains(view._draft_primary.geometry().translated(view._draft_page.pos()))
