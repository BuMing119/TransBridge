from __future__ import annotations

import inspect
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QMainWindow

from transbridge.application.history_search import HistorySearchPage, IndexStatus
from transbridge.ui.coordinators import project_coordinator as project_module
from transbridge.ui.coordinators.operation_coordinator import OperationCoordinator
from transbridge.ui.coordinators.parse_coordinator import ParseCoordinator
from transbridge.ui.coordinators.project_coordinator import ProjectCoordinator
from transbridge.ui.main_window import MainWindow
from transbridge.ui.shell import tool_windows as tool_windows_module
from transbridge.ui.shell.menu_builder import MenuBuilder, MenuCallbacks
from transbridge.ui.shell.tool_windows import ToolWindows
from transbridge.ui.workbench import widget as workbench_module

_APP = QApplication.instance() or QApplication([])


def test_current_user_fetch_downloads_avatar_in_existing_api_worker(monkeypatch) -> None:
    events: list[object] = []

    class _UserApi:
        def __init__(self, *, token, config) -> None:
            events.append(("init", token, config))

        def get_my_user(self):
            events.append("user")
            return {"id": 7, "avatar": "https://paratranz.cn/media/avatar.png"}

        def with_avatar_payload(self, user):
            events.append(("avatar", user))
            return {**user, "_avatar_bytes": b"image"}

        def close(self) -> None:
            events.append("close")

    monkeypatch.setattr(tool_windows_module, "ParatranzUserAPI", _UserApi)
    config = SimpleNamespace(token="token")

    user = tool_windows_module._fetch_current_user(config)

    assert user["_avatar_bytes"] == b"image"
    assert events == [
        ("init", "token", config),
        "user",
        ("avatar", {"id": 7, "avatar": "https://paratranz.cn/media/avatar.png"}),
        "close",
    ]


