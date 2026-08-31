from __future__ import annotations

# ruff: noqa: E402, I001 - Qt platform must be configured before importing PyQt.

import ast
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import MethodType, SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QThread
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QApplication, QDialog

from transbridge.application.contracts import OperationResult
from transbridge.application.io import FormatId
from transbridge.application.io.identity import EntryKey, ExternalEntryRef, SourceNamespace
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.ui.coordinators.project_coordinator import ProjectCoordinator
from transbridge.ui.main_window import MainWindow, _AutoSaveManager
from transbridge.ui.paratranz import config_dialog as config_dialog_module
from transbridge.ui.paratranz.config_dialog import ConfigDialog
from transbridge.ui.paratranz.string_detail_dialog import StringDetailDialog
from transbridge.ui import source_hydration as source_hydration_module
from transbridge.ui.workers import ApiWorker, get_api_status_bus, get_http_error_bus


_APP = QApplication.instance() or QApplication([])


def _wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.005)
    QApplication.processEvents()
    assert predicate()


def test_visible_disk_task_runs_off_gui_thread_and_restores_progress() -> None:
    started = threading.Event()
    release = threading.Event()
    observed: list[str] = []

    class Workbench:
        def __init__(self) -> None:
            self.messages: list[tuple[int, str]] = []
            self.enabled = True

        def show_step2_progress(self, total: int, message: str) -> None:
            self.messages.append((total, message))

        def hide_step2_progress(self) -> None:
            self.messages.append((-1, "hidden"))

        def setEnabled(self, enabled: bool) -> None:
            self.enabled = enabled

    harness = SimpleNamespace(
        _foreground_worker=None,
        _project_open_worker=None,
        _save_worker=None,
        _workers=[],
        _workbench=Workbench(),
        show_message=observed.append,
    )

    def operation() -> str:
        started.set()
        release.wait(1)
        return "done"

    before = time.monotonic()
    accepted = MainWindow._start_foreground_task(
        harness,
        operation,
        message="正在读取…",
        on_result=observed.append,
    )
    elapsed = time.monotonic() - before

    assert accepted is True
    assert elapsed < 0.1
    assert started.wait(1)
    assert harness._workbench.enabled is False
    assert harness._workbench.messages == [(0, "正在读取…")]

    release.set()
    _wait_until(lambda: harness._foreground_worker is None)
    assert observed == ["done"]
    assert harness._workbench.enabled is True
    assert harness._workbench.messages[-1] == (-1, "hidden")


def test_authoritative_save_runs_off_gui_thread_and_reports_completion() -> None:
    started = threading.Event()
    release = threading.Event()
    completions: list[bool] = []

    class ProjectBar:
        def set_save_dirty(self, _dirty: bool) -> None:
            pass

        def flash_saved(self) -> None:
            pass

    class Workbench:
        project_bar = ProjectBar()

        def __init__(self) -> None:
            self.enabled = True
            self.progress_visible = False

        def show_step2_progress(self, _total: int, _message: str) -> None:
            self.progress_visible = True

        def hide_step2_progress(self) -> None:
            self.progress_visible = False

        def setEnabled(self, enabled: bool) -> None:
            self.enabled = enabled

    class Commands:
        def save(self, _context):
            started.set()
            release.wait(1)
            return SimpleNamespace(is_success=True)

    harness = SimpleNamespace(
        _ctx=SimpleNamespace(uses_authoritative_projection=True, dirty=False),
        _project_commands=Commands(),
        _runtime_context=object(),
        _foreground_worker=None,
        _project_open_worker=None,
        _save_worker=None,
        _save_callbacks=[],
        _workers=[],
        _close_pending=False,
        _workbench=Workbench(),
        show_message=lambda _message: None,
    )
    harness._save_current_project = MethodType(MainWindow._save_current_project, harness)

    before = time.monotonic()
    queued = MainWindow._save_current_project_async(
        harness,
        on_finished=completions.append,
    )
    elapsed = time.monotonic() - before

    assert queued is True
    assert elapsed < 0.1
    assert started.wait(1)
    assert harness._workbench.progress_visible
    assert not harness._workbench.enabled

    release.set()
    _wait_until(lambda: harness._save_worker is None)
    assert completions == [True]
    assert harness._workbench.enabled
    assert not harness._workbench.progress_visible


