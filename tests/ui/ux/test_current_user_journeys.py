from __future__ import annotations

import inspect
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QMainWindow

from transbridge.ui.coordinators import project_coordinator as project_module
from transbridge.ui.coordinators.operation_coordinator import OperationCoordinator
from transbridge.ui.coordinators.parse_coordinator import ParseCoordinator
from transbridge.ui.coordinators.project_coordinator import ProjectCoordinator
from transbridge.ui.main_window import MainWindow
from transbridge.ui.shell.menu_builder import MenuBuilder, MenuCallbacks
from transbridge.ui.shell.tool_windows import ToolWindows
from transbridge.ui.workbench import widget as workbench_module

_APP = QApplication.instance() or QApplication([])


def _callbacks(calls: list[str]) -> MenuCallbacks:
    def callback(name: str):
        return lambda: calls.append(name)

    return MenuCallbacks(
        new_project=callback("project.new"),
        open_project=callback("project.open"),
        parse=callback("translation.parse"),
        migrate=callback("translation.migrate"),
        upload=callback("sync.upload"),
        batch_upload=callback("sync.batch_upload"),
        download=callback("sync.download"),
        batch_download=callback("sync.batch_download"),
        write=callback("publish.write"),
        batch_write=callback("publish.batch_write"),
        new_variant=callback("variant.new"),
        copy_variant=callback("variant.copy"),
        save_snapshot=callback("snapshot.save"),
        load_snapshot=callback("snapshot.load"),
        export_transbridge=callback("project.export"),
        import_transbridge=callback("project.import"),
        refresh_projects=callback("paratranz.refresh"),
        show_config=callback("config.open"),
        open_ai_translator=callback("translation.ai"),
        toggle_smart_assistant=callback("assistant.toggle"),
        open_dictionary=callback("dictionary.open"),
        open_fomod=callback("publish.fomod"),
        show_user=callback("account.user"),
        show_mails=callback("account.mails"),
        show_about=callback("help.about"),
        manual_save=callback("project.save"),
    )


def test_current_navigation_routes_each_public_intent_once() -> None:
    calls: list[str] = []
    window = QMainWindow()
    builder = MenuBuilder(window, _callbacks(calls))
    handles = builder.build()

    handles.parse.trigger()
    handles.ai_translator.trigger()
    handles.dictionary.trigger()
    handles.fomod.trigger()
    handles.smart_assistant.trigger()
    handles.view_assistant.trigger()
    assert calls == [
        "translation.parse",
        "translation.ai",
        "dictionary.open",
        "publish.fomod",
        "assistant.toggle",
        "assistant.toggle",
    ]
    window.close()


def test_startup_does_not_block_local_restore_on_optional_token() -> None:
    source = inspect.getsource(MainWindow.__init__)

    assert "if self._ctx.config.token:" in source
    assert "self._tool_windows.show_config()" not in source
    assert "self._project_coordinator.init_workspace()" in source


def test_v2_new_project_routes_to_start_center_without_legacy_creation() -> None:
    calls: list[tuple[str, bool]] = []
    host = SimpleNamespace(
        context=SimpleNamespace(uses_authoritative_projection=True),
        show_start_center=lambda *, user_requested: calls.append(("start", user_requested)),
    )

    ProjectCoordinator(host).new_project()

    assert calls == [("start", True)]


def test_current_authoritative_startup_attempts_automatic_restore(monkeypatch) -> None:
    workspace = SimpleNamespace()
    monkeypatch.setattr(project_module.WorkspaceState, "load", lambda _path: workspace)
    runtime_context = object()
    prepare_calls: list[object] = []
    opener = SimpleNamespace(prepare_active=lambda context: prepare_calls.append(context) or "prepared")
    context = SimpleNamespace(uses_authoritative_projection=True, workspace=None)
    host = SimpleNamespace(
        context=context,
        current_project_opener=opener,
        runtime_context=runtime_context,
        show_message=lambda _message: None,
    )
    captured: dict[str, object] = {}
    coordinator = ProjectCoordinator(host)

    def capture(prepare, **kwargs) -> None:
        captured["result"] = prepare()
        captured.update(kwargs)

    coordinator._start_current_project_open = capture  # type: ignore[method-assign]
    coordinator.init_workspace()

    assert context.workspace is workspace
    assert prepare_calls == [runtime_context]
    assert captured["result"] == "prepared"
    assert captured["success_verb"] == "已恢复"
    assert captured["show_error_dialog"] is False


def test_current_parse_dialog_cancel_has_no_worker_or_message(monkeypatch) -> None:
    from transbridge.ui.workbench._parse_config_dialog import ParseConfigDialog

    monkeypatch.setattr(
        ParseConfigDialog,
        "exec",
        lambda _dialog: ParseConfigDialog.DialogCode.Rejected,
    )
    monkeypatch.setattr(
        ParseConfigDialog,
        "get_config",
        lambda _dialog: (_ for _ in ()).throw(AssertionError("cancel read config")),
    )
    messages: list[str] = []
    host = QMainWindow()
    host.show_message = messages.append  # type: ignore[attr-defined]
    host.workers = []  # type: ignore[attr-defined]

    ParseCoordinator(host).parse_plugin()

    assert host.workers == []
    assert messages == []
    host.close()


def test_current_operation_guards_are_silent_until_required_context_exists() -> None:
    calls: list[str] = []
    context = SimpleNamespace(collection=None, current_project=None, slots={})
    host = SimpleNamespace(
        context=context,
        upload_card=SimpleNamespace(
            upload=lambda: calls.append("upload"),
            batch_upload=lambda: calls.append("batch_upload"),
        ),
        download_card=SimpleNamespace(
            download=lambda: calls.append("download"),
            batch_download=lambda: calls.append("batch_download"),
        ),
        write_card=SimpleNamespace(
            write=lambda: calls.append("write"),
            batch_write=lambda: calls.append("batch_write"),
        ),
    )
    coordinator = OperationCoordinator(host)

    coordinator.upload()
    coordinator.download()
    coordinator.write()
    coordinator.batch_upload()
    coordinator.batch_download()
    coordinator.batch_write()
    assert calls == []

    context.collection = [object()]
    context.current_project = {"id": 7}
    context.slots = {"a": object(), "b": object()}
    coordinator.upload()
    coordinator.download()
    coordinator.write()
    coordinator.batch_upload()
    coordinator.batch_download()
    coordinator.batch_write()
    assert calls == [
        "upload",
        "download",
        "write",
        "batch_upload",
        "batch_download",
        "batch_write",
    ]


def test_current_ai_intent_routes_through_workbench_public_port() -> None:
    calls: list[str] = []
    host = SimpleNamespace(workbench=SimpleNamespace(open_tool=lambda tool_id: calls.append(tool_id)))

    ToolWindows(host).open_ai_translator()

    assert calls == ["ai_translator"]


def test_current_ai_run_hands_focus_to_progress_context(monkeypatch) -> None:
    activations: list[tuple[object, bool]] = []
    monkeypatch.setattr(
        workbench_module,
        "show_and_activate",
        lambda window, *, deferred=False: activations.append((window, deferred)),
    )
    config_window = object()
    progress_window = object()
    windows = {"ai_translator": config_window}

    workbench_module._track_ai_progress(windows, progress_window)

    assert windows == {"ai_translator_progress": progress_window}
    assert activations == [(progress_window, True)]
