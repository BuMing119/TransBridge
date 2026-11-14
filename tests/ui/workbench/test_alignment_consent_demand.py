from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

from PyQt6.QtWidgets import QMessageBox, QWidget
import pytest

from tests.application.terminology_profiles.test_profile_alignment import Service, scheme
from tests.persistence.project_store.test_alignment_cache import cached_project as _cached_project
from tests.ui.workbench.terminology_async_support import wait_until
from tests.ui.workbench.test_alignment_cache_preview import _complete, _entries, _seed
from tests.ui.workbench.test_alignment_consent import _Settings
from tests.ui.workbench.test_terminology_preview_async import _Pool
from transbridge.application.terminology_profiles.alignment_cache import VariantAlignmentCache
from transbridge.ui.workbench.alignment_consent import AlignmentConsent
from transbridge.ui.workbench.terminology_alignment import AlignmentBarBinding
from transbridge.ui.workbench.terminology_preview import TerminologyPreviewCoordinator
from transbridge.ui.workbench.terminology_profile_bar import TerminologyProfileBar, TerminologyProfileBarState

cached_project = _cached_project


@contextmanager
def _workbench(data, *, seed=False, rules_only=False):
    _store, backend, stored, inputs = data
    if seed:
        _seed(backend, inputs)
    cache, entries = VariantAlignmentCache(backend, "a"), _entries(stored)
    if rules_only:
        entries = tuple(replace(entry, translation="诺德") for entry in entries)
    owner, pool, consent = QWidget(), _Pool(), AlignmentConsent(settings=_Settings())
    calls = []

    @contextmanager
    def open_service():
        yield Service()

    def prepare():
        calls.append("prepare")
        return SimpleNamespace(open=open_service)

    coordinator = TerminologyPreviewCoordinator(
        owner, identity=lambda: "a", thread_pool=pool, alignment_prepare=prepare
    )
    coordinator.set_cache_provider(lambda: cache)
    bar = TerminologyProfileBar(owner)
    binding = AlignmentBarBinding(bar, coordinator, scope=lambda: (entries, ()), consent=consent, delay_ms=0)
    source, target = scheme("A", "诺德"), scheme("B", "诺尔")
    owner.show()
    coordinator.set_entries(entries)
    coordinator.set_profile(target, source_profile=source)
    binding.set_state(TerminologyProfileBarState(enabled=True, selected_revision=target, applied_revision=source))
    try:
        yield owner, pool, coordinator, consent, calls, binding
    finally:
        coordinator.close()
        if consent._dialog is not None:
            consent._dialog.reject()
        owner.close()
        cache.close()


@pytest.mark.parametrize("seed,rules_only", [(True, False), (False, True)])
def test_rules_or_complete_cache_do_not_ask(cached_project, seed, rules_only):
    with _workbench(cached_project, seed=seed, rules_only=rules_only) as (
        _owner,
        pool,
        preview,
        consent,
        calls,
        _binding,
    ):
        assert consent._dialog is None
        _complete(pool, preview, 1)
        wait_until(lambda: preview.alignment_running)
        _complete(pool, preview, 2)
        assert consent._dialog is None and not consent.decided
        assert calls == []
        assert all(not row.diagnostics for row in preview.projection.values())


def test_first_real_miss_asks_before_preparation_then_accept_resumes_automatically(cached_project):
    with _workbench(cached_project) as (_owner, pool, preview, consent, calls, _binding):
        _complete(pool, preview, 1)
        assert consent._dialog is None and calls == []
        wait_until(lambda: preview.alignment_running)
        _complete(pool, preview, 2)
        wait_until(lambda: consent._dialog is not None)
        assert calls == [] and not preview.pending
        consent._dialog.button(QMessageBox.StandardButton.Yes).click()
        wait_until(lambda: preview.alignment_running)
        _complete(pool, preview, 3)
        assert calls == ["prepare"]
        assert all(not row.diagnostics for row in preview.projection.values())


@pytest.mark.parametrize("answer", ["no", "close"])
def test_refusal_or_close_suppresses_later_misses_for_this_launch(cached_project, answer):
    with _workbench(cached_project) as (_owner, pool, preview, consent, calls, binding):
        _complete(pool, preview, 1)
        wait_until(lambda: preview.alignment_running)
        _complete(pool, preview, 2)
        wait_until(lambda: consent._dialog is not None)
        if answer == "no":
            consent._dialog.button(QMessageBox.StandardButton.No).click()
        else:
            consent._dialog.reject()
        wait_until(lambda: preview.alignment_running)
        _complete(pool, preview, 3)
        source, target = scheme("A", "诺德"), scheme("C", "北地人")
        preview.set_profile(target, source_profile=source)
        binding.set_state(TerminologyProfileBarState(enabled=True, selected_revision=target, applied_revision=source))
        _complete(pool, preview, 4)
        wait_until(lambda: preview.alignment_running)
        _complete(pool, preview, 5)
        assert consent.decided and not consent.enabled and consent._dialog is None
        assert calls == []


def test_obsolete_cache_miss_cannot_open_dialog(cached_project):
    with _workbench(cached_project) as (_owner, pool, preview, consent, calls, binding):
        _complete(pool, preview, 1)
        wait_until(lambda: len(pool.calls) == 2)
        pool.calls[-1].run()
        source = scheme("A", "诺德")
        preview.set_profile(source, source_profile=source)
        binding.set_state(TerminologyProfileBarState(enabled=True, selected_revision=source, applied_revision=source))
        from PyQt6.QtWidgets import QApplication

        QApplication.processEvents()
        assert consent._dialog is None and not consent.decided and calls == []