def test_current_window_project_open_shows_and_clears_start_center_progress() -> None:
    started = threading.Event()
    release = threading.Event()
    progress: list[str | None] = []
    messages: list[str] = []
    restored_sources: list[str] = []
    operation_threads: dict[str, QThread] = {}

    class Workbench:
        def __init__(self) -> None:
            self.progress_visible = False

        def show_step2_progress(self, _total: int, _message: str) -> None:
            self.progress_visible = True

        def hide_step2_progress(self) -> None:
            self.progress_visible = False

    class Opener:
        def prepare_path(self, path, _context):
            operation_threads["prepare"] = QThread.currentThread()
            started.set()
            release.wait(1)
            return OperationResult.completed(SimpleNamespace(path=path))

        def activate(self, _prepared, _context, *, dirty_decision):
            operation_threads["activate"] = QThread.currentThread()
            assert dirty_decision is None
            return OperationResult.completed({
                "name": "OtherMod",
                "sources": [
                    {
                        "source_id": "source-current",
                        "enabled": True,
                        "format_id": "plugin.sse",
                        "location": "D:/mods/OtherMod.esp",
                        "kind": "plugin",
                        "bilingual_capability": "none",
                        "format_options": {},
                    }
                ],
            })

    host = SimpleNamespace(
        context=SimpleNamespace(uses_authoritative_projection=True, dirty=False),
        current_project_opener=Opener(),
        runtime_context=object(),
        project_open_worker=None,
        save_worker=None,
        foreground_worker=None,
        workers=[],
        workbench=Workbench(),
        show_message=messages.append,
        show_workbench=lambda: messages.append("workbench"),
        show_project_open_progress=progress.append,
        hide_project_open_progress=lambda: progress.append(None),
    )

    coordinator = ProjectCoordinator(host)
    coordinator.restore_parse_esp = restored_sources.append  # type: ignore[method-assign]
    coordinator.open_project_path("D:/projects/other.json")

    assert started.wait(1)
    assert progress == ["正在校验并加载本地工程…"]
    assert host.workbench.progress_visible
    release.set()
    _wait_until(lambda: host.project_open_worker is None)

    assert progress == ["正在校验并加载本地工程…", None]
    assert not host.workbench.progress_visible
    assert restored_sources == ["D:/mods/OtherMod.esp"]
    assert messages == ["项目「OtherMod」已打开", "workbench"]
    assert operation_threads["prepare"] is not _APP.thread()
    assert operation_threads["activate"] is _APP.thread()


def test_api_worker_signal_buses_are_owned_by_the_qt_application() -> None:
    assert get_api_status_bus().parent() is _APP
    assert get_http_error_bus().parent() is _APP


def test_api_worker_signal_buses_are_lazy_and_exit_with_the_qt_application() -> None:
    script = """
from transbridge.ui import workers

assert workers._api_status_bus is None
assert workers._http_error_bus is None

from PyQt6.QtWidgets import QApplication

application = QApplication([])
assert workers.get_api_status_bus().parent() is application
assert workers.get_http_error_bus().parent() is application
worker = workers.ApiWorker(lambda: "done", route_http_errors=False)
worker.start()
assert worker.wait(2_000)
application.processEvents()
"""
    environment = dict(os.environ)
    environment["QT_QPA_PLATFORM"] = "offscreen"

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parents[2],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_restore_plugin_sources_only_materializes_the_primary_workbench_source() -> None:
    restored_sources: list[str] = []
    coordinator = ProjectCoordinator(SimpleNamespace())
    coordinator.restore_parse_esp = restored_sources.append  # type: ignore[method-assign]

    coordinator._restore_plugin_sources((
        {
            "source_id": "translation-source",
            "format_id": "plugin.sse",
            "location": "D:/mods/MyMod-zh.esp",
            "legacy": {"role": "migration"},
        },
        {
            "source_id": "primary-source",
            "format_id": "plugin.sse",
            "location": "D:/mods/MyMod.esp",
            "legacy": {"role": "primary"},
        },
    ))

    assert restored_sources == ["D:/mods/MyMod.esp"]


