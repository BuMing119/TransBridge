from contextlib import contextmanager
from dataclasses import replace
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, QThreadPool, pyqtSignal
from PyQt6.QtWidgets import QApplication, QWidget
import pytest

from tests.ui.workbench.terminology_async_support import wait_until
from tests.ui.workbench.test_terminology_preview_async import _entries, _Pool, _profile
from transbridge.application.terminology_profiles import ProjectedTranslation
from transbridge.application.terminology_profiles.entry_projection import ProfileEntryInput
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.ui.project_rows import ProjectRowsCollection
from transbridge.ui.workbench.terminology_alignment import AlignmentBarBinding, freeze_alignment_scope
from transbridge.ui.workbench.terminology_preview import TerminologyPreviewCoordinator
from transbridge.ui.workbench.terminology_profile_bar import TerminologyProfileBar, TerminologyProfileBarState

_APP = QApplication.instance() or QApplication([])


def _setup(monkeypatch, *, prepare_error=None, pool=None):
    from transbridge.application.terminology_profiles import profile_alignment

    owner = QWidget()
    pool = pool or _Pool()
    calls = []
    resolver = SimpleNamespace(resolve=lambda _request: None)

    @contextmanager
    def open_service():
        calls.append("open")
        try:
            yield SimpleNamespace(resolve_many=lambda requests, **options: ())
        finally:
            calls.append("close")

    def prepare():
        calls.append("prepare")
        if prepare_error:
            raise ValueError(prepare_error)
        return SimpleNamespace(open=open_service)

    def align(entries, profile, base, *, source_profile, service, cancellation, progress, prior, retry_uncertain=True):
        assert all(isinstance(entry, ProfileEntryInput) for entry in entries)
        calls.append((entries, cancellation, prior))
        cancellation.raise_if_cancelled()
        service.resolve_many(entries, cancellation=cancellation, progress=progress)
        progress(1, len(entries))
        return resolver, {
            entry.entry_key: ProjectedTranslation(entry.common_translation, "AI result") for entry in entries
        }

    monkeypatch.setattr(profile_alignment, "align_profile_entries", align)
    identity = ["project", "variant", 1]
    coordinator = TerminologyPreviewCoordinator(
        owner, identity=lambda: tuple(identity), thread_pool=pool, alignment_prepare=prepare
    )
    entries = _entries(2)
    coordinator.set_entries(entries)
    coordinator.set_profile(_profile(entries))
    return owner, pool, coordinator, calls, identity, entries


def _run_local(pool, coordinator):
    wait_until(lambda: len(pool.calls) == 1)
    pool.calls[0].run()
    wait_until(lambda: not coordinator.pending)


def test_coordinator_waits_for_scheduler_and_progress_does_not_rebuild_translation_table(monkeypatch):
    owner, pool, coordinator, calls, _identity, _items = _setup(monkeypatch)
    _run_local(pool, coordinator)
    assert calls == []
    changes, statuses = [], []
    coordinator.changed.connect(lambda: changes.append(dict(coordinator.projection)))
    coordinator.alignment_status_changed.connect(lambda: statuses.append(coordinator.alignment_message))
    previous = coordinator.projection
    assert coordinator.start_alignment()
    assert coordinator.projection is previous
    assert changes == []
    with pytest.raises(RuntimeError, match="预览尚未完成"):
        coordinator.capture_projector()
    wait_until(lambda: len(pool.calls) == 2)
    pool.calls[1].run()
    wait_until(lambda: not coordinator.pending)
    assert len(changes) == 1
    assert statuses and any("1/2" in status for status in statuses)
    assert {result.translation for result in coordinator.projection.values()} == {"AI result"}
    assert calls[1:3] == ["prepare", "open"] and calls[-1] == "close"
    coordinator.capture_projector()
    owner.close()


@pytest.mark.parametrize("before_run", [True, False])
def test_cancel_keeps_complete_preview_and_rejects_queued_results(monkeypatch, before_run):
    owner, pool, coordinator, calls, _identity, _items = _setup(monkeypatch)
    _run_local(pool, coordinator)
    previous = coordinator.projection
    coordinator.start_alignment()
    wait_until(lambda: len(pool.calls) == 2)
    if not before_run:
        pool.calls[1].run()
    coordinator.cancel_alignment()
    if before_run:
        pool.calls[1].run()
    _APP.processEvents()
    assert coordinator.projection is previous
    assert not coordinator.pending and not coordinator.alignment_running
    assert "已取消" in coordinator.alignment_message
    if before_run:
        assert calls == []
    owner.close()


@pytest.mark.parametrize("change", ["profile", "entries", "identity", "close"])
def test_obsolete_ai_result_cannot_publish(monkeypatch, change):
    owner, pool, coordinator, _calls, identity, entries = _setup(monkeypatch)
    _run_local(pool, coordinator)
    coordinator.start_alignment()
    wait_until(lambda: len(pool.calls) == 2)
    pool.calls[1].run()
    if change == "profile":
        coordinator.set_profile(_profile(entries, "Other"))
    elif change == "entries":
        coordinator.set_entries([replace(entries[0], translation="Edited")])
    elif change == "identity":
        identity[-1] += 1
    else:
        coordinator.close()
    _APP.processEvents()
    assert all(result.translation != "AI result" for result in coordinator.projection.values())
    owner.close()


