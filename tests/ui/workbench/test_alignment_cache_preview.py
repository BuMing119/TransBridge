import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QWidget

from tests.application.terminology_profiles.test_profile_alignment import Service, scheme
from tests.persistence.project_store.test_alignment_cache import cached_project as _cached_project
from tests.ui.workbench.terminology_async_support import wait_until
from tests.ui.workbench.test_terminology_alignment import _Consent
from tests.ui.workbench.test_terminology_preview_async import _Pool
from transbridge.application.terminology_profiles.alignment_cache import VariantAlignmentCache
from transbridge.application.terminology_profiles.entry_projection import ProfileEntryInput
from transbridge.application.terminology_profiles.profile_alignment import align_profile_entries
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.ui.workbench.terminology_alignment import AlignmentBarBinding
from transbridge.ui.workbench.terminology_preview import TerminologyPreviewCoordinator
from transbridge.ui.workbench.terminology_profile_bar import TerminologyProfileBar, TerminologyProfileBarState

_APP = QApplication.instance() or QApplication([])
cached_project = _cached_project


def _seed(backend, inputs):
    cache = VariantAlignmentCache(backend, "a")
    try:
        align_profile_entries(
            tuple(ProfileEntryInput(key, original, translation, None) for key, original, translation in inputs),
            scheme("B", "诺尔"),
            None,
            source_profile=scheme("A", "诺德"),
            service=Service(),
            cache=cache,
        )
    finally:
        cache.close()


def _entries(stored):
    return [
        TranslationEntry(
            str(i),
            row.snapshot.entry_key.local_key,
            row.snapshot.original,
            row.state.translation,
            0,
            None,
            entry_key=row.snapshot.entry_key,
        )
        for i, row in enumerate(stored)
    ]


def _complete(pool, coordinator, number):
    wait_until(lambda: len(pool.calls) == number)
    pool.calls[-1].run()
    wait_until(lambda: not coordinator.pending)


def test_restart_reads_cache_without_ai_and_other_variant_has_no_judgments(cached_project):
    _store, backend, stored, inputs = cached_project
    _seed(backend, inputs)
    a, b = VariantAlignmentCache(backend, "a"), VariantAlignmentCache(backend, "b")
    current = [a]
    owner, pool = QWidget(), _Pool()
    coordinator = TerminologyPreviewCoordinator(owner, identity=lambda: current[0].variant_id, thread_pool=pool)
    coordinator.set_cache_provider(lambda: current[0])
    try:
        coordinator.set_entries(_entries(stored))
        coordinator.set_profile(scheme("B", "北地人"), source_profile=scheme("A", "诺德"))
        _complete(pool, coordinator, 1)
        assert {row.translation for row in coordinator.projection.values()} == {"北地人和诺德"}
        current[0] = b
        coordinator.set_entries(_entries(stored))
        _complete(pool, coordinator, 2)
        assert all(row.diagnostics for row in coordinator.projection.values())
        coordinator.capture_projector()
    finally:
        coordinator.close()
        owner.close()
        a.close()
        b.close()


def test_disabled_ai_warms_full_scope_cache_and_clear_does_not_start_model(cached_project):
    _store, backend, stored, inputs = cached_project
    _seed(backend, inputs)
    cache, entries = VariantAlignmentCache(backend, "a"), _entries(stored)
    owner, pool = QWidget(), _Pool()

    def unexpected_model():
        raise AssertionError("A cache-only preview must never prepare a model")

    coordinator = TerminologyPreviewCoordinator(
        owner, identity=lambda: "a", thread_pool=pool, alignment_prepare=unexpected_model
    )
    coordinator.set_cache_provider(lambda: cache)
    bar = TerminologyProfileBar(owner)
    binding = AlignmentBarBinding(bar, coordinator, scope=lambda: (entries, ()), consent=_Consent(), delay_ms=0)
    source, target = scheme("A", "诺德"), scheme("B", "诺尔")
    try:
        coordinator.set_entries(entries[:1])
        coordinator.set_profile(target, source_profile=source)
        binding.set_state(TerminologyProfileBarState(enabled=True, selected_revision=target, applied_revision=source))
        _complete(pool, coordinator, 1)
        wait_until(lambda: coordinator.alignment_running)
        _complete(pool, coordinator, 2)
        assert len(coordinator.projection) == 3
        assert all(not item.diagnostics for item in coordinator.projection.values())
        assert "缓存读取完成" in coordinator.alignment_message
        coordinator.clear_alignment_cache()
        _complete(pool, coordinator, 3)
        assert all(item.diagnostics for item in coordinator.projection.values())
        assert not binding._timer.isActive()
    finally:
        coordinator.close()
        owner.close()
        cache.close()