def test_authoritative_hydrations_restore_all_sources_without_reparsing() -> None:
    class Context:
        def __init__(self) -> None:
            self.slots = {"old": object()}

        def remove_slot(self, key: str) -> None:
            self.slots.pop(key)

        def add_slot(self, key: str, slot) -> None:
            self.slots[key] = slot

    states = []
    hydrations = []
    for index in (1, 2):
        namespace = SourceNamespace(f"source:plugin:{index}")
        key = EntryKey(namespace, f"entry-{index}")
        entry = TranslationEntry(
            f"entry-{index}",
            f"entry-{index}",
            f"Original {index}",
            "",
            0,
            "FULL",
            entry_key=key,
        )
        location = f"D:/mods/Plugin{index}.esp"
        hydrations.append(
            SimpleNamespace(
                format_id=FormatId.PLUGIN_SSE,
                location=location,
                entries=(entry.snapshot(),),
                source_snapshot=SimpleNamespace(name=f"snapshot-{index}"),
            )
        )
        state = {
            "entry_key": key.to_dict(),
            "translation": f"权威译文 {index}",
            "stage": 1,
        }
        if index == 1:
            state["external_refs"] = [ExternalEntryRef("paratranz", "project:42", 99).to_dict()]
            state["revision"] = 3
        states.append(state)
    projection = SimpleNamespace(snapshot=lambda: SimpleNamespace(to_dict=lambda: {"values": {"entries": states}}))
    context = Context()
    host = SimpleNamespace(
        context=context,
        app_runtime=SimpleNamespace(use_cases=SimpleNamespace(resolve=lambda _name: projection)),
        show_message=lambda _message: None,
    )
    coordinator = ProjectCoordinator(host)
    coordinator.restore_parse_esp = lambda *_args, **_kwargs: (_ for _ in ()).throw(  # type: ignore[method-assign]
        AssertionError("authoritative hydration must not parse the source again")
    )

    error = coordinator._restore_plugin_sources((), tuple(hydrations))

    assert error is None
    assert set(context.slots) == {"D:/mods/Plugin1.esp", "D:/mods/Plugin2.esp"}
    assert [next(iter(slot.collection)).translation for slot in context.slots.values()] == ["权威译文 1", "权威译文 2"]
    first = next(iter(context.slots["D:/mods/Plugin1.esp"].collection))
    assert first.external_refs == (ExternalEntryRef("paratranz", "project:42", 99),)
    assert first.revision.value == 3
    assert all(slot.plugin is None for slot in context.slots.values())


def test_authoritative_hydration_failure_is_visible_and_keeps_existing_slots(monkeypatch) -> None:
    messages: list[str] = []

    class Context:
        slots = {"existing": object()}

        def remove_slot(self, key: str) -> None:
            self.slots.pop(key)

        def add_slot(self, key: str, slot) -> None:
            self.slots[key] = slot

    projection = SimpleNamespace(snapshot=lambda: SimpleNamespace(to_dict=lambda: {"values": {"entries": []}}))
    host = SimpleNamespace(
        context=Context(),
        app_runtime=SimpleNamespace(use_cases=SimpleNamespace(resolve=lambda _name: projection)),
        show_message=messages.append,
    )
    coordinator = ProjectCoordinator(host)
    monkeypatch.setattr(
        source_hydration_module,
        "slot_from_hydration",
        lambda _hydration: (_ for _ in ()).throw(ValueError("broken hydration")),
    )

    error = coordinator._restore_plugin_sources((), (SimpleNamespace(location="D:/mods/Broken.esp"),))

    assert error is not None and "broken hydration" in error
    assert set(host.context.slots) == {"existing"}
    assert messages == [error]