def test_preparation_failure_retains_preview_and_allows_retry(monkeypatch):
    owner, pool, coordinator, _calls, _identity, _items = _setup(monkeypatch, prepare_error="请配置 AI 服务")
    _run_local(pool, coordinator)
    previous = coordinator.projection
    coordinator.start_alignment()
    wait_until(lambda: len(pool.calls) == 2)
    pool.calls[1].run()
    wait_until(lambda: not coordinator.pending)
    assert coordinator.projection is previous
    assert "请配置 AI 服务" in coordinator.alignment_message
    assert coordinator.start_alignment()
    coordinator.cancel_alignment()
    owner.close()


class _Consent(QObject):
    changed = pyqtSignal()

    def __init__(self, enabled=False, decided=True):
        super().__init__()
        self.enabled, self.decided = enabled, decided


def test_scheme_bar_starts_automatically_after_consent_and_cancel_does_not_restart(monkeypatch):
    owner, pool, coordinator, calls, _identity, entries = _setup(monkeypatch)
    _run_local(pool, coordinator)
    bar = TerminologyProfileBar(owner)
    consent = _Consent()
    binding = AlignmentBarBinding(bar, coordinator, consent=consent, delay_ms=0)
    binding.set_state(TerminologyProfileBarState(enabled=True, selected_revision=_profile(entries)))
    assert bar.alignment_button.isHidden()
    assert calls == []
    consent.enabled = True
    consent.changed.emit()
    wait_until(lambda: coordinator.alignment_running)
    assert bar.alignment_button.text() == "取消定位"
    bar.alignment_button.click()
    assert not coordinator.alignment_running
    assert "已取消" in bar.detail.toolTip()
    binding.render()
    _APP.processEvents()
    assert not coordinator.alignment_running
    assert not binding._timer.isActive()
    owner.close()


def test_refused_consent_does_not_start_model_work(monkeypatch):
    owner, pool, coordinator, calls, _identity, entries = _setup(monkeypatch)
    _run_local(pool, coordinator)
    bar = TerminologyProfileBar(owner)
    consent = _Consent(enabled=False, decided=True)
    binding = AlignmentBarBinding(bar, coordinator, consent=consent, delay_ms=0)
    binding.set_state(TerminologyProfileBarState(enabled=True, selected_revision=_profile(entries)))
    _APP.processEvents()
    assert calls == [] and not coordinator.alignment_running
    assert not binding._timer.isActive()
    owner.close()


def test_automatic_attempt_is_reused_after_scheme_switch_but_new_context_runs(monkeypatch):
    owner, pool, coordinator, _calls, identity, entries = _setup(monkeypatch)
    _run_local(pool, coordinator)
    bar = TerminologyProfileBar(owner)
    binding = AlignmentBarBinding(
        bar, coordinator, consent=_Consent(enabled=True), context_identity=lambda: tuple(identity), delay_ms=0
    )
    first = TerminologyProfileBarState(enabled=True, selected_revision=_profile(entries))
    binding.set_state(first)
    wait_until(lambda: coordinator.alignment_running)
    wait_until(lambda: len(pool.calls) == 2)
    pool.calls[-1].run()
    wait_until(lambda: not coordinator.pending)
    other = replace(first, selected_revision=_profile(entries, "Other"))
    coordinator.set_profile(other.selected_revision)
    binding.set_state(other)
    wait_until(lambda: len(pool.calls) == 3)
    pool.calls[-1].run()
    wait_until(lambda: coordinator.alignment_running)
    coordinator.cancel_alignment()
    coordinator.set_profile(first.selected_revision)
    binding.set_state(first)
    before = len(pool.calls)
    wait_until(lambda: len(pool.calls) == before + 1)
    pool.calls[-1].run()
    wait_until(lambda: not coordinator.pending)
    binding.render()
    _APP.processEvents()
    assert not binding._timer.isActive() and not coordinator.alignment_running
    identity[-1] += 1
    coordinator.set_entries(entries)
    before = len(pool.calls)
    wait_until(lambda: len(pool.calls) == before + 1)
    pool.calls[-1].run()
    wait_until(lambda: coordinator.alignment_running)
    coordinator.cancel_alignment()
    owner.close()


def test_rapid_selections_only_schedule_last_profile_and_destroy_cancels_timer(monkeypatch):
    from PyQt6 import sip

    owner, pool, coordinator, _calls, _identity, entries = _setup(monkeypatch)
    _run_local(pool, coordinator)
    bar = TerminologyProfileBar(owner)
    binding = AlignmentBarBinding(bar, coordinator, consent=_Consent(enabled=True), delay_ms=0)
    starts = []
    monkeypatch.setattr(coordinator, "start_alignment", lambda **kw: starts.append(binding._state) or True)
    first = TerminologyProfileBarState(enabled=True, selected_revision=_profile(entries))
    last = replace(first, selected_revision=_profile(entries, "Other"))
    binding.set_state(first)
    binding.set_state(last)
    wait_until(lambda: bool(starts))
    assert starts == [last]
    binding.set_state(first)
    assert binding._timer.isActive()
    sip.delete(bar)
    _APP.processEvents()
    assert starts == [last]
    owner.close()


