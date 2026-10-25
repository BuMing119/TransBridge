from threading import Event, get_ident
from types import SimpleNamespace

from PyQt6 import sip
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QWidget
import pytest

from tests.ui.tools import test_unified_task_runtime as support
from transbridge.ui.tools.ai_translator import (
    task_empty_recovery,
    task_launch,
    task_recovery_binding,
    task_runtime,
    task_session,
)
from transbridge.ui.tools.ai_translator.ai_translator_window import AITranslatorWindow

qapp = support.qapp
launch = support.launch


def test_completed_recovery_releases_launch_if_window_refresh_fails(launch, monkeypatch):
    active = task_launch.TaskLaunch(launch.window)
    launch.window._task_launch = active
    task_launch._ACTIVE_LAUNCHES.add(active)

    def fail_refresh(_window):
        raise RuntimeError("history refresh failed")

    monkeypatch.setattr(task_empty_recovery, "publish_empty_recovery", fail_refresh)
    active._empty_completed({"state": "completed"})
    assert active.finished and active not in task_launch._ACTIVE_LAUNCHES
    assert launch.window._task_launch is None
    assert launch.messages == [("任务已完成", "任务记录已完成，界面刷新失败：history refresh failed")]
    assert not launch.progress


def block_stage(monkeypatch, phase):
    entered, release = Event(), Event()
    threads = []
    target, name = (task_launch, "inspect_launch") if phase == "inspect" else (task_launch.TaskConsistency, "capture")
    original = getattr(target, name)

    def operation(*args, **kwargs):
        threads.append(get_ident())
        entered.set()
        assert release.wait(5), "test did not release the background launch stage"
        return original(*args, **kwargs)

    monkeypatch.setattr(target, name, operation)
    return entered, release, threads


@pytest.mark.parametrize("blocked", [False, True])
def test_continue_recovery_keeps_real_window_alive_until_async_launch_finishes(launch, monkeypatch, blocked):
    closed = []

    class RecoveryWindow(AITranslatorWindow):
        def __init__(self):
            QWidget.__init__(self)
            for key, value in vars(launch.window).items():
                if key not in {"close", "progress_window_created"}:
                    setattr(self, key, value)
            self.progress_window_created.connect(launch.window.progress_window_created.emit)
            self._task_refresh_timer = QTimer(self)
            self.request_task_refresh = lambda: None
            for name in (
                "_project_terminology",
                "_config_binding",
                "_embedding_connection",
                "_llm_connection",
                "_theme_binding",
            ):
                setattr(self, name, SimpleNamespace(close=lambda: None))

        def closeEvent(self, event):
            closed.append(True)
            super().closeEvent(event)

    owner = RecoveryWindow()
    owner.show()
    tasks = owner._task_sources()

    def configure(window, record, *, restart):
        window._recovery_selection = (record, restart)

    monkeypatch.setattr(task_recovery_binding, "configure_recovery", configure)
    monkeypatch.setattr(task_recovery_binding, "recovery_sources", lambda *_: tasks)
    if blocked:

        def fail_preflight(*_args, **_kwargs):
            raise ValueError("恢复任务运行条件已变化")

        monkeypatch.setattr(task_launch, "preflight_ai_run", fail_preflight)
    entered, release, _ = block_stage(monkeypatch, "inspect")
    try:
        task_recovery_binding.open_recovery(owner, {"task_id": "original-task", "mode": "translate"})
        active = owner._task_launch
        assert active is not None and not active.cancelled
        assert not closed and owner.isVisible()
        launch.wait(entered.is_set)
        assert active.worker.busy and not launch.progress and not closed
        release.set()
        launch.wait(lambda: owner._task_launch is None)
        if blocked:
            assert not closed and owner.isVisible() and not launch.progress
            assert launch.messages == [("AI 任务未启动", "恢复任务运行条件已变化")]
        else:
            assert closed == [True] and not owner.isVisible()
            assert len(launch.progress) == 1 and launch.progress[0].running
            assert launch.progress[0].request.recovery_task_id == "original-task"
            assert not launch.messages
        assert active.finished and active not in task_launch._ACTIVE_LAUNCHES
    finally:
        release.set()
        launch.wait(lambda: getattr(owner, "_task_launch", None) is None)
        owner.close()
        owner.deleteLater()


@pytest.mark.parametrize("phase", ["inspect", "consistency"])
def test_slow_launch_keeps_gui_heartbeat_and_publishes_only_after_preparation(launch, monkeypatch, phase):
    entered, release, threads = block_stage(monkeypatch, phase)
    ticks, published = [], []
    emit = launch.window.progress_window_created.emit
    launch.window.progress_window_created.emit = lambda value: (published.append(get_ident()), emit(value))
    timer = QTimer()
    timer.setInterval(1)
    timer.timeout.connect(lambda: ticks.append(get_ident()))
    task_runtime.start_task(launch.window)
    active = launch.window._task_launch
    try:
        launch.wait(entered.is_set)
        timer.start()
        launch.wait(lambda: len(ticks) >= 5)
        assert active in task_launch._ACTIVE_LAUNCHES and active.worker.busy
        assert not launch.progress and not published
        assert threads == [threads[0]] and threads[0] != get_ident()
        assert all(thread == get_ident() for thread in ticks)
    finally:
        timer.stop()
        release.set()
        launch.wait(lambda: launch.window._task_launch is None)
    assert published == [get_ident()]
    assert launch.progress[0].running
    assert not launch.messages and active not in task_launch._ACTIVE_LAUNCHES


