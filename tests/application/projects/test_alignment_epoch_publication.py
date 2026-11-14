from dataclasses import replace

import pytest

from tests.application.projects.test_lifecycle import _active, _context, _harness, _refs
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.projects.models import LifecycleSave
from transbridge.application.terminology_profiles.alignment_cache import VariantAlignmentCache
from transbridge.persistence.v2.variant import SourceFingerprint, VariantAggregate, VariantEntryState, VariantSnapshot


def _setup():
    project_ref, variant_ref = _refs("project", "main")
    key = EntryKey(SourceNamespace("source"), "entry")
    snapshot = VariantSnapshot(
        variant_ref, (SourceFingerprint(key.namespace, None),), (VariantEntryState(key, translation="A"),)
    )
    active = replace(_active(project_ref, variant_ref), variant=VariantAggregate(snapshot))
    harness = _harness(active, {})

    class Backend:
        def __init__(self):
            self.cleared = []

        def clear(self, variant_id, keys):
            self.cleared.append((variant_id, tuple(keys)))

    backend = Backend()
    cache = VariantAlignmentCache(backend, "main")
    cache.bind_tracker(active.variant.alignment_epochs)
    return harness, key, backend, cache


def _publish(harness, text, kind, *, reject=False):
    active = harness.service.active
    before = active.variant.snapshot()
    candidate = replace(before, entries=(replace(before.entries[0], translation=text),), revision=before.revision + 1)

    def verify():
        if reject:
            raise ValueError("publication rejected")

    if kind == "content":
        return harness.service.commit_active_content(
            active.project,
            candidate,
            _context(),
            expected_project_revision=active.project.envelope.revision,
            expected_variant_revision=before.revision,
            before_publish=verify,
        )
    save = LifecycleSave.capture(replace(active, variant=VariantAggregate(candidate)))
    return harness.service.reconcile_variant_save(
        save, before.revision, (), _context(), verify=verify, commit_mutation=None
    )


@pytest.mark.parametrize("kind", ["content", "reconcile"])
def test_published_replacements_invalidate_bound_cache_even_without_any_ui_refresh(kind):
    harness, key, backend, cache = _setup()
    original_tracker = harness.service.active.variant.alignment_epochs
    try:
        assert _publish(harness, "B", kind).is_success
        assert _publish(harness, "A", kind).is_success
        # No new registry lookup, UI delivery or tracker bind occurred.
        assert harness.service.active.variant.alignment_epochs is original_tracker
        cache.close()
        assert backend.cleared == [("main", (key.serialize(),)), ("main", (key.serialize(),))]
    finally:
        cache.close()


@pytest.mark.parametrize("kind", ["content", "reconcile"])
def test_rejected_replacement_does_not_notify_bound_cache(kind):
    harness, _key, backend, cache = _setup()
    try:
        before = harness.service.active
        assert not _publish(harness, "B", kind, reject=True).is_success
        cache.flush()
        assert harness.service.active is before
        assert backend.cleared == []
    finally:
        cache.close()


@pytest.mark.parametrize("kind", ["content", "reconcile"])
def test_same_translation_replacement_keeps_cache(kind):
    harness, _key, backend, cache = _setup()
    try:
        assert _publish(harness, "A", kind).is_success
        cache.flush()
        assert backend.cleared == []
    finally:
        cache.close()