def test_model_preparation_and_alignment_run_outside_ui_thread(monkeypatch):
    from threading import get_ident

    pool = QThreadPool()
    owner, _pool, coordinator, calls, _identity, _items = _setup(monkeypatch, pool=pool)
    wait_until(lambda: not coordinator.pending)
    ui_thread = get_ident()
    original = coordinator._alignment_prepare
    threads = []

    def prepare():
        threads.append(get_ident())
        return original()

    coordinator._alignment_prepare = prepare
    coordinator.start_alignment()
    wait_until(lambda: not coordinator.pending)
    assert threads and ui_thread not in threads
    assert calls[-1] == "close"
    owner.close()
    assert pool.waitForDone(1000)


def test_no_ambiguous_candidates_does_not_require_ai_configuration():
    owner, pool = QWidget(), _Pool()
    entries = _entries()

    def unexpected_prepare():
        raise AssertionError("Local-only rows must not read model configuration")

    coordinator = TerminologyPreviewCoordinator(
        owner, identity=lambda: "scope", thread_pool=pool, alignment_prepare=unexpected_prepare
    )
    coordinator.set_entries(entries)
    coordinator.set_profile(_profile(entries))
    _run_local(pool, coordinator)
    coordinator.start_alignment()
    wait_until(lambda: len(pool.calls) == 2)
    pool.calls[1].run()
    wait_until(lambda: not coordinator.pending)
    assert "定位完成" in coordinator.alignment_message
    assert next(iter(coordinator.projection.values())).translation == "New"
    owner.close()


def test_full_scope_includes_other_sources_and_defers_storage_reads(monkeypatch):
    from threading import get_ident

    pool = QThreadPool()
    owner, _pool, coordinator, calls, _identity, entries = _setup(monkeypatch, pool=pool)
    wait_until(lambda: not coordinator.pending)
    other = TranslationEntry("other", "other", "Other source", "Unseen", 1, None)
    threads = []

    def rows():
        threads.append(get_ident())
        yield other, "unused label key", set()

    collection = ProjectRowsCollection.__new__(ProjectRowsCollection)
    collection.query_rows = rows
    ctx = SimpleNamespace(
        slots={
            "visible": SimpleNamespace(collection=entries),
            "other": SimpleNamespace(collection=collection),
        }
    )
    resident, deferred = freeze_alignment_scope(ctx)
    assert threads == []
    ui_thread = get_ident()
    coordinator.start_alignment(entries=resident, deferred=deferred)
    wait_until(lambda: not coordinator.pending)
    captured = next(call[0] for call in calls if isinstance(call, tuple))
    assert len(captured) == 3
    assert {item.original for item in captured} == {"Dragon", "Other source"}
    assert threads and ui_thread not in threads
    assert len(coordinator.projection) == 3
    owner.close()
    assert pool.waitForDone(1000)


def test_viewport_scroll_does_not_cancel_full_scope_alignment(monkeypatch):
    owner, pool, coordinator, _calls, _identity, entries = _setup(monkeypatch)
    _run_local(pool, coordinator)
    coordinator.start_alignment(entries=entries)
    wait_until(lambda: len(pool.calls) == 2)
    coordinator.set_entries(entries[1:], viewport_only=True)
    assert coordinator.alignment_running
    pool.calls[1].run()
    wait_until(lambda: not coordinator.pending)
    assert len(coordinator.projection) == len(entries)
    owner.close()


@pytest.mark.parametrize("viewport_only", [True, False])
def test_source_switch_keeps_full_alignment_but_revision_change_invalidates(monkeypatch, viewport_only):
    owner, pool, coordinator, _calls, identity, entries = _setup(monkeypatch)
    _run_local(pool, coordinator)
    source = ["source-a"]
    coordinator._identity = lambda: (source[0], tuple(identity))
    coordinator._alignment_identity = lambda: tuple(identity)
    coordinator.start_alignment(entries=entries)
    wait_until(lambda: len(pool.calls) == 2)
    source[0] = "source-b"
    coordinator.set_entries(entries[1:], viewport_only=viewport_only)
    assert coordinator.alignment_running
    pool.calls[-1].run()
    wait_until(lambda: not coordinator.pending)
    assert len(coordinator.projection) == len(entries)
    coordinator.capture_projector()
    coordinator.start_alignment(entries=entries)
    wait_until(lambda: len(pool.calls) == 3)
    identity[-1] += 1
    coordinator.set_entries(entries[1:], viewport_only=viewport_only)
    assert not coordinator.alignment_running
    pool.calls[2].run()
    _APP.processEvents()
    assert not coordinator.projection
    owner.close()
