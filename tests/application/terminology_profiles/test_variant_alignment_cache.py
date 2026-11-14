from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event

import pytest

from tests.persistence.project_store.test_alignment_cache import _write, cached_project as _cached_project
from transbridge.application.terminology_profiles.alignment_cache import VariantAlignmentCache
from transbridge.application.terminology_profiles.occurrence_alignment import (
    AlignmentCandidate,
    AlignmentDecision,
    AlignmentRequest,
    TermOccurrenceCount,
)
from transbridge.persistence.v2.translation_epochs import TranslationEpochTracker

cached_project = _cached_project


def _requests(inputs, target="诺尔"):
    return tuple(
        AlignmentRequest(
            key,
            original,
            translation,
            "plugin",
            (
                AlignmentCandidate(0, 2, "诺德", target, "nord", "Nord"),
                AlignmentCandidate(3, 5, "诺德", target, "nord", "Nord"),
            ),
            (TermOccurrenceCount("nord", "Nord", 1, 2, 0),),
        )
        for key, original, translation in inputs
    )


class _Model:
    def __init__(self, *, certain=True):
        self.calls = []
        self.certain = certain

    def resolve_many(self, requests, **_options):
        self.calls.append(tuple(requests))
        return tuple(AlignmentDecision((0,) if self.certain else (), self.certain, "定位依据") for _r in requests)


def test_reopen_reuses_positions_with_new_target_but_other_version_is_empty(cached_project):
    _store, backend, _entries, inputs = cached_project
    model = _Model()
    a = VariantAlignmentCache(backend, "a")
    try:
        assert all(a.decide(_requests(inputs), model))
    finally:
        a.close()
    reopened, b = VariantAlignmentCache(backend, "a"), VariantAlignmentCache(backend, "b")
    try:
        assert all(reopened.decide(_requests(inputs, "北地人")))
        assert b.decide(_requests(inputs)) == (None,) * 3
        assert len(model.calls) == 1
    finally:
        reopened.close()
        b.close()


def test_accepted_edit_and_revert_discards_only_that_entry_across_targets(cached_project):
    _store, backend, entries, inputs = cached_project
    cache, tracker, model = VariantAlignmentCache(backend, "a"), TranslationEpochTracker(), _Model()
    cache.bind_tracker(tracker)
    requests = _requests(inputs)
    try:
        cache.decide(requests, model)
        tracker.invalidate((entries[0].snapshot.entry_key,))
        tracker.invalidate((entries[0].snapshot.entry_key,))
        result = cache.decide(_requests(inputs, "另一译名"))
        assert result[0] is None and all(result[1:])
        cache.decide(requests, model)
        assert len(model.calls) == 2 and len(model.calls[-1]) == 1
    finally:
        cache.close()


@pytest.mark.parametrize("change", ["translation", "clear", "external_aba"])
def test_changed_fence_prevents_old_result_from_being_persisted(cached_project, change):
    store, backend, entries, inputs = cached_project
    cache = VariantAlignmentCache(backend, "a")
    entered, release = Event(), Event()

    class SlowModel(_Model):
        def resolve_many(self, requests, **options):
            entered.set()
            assert release.wait(5)
            return super().resolve_many(requests, **options)

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(cache.decide, _requests(inputs[:1]), SlowModel())
            assert entered.wait(5)
            if change == "clear":
                cache.clear_all()
            elif change == "translation":
                cache.invalidate((inputs[0][0],))
            else:
                _write(store, "a", entries[0].snapshot.entry_key, translation="改过")
                _write(store, "a", entries[0].snapshot.entry_key, translation=inputs[0][2])
            cache.flush()
            release.set()
            assert result.result(5) == (None,)
        assert not any(backend.capture("a", inputs).records.values())
        assert cache.decide(_requests(inputs[:1])) == (None,)
    finally:
        release.set()
        cache.close()


def test_uncertain_is_session_only_and_explicit_retry_can_replace_it(cached_project):
    _store, backend, _entries, inputs = cached_project
    cache, model = VariantAlignmentCache(backend, "a"), _Model(certain=False)
    requests = _requests(inputs[:1])
    try:
        cache.decide(requests, model)
        cache.decide(requests, model)
        assert len(model.calls) == 1
        assert not any(backend.capture("a", inputs).records.values())
        model.certain = True
        assert cache.decide(requests, model, retry_uncertain=True)[0].certain
        assert len(model.calls) == 2
    finally:
        cache.close()


def test_unrelated_input_changes_do_not_reuse_a_position(cached_project):
    _store, backend, _entries, inputs = cached_project
    cache = VariantAlignmentCache(backend, "a")
    request = _requests(inputs[:1])[0]
    try:
        cache.decide((request,), _Model())
        changed = replace(request, plugin_id="different")
        assert cache.decide((changed,)) == (None,)
        changed = replace(request, candidates=request.candidates[:1])
        assert cache.decide((changed,)) == (None,)
    finally:
        cache.close()


def test_simultaneous_same_input_uses_one_model_request(cached_project):
    _store, backend, _entries, inputs = cached_project
    cache, model = VariantAlignmentCache(backend, "a"), _Model()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(cache.decide, _requests(inputs), model) for _i in range(2)]
            assert all(all(future.result(5)) for future in futures)
        assert len(model.calls) == 1
    finally:
        cache.close()


def test_model_result_repairs_corrupt_record_for_next_launch(cached_project):
    from transbridge.application.terminology_profiles.alignment_cache_codec import location_key

    _store, backend, _entries, inputs = cached_project
    requests = _requests(inputs[:1])
    snapshot = backend.capture("a", inputs[:1])
    backend.publish(snapshot, ((requests[0].entry_key, location_key(requests[0]), "broken JSON"),))
    cache = VariantAlignmentCache(backend, "a")
    try:
        assert cache.decide(requests, _Model())[0].certain
    finally:
        cache.close()
    reopened = VariantAlignmentCache(backend, "a")
    try:
        assert reopened.decide(requests)[0].certain
    finally:
        reopened.close()
