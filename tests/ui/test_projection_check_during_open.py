"""Open transactions check installed content without waiting on their own completion."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import replace
from threading import Event
from types import SimpleNamespace

import pytest

from tests.conftest import make_entry
from tests.ui.test_content_projection_merge import _APP, _snapshot, view_factory as view_factory  # noqa: F401
from transbridge.application.io.identity import ExternalEntryRef
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.context import CollectionSlot


@pytest.fixture
def opening(view_factory):  # noqa: F811 - imported pytest fixture
    view = view_factory()
    token = view.context.begin_project_open()
    pool = ThreadPoolExecutor(max_workers=1)
    yield SimpleNamespace(view=view, context=view.context, token=token, pool=pool)
    if view.context.project_opening:
        assert view.context.finish_project_open(token)
    _APP.processEvents()
    pool.shutdown(wait=True)


def _check(case):
    return case.pool.submit(case.context.authoritative_projection_diverged).result(timeout=1)


def _next_view(case):
    entry = make_entry("new", translation="新工程译文", stage=3)
    snapshot = _snapshot((entry,), project="new-project", variant="new-variant")
    slots = (("new.esp", CollectionSlot("new", TranslationEntryCollection((entry,)))),)
    case.view.store.rebuild(snapshot)
    return snapshot, slots


def test_open_owner_can_check_the_old_view_before_its_worker_finishes(opening):
    assert _check(opening) is False
    assert opening.context.project_opening


def test_entering_open_wakes_a_check_already_waiting_for_normal_delivery(view_factory, monkeypatch):  # noqa: F811
    view = view_factory()
    original, other = view.entries
    updated = replace(original, translation="等待交付的旧工程译文", stage=5)
    token = None
    waiter_entered = Event()
    real_wait = view.context._project_view._lock.wait_for

    def wait_for(predicate):
        waiter_entered.set()
        return real_wait(predicate)

    monkeypatch.setattr(view.context._project_view._lock, "wait_for", wait_for)
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        pool.submit(view.store.rebuild, _snapshot((updated, other), revision=1)).result(timeout=1)
        future = pool.submit(view.context.authoritative_projection_diverged)
        assert waiter_entered.wait(1)
        with pytest.raises(FutureTimeoutError):
            future.result(timeout=0.05)

        token = view.context.begin_project_open()

        assert future.result(timeout=1) is False
        assert view.context.project_opening
        _APP.processEvents()
        assert view.context.finish_project_open(token)
        assert view.context.slots["0"].collection.get(original.identity).translation == updated.translation
    finally:
        if token is not None and view.context.project_opening:
            view.context.finish_project_open(token)
        _APP.processEvents()
        pool.shutdown(wait=True)


@pytest.mark.parametrize(
    "changes",
    [
        {"translation": "未提交草稿"},
        {"stage": 9},
        {"external_refs": (ExternalEntryRef("paratranz", "project:42", 999),)},
    ],
)
def test_open_owner_still_detects_genuine_unsubmitted_content(opening, changes):
    entry = opening.view.entries[0]
    local = replace(entry, **changes)
    opening.context.slots["0"].collection = TranslationEntryCollection((local,))

    assert _check(opening) is True
    assert opening.context.slots["0"].collection.get(entry.identity) is local
    assert opening.context.project_opening


def test_pending_next_project_is_not_used_as_authority_for_the_old_visible_slots(opening):
    snapshot, slots = _next_view(opening)
    old_slot = opening.context.active_slot

    assert _check(opening) is False
    assert opening.context.authoritative_projection_diverged() is False
    assert opening.context.active_slot is old_slot
    assert opening.context.install_project_open(opening.token, snapshot, slots)
    assert _check(opening) is False


def test_same_token_installation_during_a_background_comparison_forces_a_fresh_read(opening, monkeypatch):
    binding = opening.context._project_view
    real_compare = binding._diverged_snapshot
    entered, release = Event(), Event()
    reads = []

    def compare(snapshot):
        reads.append(snapshot)
        if len(reads) == 1:
            entered.set()
            assert release.wait(3)
        return real_compare(snapshot)

    monkeypatch.setattr(binding, "_diverged_snapshot", compare)
    snapshot, slots = _next_view(opening)
    future = opening.pool.submit(opening.context.authoritative_projection_diverged)
    try:
        assert entered.wait(1)
        assert opening.context.install_project_open(opening.token, snapshot, slots)
        release.set()
        assert future.result(timeout=1) is False
        assert len(reads) == 2
        assert reads[-1] is snapshot
    finally:
        release.set()


def test_background_check_does_not_observe_a_partially_installed_view(opening):
    futures = []
    checks = []

    def during_install():
        future = opening.pool.submit(opening.context.authoritative_projection_diverged)
        futures.append(future)
        with pytest.raises(FutureTimeoutError):
            future.result(timeout=0.05)
        checks.append(True)

    opening.context.project_changed.connect(during_install)
    snapshot, slots = _next_view(opening)
    assert opening.context.install_project_open(opening.token, snapshot, slots)

    assert checks == [True]
    assert futures[0].result(timeout=1) is False
    assert opening.context.project_opening


def test_finishing_a_cancelled_open_still_delivers_pending_updates_to_the_old_project(opening):
    original, other = opening.view.entries
    updated = replace(original, translation="切换期间已提交的译文", stage=5)
    snapshot = _snapshot((updated, other), revision=1)
    opening.pool.submit(opening.view.store.rebuild, snapshot).result(timeout=1)

    assert _check(opening) is False
    assert opening.context.finish_project_open(opening.token)
    assert opening.context.slots["0"].collection.get(original.identity).translation == updated.translation
    assert opening.context.authoritative_projection_diverged() is False