def _callbacks(calls: list[str]) -> MenuCallbacks:
    def callback(name: str):
        return lambda: calls.append(name)

    return MenuCallbacks(
        new_project=callback("project.new"),
        open_project=callback("project.open"),
        prepare_content=callback("translation.prepare_content"),
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
        show_appearance=callback("settings.appearance"),
        show_config=callback("config.open"),
        open_ai_translator=callback("translation.ai"),
        toggle_smart_assistant=callback("assistant.toggle"),
        open_dictionary=callback("dictionary.open"),
        open_history_search=callback("history.search"),
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

    handles.prepare_content.trigger()
    handles.ai_translator.trigger()
    handles.dictionary.trigger()
    handles.history_search.trigger()
    handles.fomod.trigger()
    handles.smart_assistant.trigger()
    handles.view_assistant.trigger()
    handles.appearance.trigger()
    assert calls == [
        "translation.prepare_content",
        "translation.ai",
        "dictionary.open",
        "history.search",
        "publish.fomod",
        "assistant.toggle",
        "assistant.toggle",
        "settings.appearance",
    ]
    window.close()


def test_startup_does_not_block_local_restore_on_optional_token() -> None:
    source = inspect.getsource(MainWindow.__init__)

    assert "if self._ctx.config.token:" in source
    assert "self._tool_windows.show_config()" not in source
    assert "self._project_coordinator.init_workspace(initial_project_path=initial_project_path)" in source


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


def test_explicit_startup_project_takes_precedence_over_active_reference(monkeypatch) -> None:
    workspace = SimpleNamespace()
    monkeypatch.setattr(project_module.WorkspaceState, "load", lambda _path: workspace)
    runtime_context = object()
    prepare_paths: list[tuple[str, object]] = []
    opener = SimpleNamespace(
        has_active_reference=True,
        prepare_path=lambda path, context: prepare_paths.append((path, context)) or "prepared-explicit",
        prepare_active=lambda _context: (_ for _ in ()).throw(AssertionError("active pointer must be skipped")),
    )
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
    coordinator.init_workspace(initial_project_path="D:/projects/selected.json")

    assert context.workspace is workspace
    assert prepare_paths == [("D:/projects/selected.json", runtime_context)]
    assert captured["result"] == "prepared-explicit"
    assert captured["success_verb"] == "已打开"
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
    calls: list[tuple[str, dict[str, object]]] = []
    host = SimpleNamespace(
        workbench=SimpleNamespace(open_tool=lambda tool_id, **kwargs: calls.append((tool_id, kwargs))),
        app_runtime=None,
    )
    tools = ToolWindows(host)
    settings_sections: list[str] = []
    tools.show_ui_settings = lambda section="appearance": settings_sections.append(section)  # type: ignore[method-assign]

    tools.open_ai_translator()

    assert calls[0][0] == "ai_translator"
    calls[0][1]["settings_requested"]()
    assert settings_sections == ["ai_service"]


def test_legacy_batch_ai_entry_routes_to_unified_workbench_tool() -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    host = SimpleNamespace(
        workbench=SimpleNamespace(open_tool=lambda tool_id, **kwargs: calls.append((tool_id, kwargs))),
        app_runtime=None,
    )
    tools = ToolWindows(host)
    settings_sections: list[str] = []
    tools.show_ui_settings = lambda section="appearance": settings_sections.append(section)  # type: ignore[method-assign]

    tools.open_batch_ai_translation()

    assert calls[0][0] == "ai_translator"
    calls[0][1]["settings_requested"]()
    assert settings_sections == ["ai_service"]


def test_history_search_menu_opens_independent_windows() -> None:
    class _Subscription:
        def close(self) -> None:
            pass

    class _TaskRuntime:
        @staticmethod
        def subscribe(*_args, **_kwargs):
            return _Subscription()

    class _Tasks:
        runtime = _TaskRuntime()

    class _Search:
        @staticmethod
        def status():
            return IndexStatus(True, 0, "now")

        @staticmethod
        def scopes():
            return ()

        @staticmethod
        def query(_request):
            return HistorySearchPage((), 0)

    class _UseCases:
        values = {"history_search": _Search(), "history_search_tasks": _Tasks()}

        def names(self):
            return frozenset(self.values)

        def resolve(self, name):
            return self.values[name]

    host = QMainWindow()
    host.app_runtime = SimpleNamespace(use_cases=_UseCases())
    host.runtime_context = SimpleNamespace(
        owner_id="desktop",
        session_id="session",
        permissions=frozenset(),
    )
    host.show_message = lambda _message: None
    tools = ToolWindows(host)

    tools.open_history_search()
    tools.open_history_search()

    windows = tuple(tools._history_search_windows.values())
    assert len(windows) == 2
    assert windows[0] is not windows[1]
    assert all(window.parent() is None for window in windows)
    assert {window._owner.entrypoint for window in windows} == {"history-search:1", "history-search:2"}
    assert {window._taskbar_app_user_model_id for window in windows} == {
        f"TransBridge.HistorySearch.{os.getpid()}.1",
        f"TransBridge.HistorySearch.{os.getpid()}.2",
    }
    assert {window.windowTitle() for window in windows} == {
        "历史翻译与术语搜索 1",
        "历史翻译与术语搜索 2",
    }

    tools.dispose()
    assert tools._history_search_windows == {}
    host.close()


def test_ui_settings_injects_existing_service_configs_into_one_center(monkeypatch) -> None:
    calls: list[object] = []

    class _Dialog:
        def __init__(self, theme, config, parent, **kwargs) -> None:
            calls.append(("construct", theme, config, parent, kwargs))

        def exec(self) -> None:
            calls.append("exec")

    monkeypatch.setattr("transbridge.ui.settings_dialog.SettingsDialog", _Dialog)
    llm = SimpleNamespace()
    monkeypatch.setattr("transbridge.config.llm.LLMConfig.load_from_file", lambda: llm)
    foundation = SimpleNamespace(theme="theme", config="config", registry="registry", locale="locale")
    paratranz = SimpleNamespace(token="")
    host = SimpleNamespace(
        ui_foundation=foundation,
        context=SimpleNamespace(config=paratranz, current_user=None),
    )
    tools = ToolWindows(host)

    tools.show_ui_settings("ai_service")

    assert calls[0][:4] == ("construct", "theme", "config", host)
    kwargs = calls[0][4]
    assert kwargs["initial_section"] == "ai_service"
    assert kwargs["llm_config"] is llm
    assert kwargs["paratranz_config"] is paratranz
    assert callable(kwargs["reload_llm"])
    kwargs["on_paratranz_saved"](paratranz)
    assert host.context.config is paratranz
    assert calls[1:] == ["exec"]


def test_ui_settings_without_foundation_reports_stable_unavailability() -> None:
    messages: list[str] = []
    host = SimpleNamespace(ui_foundation=None, show_message=messages.append)

    ToolWindows(host).show_ui_settings()

    assert messages == ["通用设置当前不可用，请稍后重试。"]


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
