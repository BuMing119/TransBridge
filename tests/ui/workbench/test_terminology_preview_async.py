from __future__ import annotations

from dataclasses import replace
import gc
import os
import sys
from threading import get_ident
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import sip
from PyQt6.QtCore import QThreadPool
from PyQt6.QtWidgets import QApplication, QWidget
import pytest

from tests.ui.workbench.terminology_async_support import wait_until
from transbridge.application.projections import ProjectionSnapshot, ProjectionStore
from transbridge.application.terminology_profiles import (
    ProfileEntryOverride,
    PublishedTerminologyProfile,
    TerminologyProfileContent,
)
from transbridge.application.terminology_profiles.entry_projection import ProfileEntryInput, ProfileEntryProjector
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.ui import context as context_module
from transbridge.ui.workbench.terminology_preview import (
    TerminologyPreviewCoordinator,
    preview_identity,
    profile_snapshot_key,
)

_APP = QApplication.instance() or QApplication([])


class _Pool:
    def __init__(self):
        self.calls = []

    def start(self, call):
        self.calls.append(call)


def _entries(count=1):
    return [TranslationEntry(str(i), str(i), "Dragon", "Old", 1, None) for i in range(count)]


def _profile(entries, value="New"):
    content = TerminologyProfileContent(
        overrides=tuple(ProfileEntryOverride(item.identity.serialize(), value) for item in entries)
    )
    return PublishedTerminologyProfile("p", "project", 1, value, content.content_digest, content, "2026-10-15")


def test_capture_is_bounded_and_yields_before_all_entries_are_frozen(monkeypatch):
    owner, pool = QWidget(), _Pool()
    entries = _entries(7)
    captures = []
    original = ProfileEntryInput.capture
    monkeypatch.setattr(ProfileEntryInput, "capture", lambda item: captures.append(item.id) or original(item))
    coordinator = TerminologyPreviewCoordinator(owner, identity=lambda: "scope", thread_pool=pool, batch_size=2)
    coordinator.set_entries(entries)
    coordinator.set_profile(_profile(entries))
    assert captures == []
    coordinator._timer.stop()
    coordinator._capture_batch()
    coordinator._timer.stop()
    assert captures == ["0", "1"]
    assert not pool.calls
    assert coordinator.pending
    coordinator.close()
    owner.close()


def test_queued_result_from_old_profile_cannot_replace_new_selection():
    owner, pool = QWidget(), _Pool()
    entries = _entries()
    coordinator = TerminologyPreviewCoordinator(owner, identity=lambda: "scope", thread_pool=pool)
    coordinator.set_entries(entries)
    coordinator.set_profile(_profile(entries, "Old scheme"))
    wait_until(lambda: len(pool.calls) == 1)
    pool.calls[0].run()  # result is queued, but not yet applied to Qt
    coordinator.set_profile(_profile(entries, "New scheme"))
    _APP.processEvents()
    assert not coordinator.projection
    wait_until(lambda: len(pool.calls) == 2)
    pool.calls[1].run()
    wait_until(lambda: not coordinator.pending)
    assert next(iter(coordinator.projection.values())).translation == "New scheme"
    owner.close()


@pytest.mark.parametrize("change", ["collection", "context", "close"])
def test_collection_revision_context_and_close_isolate_in_flight_results(change):
    owner, pool = QWidget(), _Pool()
    entries = _entries()
    identity = ["variant", 1]
    coordinator = TerminologyPreviewCoordinator(owner, identity=lambda: tuple(identity), thread_pool=pool)
    coordinator.set_entries(entries)
    coordinator.set_profile(_profile(entries))
    wait_until(lambda: len(pool.calls) == 1)
    pool.calls[0].run()
    if change == "close":
        owner.close()
    elif change == "context":
        identity[0] = "other-variant"
    else:
        identity[1] += 1
        coordinator.set_entries([replace(entries[0], translation="Changed")])
    _APP.processEvents()
    assert not coordinator.projection
    if change == "context":
        assert not coordinator.pending
        assert "上下文已变化" in coordinator.message
    if change == "collection":
        wait_until(lambda: len(pool.calls) == 2)
        pool.calls[1].run()
        wait_until(lambda: not coordinator.pending)
        assert next(iter(coordinator.projection.values())).common_translation == "Changed"
    owner.close()


def test_worker_failure_is_visible_and_retry_can_finish(monkeypatch):
    owner, pool = QWidget(), _Pool()
    entries = _entries()
    coordinator = TerminologyPreviewCoordinator(owner, identity=lambda: "scope", thread_pool=pool)
    coordinator.set_entries(entries)
    profile = _profile(entries)
    coordinator.set_profile(profile)
    wait_until(lambda: len(pool.calls) == 1)
    original = ProfileEntryProjector.project_input

    def fail(*_args):
        raise ValueError("broken input")

    monkeypatch.setattr(ProfileEntryProjector, "project_input", fail)
    pool.calls[0].run()
    wait_until(lambda: not coordinator.pending)
    assert "broken input" in coordinator.message
    assert not coordinator.projection
    monkeypatch.setattr(ProfileEntryProjector, "project_input", original)
    coordinator.set_profile(profile)
    wait_until(lambda: len(pool.calls) == 2)
    pool.calls[1].run()
    wait_until(lambda: not coordinator.pending)
    assert not coordinator.error
    assert next(iter(coordinator.projection.values())).translation == "New"
    owner.close()