def test_automatic_save_is_silent_and_does_not_disable_workbench() -> None:
    started = threading.Event()
    release = threading.Event()
    messages: list[str] = []
    dirty_updates: list[bool] = []

    class ProjectBar:
        def set_save_dirty(self, dirty: bool) -> None:
            dirty_updates.append(dirty)

        def flash_saved(self) -> None:
            raise AssertionError("automatic save must not flash the manual-save affordance")

    class Workbench:
        project_bar = ProjectBar()

        def __init__(self) -> None:
            self.enabled = True
            self.progress_visible = False

        def show_step2_progress(self, _total: int, _message: str) -> None:
            self.progress_visible = True

        def hide_step2_progress(self) -> None:
            self.progress_visible = False

        def setEnabled(self, enabled: bool) -> None:
            self.enabled = enabled

    class Commands:
        def save(self, _context):
            started.set()
            release.wait(1)
            return SimpleNamespace(is_success=True)

    harness = SimpleNamespace(
        _ctx=SimpleNamespace(uses_authoritative_projection=True, dirty=False),
        _project_commands=Commands(),
        _runtime_context=object(),
        _foreground_worker=None,
        _project_open_worker=None,
        _save_worker=None,
        _save_callbacks=[],
        _workers=[],
        _close_pending=False,
        _workbench=Workbench(),
        show_message=messages.append,
    )
    harness._save_current_project = MethodType(MainWindow._save_current_project, harness)

    queued = MainWindow._save_current_project_async(harness, automatic=True)

    assert queued is True
    assert started.wait(1)
    assert harness._workbench.enabled
    assert not harness._workbench.progress_visible

    release.set()
    _wait_until(lambda: harness._save_worker is None)
    assert harness._workbench.enabled
    assert not harness._workbench.progress_visible
    assert dirty_updates == [False]
    assert messages == []


def test_autosave_debounce_restarts_after_each_dirty_edit() -> None:
    saves: list[bool] = []
    context = SimpleNamespace(
        dirty=True,
        uses_authoritative_projection=True,
        variant_store=None,
    )
    window = SimpleNamespace(
        context=context,
        save_current_project_async=lambda **kwargs: saves.append(kwargs["automatic"]) or True,
    )
    manager = _AutoSaveManager(window, debounce_ms=80)

    manager.trigger_debounce()
    deadline = time.monotonic() + 0.05
    while time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.005)
    manager.trigger_debounce()
    deadline = time.monotonic() + 0.05
    while time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.005)

    assert saves == []
    _wait_until(lambda: saves == [True])

    context.dirty = False
    manager.trigger_debounce()
    assert not manager._debounce_timer.isActive()
    manager.stop()


def test_autosave_requeues_when_project_is_still_dirty_after_save() -> None:
    requests: list[dict[str, object]] = []
    context = SimpleNamespace(
        dirty=True,
        uses_authoritative_projection=True,
        variant_store=None,
    )
    window = SimpleNamespace(
        context=context,
        save_current_project_async=lambda **kwargs: requests.append(kwargs) or True,
    )
    manager = _AutoSaveManager(window, debounce_ms=80)

    manager._auto_save()

    assert len(requests) == 1
    assert requests[0]["automatic"] is True
    on_finished = requests[0]["on_finished"]
    assert callable(on_finished)
    on_finished(True)
    assert manager._debounce_timer.isActive()

    context.dirty = False
    manager.trigger_debounce()
    assert not manager._debounce_timer.isActive()
    manager.stop()


def test_authoritative_save_does_not_report_current_when_new_changes_remain() -> None:
    completions: list[bool] = []
    dirty_updates: list[bool] = []
    flashes: list[str] = []
    messages: list[str] = []

    class ProjectBar:
        def set_save_dirty(self, dirty: bool) -> None:
            dirty_updates.append(dirty)

        def flash_saved(self) -> None:
            flashes.append("saved")

    class Workbench:
        project_bar = ProjectBar()

        def show_step2_progress(self, _total: int, _message: str) -> None:
            pass

        def hide_step2_progress(self) -> None:
            pass

        def setEnabled(self, _enabled: bool) -> None:
            pass

    class Commands:
        def save(self, _context):
            return SimpleNamespace(is_success=True)

    harness = SimpleNamespace(
        _ctx=SimpleNamespace(uses_authoritative_projection=True, dirty=True),
        _project_commands=Commands(),
        _runtime_context=object(),
        _foreground_worker=None,
        _project_open_worker=None,
        _save_worker=None,
        _save_callbacks=[],
        _workers=[],
        _close_pending=True,
        _workbench=Workbench(),
        show_message=messages.append,
    )
    harness._save_current_project = MethodType(MainWindow._save_current_project, harness)

    queued = MainWindow._save_current_project_async(harness, on_finished=completions.append)

    assert queued is True
    _wait_until(lambda: harness._save_worker is None)
    assert dirty_updates == [True]
    assert completions == [False]
    assert flashes == []
    assert messages == ["保存完成，但仍有新的更改待保存"]


