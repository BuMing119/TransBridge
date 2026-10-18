from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from PyQt6.QtCore import QThreadPool
from PyQt6.QtWidgets import QApplication
import pytest

from tests.ui.tools.terminology.test_context_isolation import _Context
from transbridge.application.contracts import RequestContext
from transbridge.ui.tools.terminology.launcher import TerminologyLauncher

_APP = QApplication.instance() or QApplication([])


def _wait(predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.001)
    assert predicate()


@pytest.mark.parametrize("next_identity", [("project-b", "variant-b"), ("project-a", "variant-b"), None])
@pytest.mark.parametrize("fail", [False, True])
def test_context_switch_drains_original_writes_and_opens_a_fresh_window(monkeypatch, next_identity, fail):
    prompts = []
    monkeypatch.setattr(
        "transbridge.ui.tools.terminology.save_close.choose_pending_save_action",
        lambda *_args: prompts.append("stale return-to-edit") or "cancel",
    )
    context = _Context()
    launcher = TerminologyLauncher(
        SimpleNamespace(app_runtime=SimpleNamespace(), runtime_context=RequestContext("operator"), context=context)
    )
    old = launcher.open()
    original = old.presenter.context
    started, release = threading.Event(), threading.Event()
    writes = []

    def write_first():
        started.set()
        assert release.wait(5)
        if fail:
            raise RuntimeError("original database is read-only")
        writes.append((original.project_id, original.variant_id, "first"))

    old._run_draft_command(write_first)
    old._run_draft_command(lambda: writes.append((original.project_id, original.variant_id, "second")))
    _wait(started.is_set)
    try:
        context.switch(next_identity)
        assert not old.context_is_current
        assert not old.workspace.isEnabled()
        assert not prompts
        assert old._draft_writes.pending_count == 2
        old._run_draft_command(lambda: writes.append("stale command"))
        assert old._draft_writes.pending_count == 2
        old_presenter = old.presenter
        current = launcher.open()
        assert current is not old
        assert current.context_is_current
        assert (current.presenter.context.project_id, current.presenter.context.variant_id) == (
            next_identity or (None, None)
        )
        assert launcher.open() is current
        assert not old_presenter.closed
        release.set()
        _wait(lambda: len(writes) == (1 if fail else 2))
        if fail:
            _wait(lambda: "original database is read-only" in old.operation_notice.message.text())
            assert not old_presenter.closed
            assert "切回原版本" in old.operation_notice.message.text()
            assert not old.workspace.isEnabled()
            old.close()
        _wait(lambda: old_presenter.closed)
        assert writes == [("project-a", "variant-a", name) for name in (("second",) if fail else ("first", "second"))]
        assert current.context_is_current
        assert not launcher._retiring
    finally:
        release.set()
        QThreadPool.globalInstance().waitForDone(5000)
        _APP.processEvents()
        launcher.close()
        _APP.processEvents()


def test_launcher_shutdown_waits_for_accepted_saves(monkeypatch):
    monkeypatch.setattr("transbridge.ui.tools.terminology.save_close.choose_pending_save_action", lambda *_: "wait")
    context = _Context()
    launcher = TerminologyLauncher(
        SimpleNamespace(app_runtime=SimpleNamespace(), runtime_context=RequestContext("operator"), context=context)
    )
    window = launcher.open()
    presenter = window.presenter
    started, release = threading.Event(), threading.Event()
    saved = []

    def write():
        started.set()
        assert release.wait(5)
        saved.append("saved")

    window._run_draft_command(write)
    _wait(started.is_set)
    try:
        launcher.close()
        _APP.processEvents()
        assert launcher.window is window
        assert not presenter.closed
        release.set()
        _wait(lambda: presenter.closed)
        assert saved == ["saved"]
    finally:
        release.set()
        QThreadPool.globalInstance().waitForDone(5000)
        _APP.processEvents()
        launcher.close()