def test_only_immutable_inputs_and_prepared_projection_run_on_worker(monkeypatch):
    owner, pool = QWidget(), QThreadPool()
    entries = _entries(20)
    ui_thread = get_ident()
    capture_threads, worker_threads, ready_threads = [], [], []
    original_capture = ProfileEntryInput.capture
    original_project = ProfileEntryProjector.project_input
    original_init = ProfileEntryProjector.__init__

    def capture(entry):
        capture_threads.append(get_ident())
        return original_capture(entry)

    def initialize(self, *args):
        worker_threads.append(get_ident())
        original_init(self, *args)

    def project(self, entry):
        assert isinstance(entry, ProfileEntryInput)
        worker_threads.append(get_ident())
        return original_project(self, entry)

    monkeypatch.setattr(ProfileEntryInput, "capture", capture)
    monkeypatch.setattr(ProfileEntryProjector, "__init__", initialize)
    monkeypatch.setattr(ProfileEntryProjector, "project_input", project)
    coordinator = TerminologyPreviewCoordinator(owner, identity=lambda: "scope", thread_pool=pool)
    coordinator.changed.connect(lambda: ready_threads.append(get_ident()))
    coordinator.set_entries(entries)
    coordinator.set_profile(_profile(entries))
    wait_until(lambda: not coordinator.pending)
    assert len(coordinator.projection) == 20
    assert set(capture_threads) == set(ready_threads) == {ui_thread}
    assert worker_threads and ui_thread not in worker_threads
    # The same immutable indexes survive a collection refresh.
    prepared = coordinator._prepared
    coordinator.set_entries(entries)
    wait_until(lambda: not coordinator.pending)
    assert coordinator._prepared is prepared
    owner.close()
    assert pool.waitForDone(1000)


def test_same_profile_id_and_digest_in_other_project_do_not_reuse_context():
    profile = _profile(_entries())
    other = replace(profile, project_id="other")
    assert profile_snapshot_key(profile, None) != profile_snapshot_key(other, None)


def test_ignored_host_close_preserves_preview_and_deleted_owner_cancels(monkeypatch):
    class Host(QWidget):
        def closeEvent(self, event):
            event.ignore()

    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *args: errors.append(args))
    host, pool = Host(), _Pool()
    owner = QWidget(host)
    coordinator = TerminologyPreviewCoordinator(owner, identity=lambda: "scope", thread_pool=pool)
    entries = _entries()
    coordinator.set_entries(entries)
    coordinator.set_profile(_profile(entries))
    host.show()
    wait_until(lambda: len(pool.calls) == 1)
    host.close()
    _APP.processEvents()
    assert not coordinator._closed.is_set()
    pool.calls[0].run()
    wait_until(lambda: not coordinator.pending)
    assert coordinator.projection
    owner.close()
    sip.delete(host)
    _APP.processEvents()
    assert coordinator._closed.is_set()
    assert errors == []


def test_destroyed_callbacks_survive_cyclic_gc(monkeypatch):
    errors, events = [], []
    monkeypatch.setattr(sys, "excepthook", lambda *args: errors.append(args))

    def create():
        owner = QWidget()
        coordinator = TerminologyPreviewCoordinator(owner, identity=lambda: owner.objectName())
        events.append(coordinator._closed)

    for _ in range(10):
        create()
    gc.collect()
    _APP.processEvents()
    assert errors == []
    assert all(event.is_set() for event in events)


@pytest.mark.parametrize("worker_started", [False, True])
def test_real_app_context_revision_change_rejects_frozen_old_text(monkeypatch, worker_started):
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: SimpleNamespace(token=""))
    values = {"project_id": "project", "variant_id": "variant", "project_revision": 1, "variant_revision": 1}
    store = ProjectionStore(ProjectionSnapshot("project:project", 1, 1, values))
    context = context_module.AppContext(project_projection=store)
    owner, pool = QWidget(), _Pool()
    entries = _entries(2)
    coordinator = TerminologyPreviewCoordinator(
        owner, identity=lambda: preview_identity(context, "scope"), thread_pool=pool, batch_size=1
    )
    changed = []
    coordinator.changed.connect(lambda: changed.append(coordinator.message))
    coordinator.set_entries(entries)
    coordinator.set_profile(_profile(entries))
    if worker_started:
        wait_until(lambda: len(pool.calls) == 1)
    else:
        coordinator._timer.stop()
        coordinator._capture_batch()
        coordinator._timer.stop()
        assert coordinator._offset == 1
    before = preview_identity(context, "scope")
    with pytest.warns(DeprecationWarning):
        entries[0].translation = "Changed during capture"
    store.rebuild(ProjectionSnapshot("project:project", 2, 1, dict(values, project_revision=2, variant_revision=2)))
    assert preview_identity(context, "scope") != before
    if worker_started:
        pool.calls[0].run()
        _APP.processEvents()
    else:
        coordinator._capture_batch()
    assert not coordinator.pending
    assert not coordinator.projection
    assert changed and "上下文已变化" in changed[-1]
    owner.close()
    context.close_projection()