def test_token_verification_is_background_and_displays_progress(monkeypatch) -> None:
    release = threading.Event()

    class Config:
        token = ""
        timeout = 15
        user_id = None

        def update_token(self, token: str) -> None:
            self.token = token

        def update_timeout(self, timeout: int) -> None:
            self.timeout = timeout

        def save_to_file(self) -> None:
            pass

    class ProjectApi:
        def __init__(self, **_kwargs) -> None:
            pass

        def list_projects(self, **_kwargs) -> list:
            release.wait(1)
            return []

    class UserApi:
        def __init__(self, **_kwargs) -> None:
            pass

        def get_my_user(self) -> dict:
            return {"id": 7, "nickname": "tester"}

    config = Config()
    context = SimpleNamespace(config=config)
    monkeypatch.setattr(config_dialog_module, "ParatranzConfig", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(config_dialog_module, "ParatranzProjectAPI", ProjectApi)
    monkeypatch.setattr(config_dialog_module, "ParatranzUserAPI", UserApi)
    dialog = ConfigDialog(context)
    dialog._token_input.setText("secret")

    before = time.monotonic()
    dialog._verify_and_save()
    elapsed = time.monotonic() - before

    assert elapsed < 0.1
    assert dialog._verify_worker is not None
    assert not dialog._progress.isHidden()
    assert not dialog._verify_btn.isEnabled()

    release.set()
    _wait_until(lambda: dialog._verify_worker is not None and not dialog._verify_worker.isRunning())
    assert config.token == "secret"
    assert config.user_id == 7


def test_string_dialog_close_waits_asynchronously_with_progress() -> None:
    release = threading.Event()
    worker = ApiWorker(lambda: release.wait(1), route_http_errors=False)
    dialog = StringDetailDialog.__new__(StringDetailDialog)
    QDialog.__init__(dialog)
    dialog._workers = [worker]
    worker.start()
    _wait_until(worker.isRunning)

    event = QCloseEvent()
    before = time.monotonic()
    dialog.closeEvent(event)
    elapsed = time.monotonic() - before

    assert elapsed < 0.1
    assert not event.isAccepted()
    assert dialog._lifecycle._close_pending
    assert dialog._lifecycle._close_progress is not None

    release.set()
    _wait_until(lambda: not dialog._lifecycle._close_pending)
    assert dialog._lifecycle._close_progress is None


def test_gui_close_handlers_do_not_block_on_thread_waits() -> None:
    root = Path(__file__).parents[2]
    targets = (
        root / "src/transbridge/ui/main_window.py",
        root / "src/transbridge/ui/paratranz/string_detail_dialog.py",
        root / "src/transbridge/ui/tools/ai_translator/_translation_progress_window.py",
        root / "src/transbridge/ui/tools/ai_translator/_batch_translation_progress_window.py",
    )
    offenders: list[str] = []
    for path in targets:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name != "closeEvent":
                continue
            for child in ast.walk(node):
                if (
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Attribute)
                    and child.func.attr in {"wait", "join"}
                ):
                    offenders.append(f"{path.name}:{child.lineno}:{child.func.attr}")
    assert offenders == []


def test_project_import_has_no_fixed_500_mib_limit() -> None:
    ui_root = Path(__file__).parents[2] / "src/transbridge/ui"
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            ui_root / "main_window.py",
            ui_root / "coordinators/project_transfer_coordinator.py",
        )
    )
    assert "500 * 1024 * 1024" not in source
    assert "shutil.disk_usage(destination.parent).free" in source


def test_api_worker_can_report_auth_errors_to_owning_dialog() -> None:
    errors: list[str] = []

    def fail() -> None:
        raise RuntimeError("API Error 401: invalid token")

    worker = ApiWorker(fail, route_http_errors=False)
    worker.error.connect(errors.append)
    worker.start()
    _wait_until(lambda: not worker.isRunning())
    assert errors == ["API Error 401: invalid token"]
