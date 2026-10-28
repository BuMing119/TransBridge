from __future__ import annotations

import os
import threading
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402
import pytest  # noqa: E402

from transbridge.ui.main_window import MainWindow  # noqa: E402
from transbridge.ui.workbench._project_bar import ProjectBar  # noqa: E402
from transbridge.ui.workbench.save_presenter import SavePhase  # noqa: E402

_APP = QApplication.instance() or QApplication([])


def _wait_until(predicate) -> None:
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.005)
    _APP.processEvents()
    assert predicate()


@pytest.fixture
def save_host():
    signal = SimpleNamespace(connect=lambda _callback: None)
    context = SimpleNamespace(
        uses_authoritative_projection=True,
        active_project_id="project",
        project_name="Project",
        active_variant_id="variant",
        active_version_identity=("project", "variant"),
        project_variants=({"id": "variant", "name": "Default"},),
        project_sources=(),
        dirty=True,
        workspace_changed=signal,
        project_changed=signal,
        variant_changed=signal,
    )
    bar = ProjectBar(context)
    bar.refresh()
    host = SimpleNamespace(
        _ctx=context,
        _foreground_worker=None,
        _project_open_worker=None,
        _save_worker=None,
        _save_callbacks=[],
        _workers=[],
        _close_pending=False,
        _workbench=SimpleNamespace(
            project_bar=bar,
            show_step2_progress=lambda *_args: None,
            hide_step2_progress=lambda: None,
            setEnabled=lambda _enabled: None,
        ),
        messages=[],
    )
    host.errors = []

    def show_message(message, *, error=False, **_options):
        host.messages.append(message)
        if error:
            host.errors.append(message)

    host.show_message = show_message
    yield host
    for worker in host._workers:
        assert worker.wait(5_000)
    _APP.processEvents()
    bar.close()


@pytest.mark.parametrize("automatic", [True, False])
@pytest.mark.parametrize("remaining_dirty", [True, False])
def test_successful_save_leaves_saving_state_and_enables_button(save_host, automatic, remaining_dirty):
    host = save_host
    bar = host._workbench.project_bar
    release = threading.Event()
    completed = []

    def save():
        assert release.wait(5)
        return SimpleNamespace(is_success=True)

    host._save_current_project = save
    try:
        assert MainWindow.save_current_project_async(host, automatic=automatic, on_finished=completed.append)
        assert bar.save_state.phase is SavePhase.SAVING
        assert not bar._save_btn.isEnabled()
        # Dirty notifications during a save must not prematurely enable the button.
        bar.set_save_dirty(True)
        host._ctx.dirty = remaining_dirty
        bar.set_save_dirty(remaining_dirty)
        assert bar.save_state.phase is SavePhase.SAVING
        assert not bar._save_btn.isEnabled()
    finally:
        release.set()
        _wait_until(lambda: bool(completed))

    assert host._save_worker is None
    assert completed == [not remaining_dirty]
    assert bar.save_state.phase is (SavePhase.DIRTY if remaining_dirty else SavePhase.CLEAN)
    assert bar.save_state.saved_at is not None
    assert bar._save_btn.isEnabled()
    if automatic:
        assert host.messages == []
        assert bar._save_btn.text() == "保存"
    if remaining_dirty:
        assert bar._save_btn.text() != "已保存"


def test_failed_automatic_save_remains_retryable_and_retry_completes(save_host):
    host = save_host
    bar = host._workbench.project_bar
    completed = []

    def fail():
        raise OSError("QA disk write failure")

    host._save_current_project = fail
    assert MainWindow.save_current_project_async(host, automatic=True, on_finished=completed.append)
    _wait_until(lambda: bool(completed))
    assert completed == [False]
    assert bar.save_state.phase is SavePhase.FAILED
    assert "QA disk write failure" in bar.save_state.diagnostic
    assert any("QA disk write failure" in message for message in host.errors)
    assert bar._save_btn.text() == "重试保存"
    assert bar._save_btn.isEnabled()

    def save():
        host._ctx.dirty = False
        return SimpleNamespace(is_success=True)

    host._save_current_project = save
    assert MainWindow.save_current_project_async(host, automatic=True, on_finished=completed.append)
    _wait_until(lambda: len(completed) == 2)
    assert completed == [False, True]
    assert bar.save_state.phase is SavePhase.CLEAN
    assert bar._save_btn.isEnabled()


def test_finished_thread_remains_owned_until_gui_completion_is_delivered(save_host):
    host = save_host
    bar = host._workbench.project_bar
    completed = []
    calls = []

    def save():
        calls.append("save")
        host._ctx.dirty = False
        return SimpleNamespace(is_success=True)

    host._save_current_project = save
    assert MainWindow.save_current_project_async(host, automatic=True, on_finished=completed.append)
    first = host._save_worker
    assert first.wait(5_000)  # Deliberately do not dispatch queued Qt completion signals yet.
    assert not first.isRunning()
    assert bar.save_state.phase is SavePhase.SAVING

    assert not MainWindow.start_foreground_task(host, lambda: None, message="Other operation")
    assert MainWindow.save_current_project_async(host, automatic=True, on_finished=completed.append)
    assert host._save_worker is first
    assert len(host._workers) == 1
    assert completed == []
    assert not bar._save_btn.isEnabled()
    _wait_until(lambda: len(completed) == 2)

    assert calls == ["save"]
    assert completed == [True, True]
    assert host._save_worker is None
    assert bar.save_state.phase is SavePhase.CLEAN
    assert bar._save_btn.isEnabled()


def test_save_completion_does_not_acknowledge_another_clean_project(save_host):
    host = save_host
    completed = []
    host._save_current_project = lambda: SimpleNamespace(is_success=True)
    assert MainWindow.save_current_project_async(host, automatic=True, on_finished=completed.append)
    assert host._save_worker.wait(5_000)
    host._ctx.active_version_identity = ("other-project", "other-variant")
    host._ctx.dirty = False
    _wait_until(lambda: bool(completed))
    assert completed == [False]
    assert host.messages == []