@pytest.mark.parametrize("phase", ["inspect", "consistency"])
def test_cancelled_launch_retains_worker_then_discards_its_late_result(launch, monkeypatch, phase):
    entered, release, _ = block_stage(monkeypatch, phase)
    task_runtime.start_task(launch.window)
    active = launch.window._task_launch
    try:
        launch.wait(entered.is_set)
        active.cancel()
        assert active.cancelled and not active.finished
        assert active in task_launch._ACTIVE_LAUNCHES and active.worker.busy
        assert launch.window._task_launch is active
    finally:
        release.set()
        launch.wait(lambda: launch.window._task_launch is None)
    assert not launch.progress and not launch.messages and not launch.controller.is_running
    assert active.finished and active not in task_launch._ACTIVE_LAUNCHES
    assert not any(event[0] in {"prepared", "created"} for event in launch.events)


def test_cancel_during_session_capture_prevents_file_preflight_and_admission(launch, monkeypatch):
    captured = {}
    job = SimpleNamespace(busy=True, cancelled=False)
    job.cancel = lambda: setattr(job, "cancelled", True)

    def capture(*args, **kwargs):
        captured.update(kwargs)
        return job

    monkeypatch.setattr(task_session.TaskSession, "create_async", capture)
    task_runtime.start_task(launch.window)
    active = launch.window._task_launch
    launch.wait(lambda: bool(captured))
    active.cancel()
    assert job.cancelled and not active.finished and active in task_launch._ACTIVE_LAUNCHES
    job.busy = False
    captured["on_error"]("AI 任务准备已取消。")
    launch.wait(lambda: launch.window._task_launch is None)
    assert not launch.preflighted and not launch.progress and not launch.messages
    assert not launch.controller.is_running


def test_closing_owner_during_launch_cancels_without_starting_progress(launch, monkeypatch):
    entered, release, _ = block_stage(monkeypatch, "consistency")
    task_runtime.start_task(launch.window)
    active = launch.window._task_launch
    try:
        launch.wait(entered.is_set)
        assert launch.controller.is_running
        launch.window.close()
        assert active.cancelled and active.worker.busy and not launch.controller.is_running
    finally:
        release.set()
        launch.wait(lambda: launch.window._task_launch is None)
    assert not launch.progress and not launch.messages
    assert launch.events == [("window-close", None)]


def test_destroyed_qt_owner_never_receives_late_launch_publication(launch, monkeypatch):
    entered, release, _ = block_stage(monkeypatch, "inspect")
    owner = QWidget()
    for key, value in vars(launch.window).items():
        if key != "close":
            setattr(owner, key, value)
    task_runtime.start_task(owner)
    active = owner._task_launch
    try:
        launch.wait(entered.is_set)
        sip.delete(owner)
        assert active.cancelled and active.worker.busy
    finally:
        release.set()
        launch.wait(lambda: active.finished)
    assert not launch.progress and not launch.messages and not launch.controller.is_running
    assert active not in task_launch._ACTIVE_LAUNCHES


def test_repeated_start_while_preparing_does_not_create_a_second_launch(launch, monkeypatch):
    entered, release, threads = block_stage(monkeypatch, "inspect")
    task_runtime.start_task(launch.window)
    active = launch.window._task_launch
    try:
        launch.wait(entered.is_set)
        task_runtime.start_task(launch.window)
        assert launch.window._task_launch is active and len(threads) == 1
        assert launch.messages == [("AI 翻译", "已有任务正在启动。")]
    finally:
        release.set()
        launch.wait(lambda: launch.window._task_launch is None)
    assert len(launch.progress) == 1 and launch.progress[0].running


@pytest.mark.parametrize("phase", ["inspect", "consistency"])
def test_version_change_during_preparation_blocks_late_publication(launch, monkeypatch, phase):
    entered, release, _ = block_stage(monkeypatch, phase)
    task_runtime.start_task(launch.window)
    try:
        launch.wait(entered.is_set)
        launch.window._ctx.active_version_identity = ("project", "other-variant")
    finally:
        release.set()
        launch.wait(lambda: launch.window._task_launch is None)
    assert not launch.progress and not launch.controller.is_running
    assert launch.messages == [("AI 任务未启动", "活动项目或版本已变化")]
    assert not any(event[0] in {"created", "shown", "prepared", "window-close"} for event in launch.events)
