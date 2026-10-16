from __future__ import annotations

import gc
import os
import sys
from threading import get_ident
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import sip
from PyQt6.QtCore import QObject, QThreadPool, pyqtSignal
from PyQt6.QtWidgets import QApplication, QWidget

from tests.ui.workbench.terminology_async_support import wait_profile, wait_until
from transbridge.application.terminology_profiles import InMemoryTerminologyProfileRepository, TerminologyProfileService
from transbridge.ui.workbench.terminology_profile_bar import TerminologyProfileBar
from transbridge.ui.workbench.terminology_profile_controller import TerminologyProfileUiController

_APP = QApplication.instance() or QApplication([])


class _Context(QObject):
    project_changed = pyqtSignal()
    variant_changed = pyqtSignal(str)
    active_version_identity = ("a", "v")


class _Pool:
    def __init__(self):
        self.calls = []

    def start(self, call):
        self.calls.append(call)


def _service(project):
    service = TerminologyProfileService(InMemoryTerminologyProfileRepository())
    profile = service.create(project, project)
    service.publish(profile.profile_id, expected_draft_revision=0)
    return service, profile.profile_id


def _controller(factory, pool):
    ctx, bar = _Context(), TerminologyProfileBar()
    previews = []
    view = SimpleNamespace(set_terminology_profile=lambda profile, **_kwargs: previews.append(profile))
    controller = TerminologyProfileUiController(ctx, factory, bar, view, thread_pool=pool)
    return ctx, bar, controller, previews


def test_service_summaries_selected_revision_and_base_read_off_main_thread(monkeypatch):
    service, profile_id = _service("a")
    service.select("a", "v", profile_id)
    reads, changes = [], []
    main = get_ident()
    selected = service.selected_revision
    summaries = service.list_profile_summaries

    def full_profiles(*_args):
        raise AssertionError("selector must not load all draft content")

    monkeypatch.setattr(service, "list_profiles", full_profiles)
    monkeypatch.setattr(service, "selected_revision", lambda *args: reads.append(get_ident()) or selected(*args))
    monkeypatch.setattr(service, "list_profile_summaries", lambda *args: reads.append(get_ident()) or summaries(*args))
    factory = SimpleNamespace(
        profile_service_for=lambda _project: reads.append(get_ident()) or service,
        base_terminology_snapshot=lambda *_args: reads.append(get_ident()),
    )
    pool = QThreadPool()
    _ctx, bar, controller, previews = _controller(factory, pool)
    controller.state_changed.connect(lambda _state: changes.append(get_ident()))
    assert controller.refresh_pending
    assert not bar.combo.isEnabled()
    wait_profile(controller)
    assert reads and main not in reads
    assert changes == [main]
    assert previews[-1].profile_id == profile_id
    assert controller.state.enabled
    bar.close()
    assert pool.waitForDone(1000)


def test_refresh_coalesces_queued_reads_and_rejects_late_old_context():
    services = {key: _service(key)[0] for key in ("a", "b")}
    pool = _Pool()
    ctx, bar, controller, previews = _controller(SimpleNamespace(profile_service_for=services.__getitem__), pool)
    assert len(pool.calls) == 1
    pool.calls[0].run()
    ctx.active_version_identity = ("b", "new")
    ctx.project_changed.emit()
    controller.refresh()
    controller.refresh()
    _APP.processEvents()
    assert len(pool.calls) == 2
    assert controller.refresh_pending
    assert previews == [None, None]
    pool.calls[1].run()
    wait_profile(controller)
    assert [item.label for item in controller.state.choices] == ["b"]
    bar.close()


def test_pending_selection_finishes_only_in_captured_project_and_variant():
    services, identifiers = {}, {}
    for key in ("a", "b"):
        services[key], identifiers[key] = _service(key)
    pool = _Pool()
    ctx, bar, controller, _previews = _controller(SimpleNamespace(profile_service_for=services.__getitem__), pool)
    pool.calls[0].run()
    wait_profile(controller)
    controller.select(identifiers["a"])
    ctx.active_version_identity = ("b", "new")
    ctx.project_changed.emit()
    pool.calls[1].run()
    wait_until(lambda: len(pool.calls) == 3)
    pool.calls[2].run()
    wait_profile(controller)
    assert services["a"].selected_revision("a", "v").profile_id == identifiers["a"]
    assert services["b"].selected_revision("b", "new") is None
    assert controller.state.selected_revision is None
    assert [item.label for item in controller.state.choices] == ["b"]
    bar.close()


def test_selection_commands_are_serial_and_in_submission_order():
    service, first = _service("a")
    second = service.create("a", "second")
    service.publish(second.profile_id, expected_draft_revision=0)
    pool = _Pool()
    _ctx, bar, controller, _previews = _controller(SimpleNamespace(profile_service_for=lambda _key: service), pool)
    pool.calls[0].run()
    wait_profile(controller)
    controller.select(first)
    controller.select(second.profile_id)
    assert len(pool.calls) == 2
    pool.calls[1].run()
    wait_until(lambda: len(pool.calls) == 3)
    assert service.selected_revision("a", "v").profile_id == first
    pool.calls[2].run()
    wait_profile(controller)
    assert service.selected_revision("a", "v").profile_id == second.profile_id
    assert controller.state.selected_profile_id == second.profile_id
    bar.close()


def test_close_rejects_queued_completion_and_read_errors_are_actionable():
    def fail(_project):
        raise PermissionError("workspace is read-only")

    pool = _Pool()
    _ctx, bar, controller, previews = _controller(SimpleNamespace(profile_service_for=fail), pool)
    pool.calls[0].run()
    wait_profile(controller)
    assert "workspace is read-only" in controller.state.detail
    assert not controller.state.enabled
    controller.refresh()
    pool.calls[1].run()
    before = controller.state
    count = len(previews)
    bar.close()
    _APP.processEvents()
    assert controller.state is before
    assert len(previews) == count


def test_ignored_host_close_keeps_controller_active_then_destroy_cancels(monkeypatch):
    class Host(QWidget):
        def closeEvent(self, event):
            event.ignore()

    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *args: errors.append(args))
    service, _profile = _service("a")
    pool = _Pool()
    _ctx, bar, controller, _previews = _controller(SimpleNamespace(profile_service_for=lambda _key: service), pool)
    host = Host()
    bar.setParent(host)
    host.show()
    _APP.processEvents()
    host.close()
    _APP.processEvents()
    assert not controller._refresh._closed.is_set()
    pool.calls[0].run()
    wait_profile(controller)
    assert controller.state.enabled
    bar.close()
    sip.delete(host)
    _APP.processEvents()
    assert controller._refresh._closed.is_set()
    assert errors == []


def test_profile_controller_destroy_callbacks_survive_cyclic_gc(monkeypatch):
    errors, events = [], []
    monkeypatch.setattr(sys, "excepthook", lambda *args: errors.append(args))
    service, _profile = _service("a")

    def create():
        _ctx, _bar, controller, _previews = _controller(
            SimpleNamespace(profile_service_for=lambda _key: service), _Pool()
        )
        events.append(controller._refresh._closed)

    for _ in range(10):
        create()
    gc.collect()
    _APP.processEvents()
    assert errors == []
    assert all(event.is_set() for event in events)
