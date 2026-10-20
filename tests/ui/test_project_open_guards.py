"""Busy guards must return before any lifecycle lock or conflicting command."""

from types import SimpleNamespace

import pytest

from transbridge.ui.coordinators.source_update_coordinator import SourceUpdateCoordinator
from transbridge.ui.main_window import MainWindow
from transbridge.ui.shell.action_catalog import IntentId
from transbridge.ui.shell.intent_composition import ShellIntentComposition
from transbridge.ui.shell.intent_router import IntentDispatchResult
from transbridge.ui.shell.window_lifecycle import WindowLifecycle
from transbridge.ui.tools.ai_translator.task_recovery_prompt import TaskRecoveryDiscovery
from transbridge.ui.tools.ai_translator.task_registry import project_directory


@pytest.mark.parametrize("intent", [IntentId.PROJECT_OPEN, IntentId.PROJECT_SAVE, IntentId.TRANSLATION_AI])
def test_opening_rejects_conflicting_shell_commands(intent):
    called, messages = [], []
    shell = ShellIntentComposition.__new__(ShellIntentComposition)
    shell._host = SimpleNamespace(context=SimpleNamespace(project_opening=True), show_message=messages.append)
    shell.router = SimpleNamespace(dispatch=lambda *args, **kwargs: called.append(args))
    result = shell.dispatch(intent)
    assert not result.accepted
    assert messages and not called


@pytest.mark.parametrize("intent", [IntentId.APP_EXIT, IntentId.HELP_CONTEXT, IntentId.SETTINGS_APPEARANCE])
def test_opening_keeps_non_project_commands_available(intent):
    shell = ShellIntentComposition.__new__(ShellIntentComposition)
    shell._host = SimpleNamespace(context=SimpleNamespace(project_opening=True))
    shell.router = SimpleNamespace(dispatch=lambda action, *args, **kwargs: IntentDispatchResult(action, True))
    assert shell.dispatch(intent).accepted


def test_failed_view_allows_reopen_but_rejects_writing():
    shell = ShellIntentComposition.__new__(ShellIntentComposition)
    shell._host = SimpleNamespace(
        context=SimpleNamespace(project_opening=False, project_view_failed=True), show_message=lambda _: None
    )
    shell.router = SimpleNamespace(dispatch=lambda action, *args, **kwargs: IntentDispatchResult(action, True))
    assert shell.dispatch(IntentId.PROJECT_OPEN).accepted
    assert not shell.dispatch(IntentId.PROJECT_SAVE).accepted


def test_finished_worker_with_pending_install_still_blocks_foreground_and_save():
    host = SimpleNamespace(
        _ctx=SimpleNamespace(project_opening=True),
        _foreground_worker=None,
        _save_worker=None,
        _project_open_worker=SimpleNamespace(isRunning=lambda: False),
        show_message=lambda _: None,
    )
    assert not MainWindow.start_foreground_task(host, lambda: None, message="test")
    assert not MainWindow.save_current_project_async(host)


def test_source_update_and_recovery_avoid_querying_lifecycle_during_open():
    context = SimpleNamespace(project_opening=True)
    coordinator = SourceUpdateCoordinator(SimpleNamespace(context=context, show_message=lambda _: None))
    coordinator.start_current()  # No runtime exists: reaching the service would fail.
    assert project_directory(context, object(), object()) is None
    scheduled = []
    discovery = SimpleNamespace(
        registry=SimpleNamespace(ctx=context, shutting_down=False),
        worker=None,
        timer=SimpleNamespace(start=scheduled.append),
    )
    TaskRecoveryDiscovery.discover(discovery)
    assert scheduled == [100]


def test_close_waits_until_gui_installation_finishes(monkeypatch):
    scheduled = []
    monkeypatch.setattr(
        "transbridge.ui.shell.window_lifecycle.QTimer.singleShot", lambda interval, callback: scheduled.append(interval)
    )
    lifecycle = WindowLifecycle.__new__(WindowLifecycle)
    lifecycle._close_pending = True
    lifecycle._check_inline_drafts = lambda: True
    lifecycle._check_runtime_tasks = lambda: True
    lifecycle._input_guard = SimpleNamespace(suspend=lambda: None)
    lifecycle._host = SimpleNamespace(
        context=SimpleNamespace(project_opening=True),
        project_open_worker=SimpleNamespace(isRunning=lambda: False),
        workbench=SimpleNamespace(),
    )
    lifecycle.begin_background_close()
    assert scheduled == [100]


@pytest.mark.parametrize("state", ["project_opening", "project_view_failed"])
def test_cancelling_close_does_not_unlock_a_busy_or_failed_project_view(state):
    enabled = []
    lifecycle = WindowLifecycle.__new__(WindowLifecycle)
    lifecycle._task_close = None
    lifecycle._recovery = SimpleNamespace(reset=lambda: None)
    lifecycle._input_guard = SimpleNamespace(resume=lambda: None)
    lifecycle.auto_saver = SimpleNamespace(start=lambda: None)
    lifecycle._host = SimpleNamespace(
        context=SimpleNamespace(**{state: True}),
        close_pending=True,
        workbench=SimpleNamespace(setEnabled=enabled.append, hide_step2_progress=lambda: None),
    )
    lifecycle._cancel_close()
    assert enabled == [False]
    assert not lifecycle._host.close_pending


@pytest.mark.parametrize("dirty", [False, True])
def test_failed_view_can_close_only_if_authoritative_state_is_clean(monkeypatch, dirty):
    callbacks, outcomes = [], []
    monkeypatch.setattr("transbridge.ui.main_window.QTimer.singleShot", lambda _, callback: callbacks.append(callback))
    host = SimpleNamespace(
        _ctx=SimpleNamespace(project_view_failed=True, dirty=dirty),
        _close_pending=True,
        show_message=lambda _: None,
    )
    assert MainWindow.save_current_project_async(host, on_finished=outcomes.append)
    assert not outcomes
    callbacks[0]()
    assert outcomes == [not dirty]
