from __future__ import annotations

import os
from threading import Thread
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.projections import ProjectionSnapshot, ProjectionStore
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui import context as context_module
from transbridge.ui.project_view_binding import ProjectViewState, prepare_project_view
from transbridge.ui.projection_types import CollectionSlot

_APP = QApplication.instance() or QApplication([])


def _snapshot(project="old", revision=1, *, labels=()):
    return ProjectionSnapshot(
        f"project:{project}",
        revision,
        revision,
        {
            "project_id": project,
            "project_name": project,
            "variant_id": f"{project}-variant",
            "project_revision": revision,
            "variant_revision": revision,
            "sources": [{"location": f"{project}.esp"}],
            "variants": [{"id": f"{project}-variant", "name": "Default"}],
            "entries": [{"entry_key": {"namespace": project, "local_key": "key"}, "labels": list(labels)}],
            "label_library": {label: {"name": label, "color": "#fff"} for label in labels},
        },
    )


def _slot(project):
    entry = TranslationEntry(
        "entry", "key", "Original", "Translated", 1, None, entry_key=EntryKey(SourceNamespace(project), "key")
    )
    return CollectionSlot(project, TranslationEntryCollection([entry]))


@pytest.fixture
def binding(monkeypatch):
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: SimpleNamespace(token=""))
    store = ProjectionStore(_snapshot())
    context = context_module.AppContext(project_projection=store)
    old_slot = _slot("old")
    context.add_slot("old.esp", old_slot)
    yield context, store, old_slot
    context.close_projection()
    _APP.processEvents()


def _publish_in_worker(store, snapshot):
    worker = Thread(target=lambda: store.rebuild(snapshot))
    worker.start()
    worker.join(timeout=2)
    assert not worker.is_alive()


def test_install_publishes_only_complete_context_and_one_active_collection(binding):
    context, store, old_slot = binding
    observations = []
    for name in (
        "label_data_changed",
        "project_changed",
        "variant_changed",
        "collection_list_changed",
        "project_installed",
        "collection_changed",
    ):
        getattr(context, name).connect(
            lambda *args, name=name: observations.append((
                name,
                context.project_name,
                context.active_key,
                context.entry_labels,
                context.project_installing,
            ))
        )
    token = context.begin_project_open()
    updated = _snapshot("new", labels=("review",))
    _publish_in_worker(store, updated)
    _APP.processEvents()
    assert context.project_name == "old"
    assert context.active_slot is old_slot
    assert observations == []

    first, final = _slot("first"), _slot("new")
    assert context.install_project_open(token, updated, (("first.esp", first), ("new.esp", final)))

    assert all(item[1:4] == ("new", "new.esp", {"entry": {"review"}}) for item in observations)
    assert [item[0] for item in observations].count("collection_changed") == 1
    assert all(item[4] for item in observations[:-2])
    assert [item[0] for item in observations[-2:]] == ["project_installed", "collection_changed"]
    assert all(not item[4] for item in observations[-2:])
    assert context.active_slot is final
    assert context.project_opening
    assert not context.install_project_open(token, updated, ())
    assert context.finish_project_open(token)
    assert not context.project_opening


def test_failed_open_keeps_old_slots_and_releases_delayed_normal_projection(binding):
    context, store, old_slot = binding
    token = context.begin_project_open()
    updated = _snapshot(revision=2, labels=("review",))
    _publish_in_worker(store, updated)
    assert context.project_revision == 1

    assert context.finish_project_open(token)
    assert context.project_revision == 2
    assert context.active_slot is old_slot
    assert context.entry_labels == {"entry": {"review"}}
    assert not context.project_opening


def test_outdated_queued_notification_cannot_restore_previous_project(binding):
    context, store, _ = binding
    _publish_in_worker(store, _snapshot(revision=2))  # Queued before the opening transaction.
    token = context.begin_project_open()
    updated = _snapshot("new")
    _publish_in_worker(store, updated)

    assert context.install_project_open(token, updated, (("new.esp", _slot("new")),))
    assert context.finish_project_open(token)
    _APP.processEvents()

    assert context.project_name == "new"
    assert context.active_key == "new.esp"


def test_stale_token_and_stale_snapshot_do_not_replace_visible_slots(binding):
    context, store, old_slot = binding
    expired = context.begin_project_open()
    assert context.finish_project_open(expired)
    token = context.begin_project_open()
    updated = _snapshot("new", revision=2)
    _publish_in_worker(store, updated)

    assert not context.install_project_open(expired, updated, (("new.esp", _slot("new")),))
    assert not context.finish_project_open(expired)
    assert not context.install_project_open(token, _snapshot("new"), (("new.esp", _slot("new")),))
    assert context.active_slot is old_slot
    assert context.project_name == "old"
    assert context.install_project_open(token, updated, (("new.esp", _slot("new")),))
    assert context.finish_project_open(token)


def test_payload_validation_fails_before_any_visible_state_changes(binding):
    context, _, old_slot = binding
    token = context.begin_project_open()
    with pytest.raises(ValueError, match="unique"):
        context.install_project_open(token, _snapshot("new"), (("a", _slot("new")), ("a", _slot("new"))))
    invalid = ProjectionSnapshot("project:new", 1, 1, {"paratranz_binding": {"project_id": "invalid"}})
    with pytest.raises(ValueError):
        context.install_project_open(token, invalid, (("new.esp", _slot("new")),))
    assert context.project_name == "old"
    assert context.active_slot is old_slot
    assert context.finish_project_open(token)


def test_new_notifications_after_install_are_not_dropped(binding):
    context, store, _ = binding
    token = context.begin_project_open()
    first = _snapshot("new")
    _publish_in_worker(store, first)
    assert context.install_project_open(token, first, (("new.esp", _slot("new")),))
    _publish_in_worker(store, _snapshot("new", revision=2, labels=("later",)))
    assert context.project_revision == 1
    assert context.finish_project_open(token)
    assert context.project_revision == 2
    assert context.entry_labels == {"entry": {"later"}}
    assert context.project_view_failed


def test_projection_published_during_install_notification_cannot_enable_outdated_slots(binding):
    context, store, _ = binding
    token = context.begin_project_open()
    snapshot = _snapshot("new")
    updated = _snapshot("new", revision=2, labels=("later",))
    context.collection_changed.connect(lambda _: _publish_in_worker(store, updated))
    assert context.install_project_open(token, snapshot, (("new.esp", _slot("new")),))
    assert context.finish_project_open(token)
    assert context.project_revision == 2
    assert context.project_view_failed


def test_closed_binding_ignores_queued_notifications_and_late_install(binding):
    context, store, _ = binding
    _publish_in_worker(store, _snapshot(revision=2))
    token = context.begin_project_open()
    context.close_projection()
    _APP.processEvents()
    assert context.project_revision == 1
    assert not context.install_project_open(token, _snapshot("new"), ())
    assert not context.finish_project_open(token)
    assert store.listener_count == 0


def test_installation_rejects_background_thread_calls(binding):
    context, _, _ = binding
    failures = []

    def attempt():
        try:
            context.begin_project_open()
        except RuntimeError as exc:
            failures.append(str(exc))

    worker = Thread(target=attempt)
    worker.start()
    worker.join(timeout=2)
    assert failures == ["Project view installation must run on the GUI thread"]
    assert not context.project_opening


def test_prepared_view_avoids_gui_recapture_and_duplicate_finished_notification(binding, monkeypatch):
    context, store, _ = binding
    token = context.begin_project_open()
    snapshot = _snapshot("new", labels=("review",))
    prepared = []
    worker = Thread(target=lambda: prepared.append(prepare_project_view(snapshot)))
    worker.start()
    worker.join(timeout=2)
    assert not worker.is_alive()
    _publish_in_worker(store, snapshot)
    monkeypatch.setattr(ProjectViewState, "capture", lambda *_: pytest.fail("Recaptured labels on the GUI thread"))

    assert context.install_project_open(token, snapshot, (("new.esp", _slot("new")),), state=prepared[0])
    _publish_in_worker(store, snapshot)
    assert context.finish_project_open(token)
    assert context.entry_labels == {"entry": {"review"}}


def test_prepared_view_must_match_the_handed_off_snapshot(binding):
    context, _, old_slot = binding
    token = context.begin_project_open()
    snapshot = _snapshot("new")
    state = prepare_project_view(_snapshot("other"))
    with pytest.raises(ValueError, match="exact installation snapshot"):
        context.install_project_open(token, snapshot, (("new.esp", _slot("new")),), state=state)
    assert context.active_slot is old_slot
    assert context.finish_project_open(token)


def test_prepared_state_retargets_acknowledgement_without_recapturing_entries(monkeypatch):
    candidate = _snapshot("new", labels=("review",))
    state = prepare_project_view(candidate)
    acknowledged = _snapshot("new", labels=("review",))
    monkeypatch.setattr(ProjectViewState, "capture", lambda *_: pytest.fail("Recaptured prepared entries"))
    installed = state.committed(acknowledged)
    assert installed.snapshot is acknowledged
    assert installed.exact_labels is state.exact_labels
    with pytest.raises(ValueError, match="committed project changed"):
        state.committed(_snapshot("new", revision=2))


def test_opening_and_failed_view_reject_edits_as_results_before_commands(binding):
    context, _, _ = binding
    context._project_commands = SimpleNamespace(
        update_entry=lambda *_args, **_kwargs: pytest.fail("Called command during project opening"),
        replace_labels=lambda *_args, **_kwargs: pytest.fail("Called command during project opening"),
    )
    context._runtime_context = object()
    token = context.begin_project_open()
    for result in (context.update_projected_entry("key"), context.replace_projected_labels({}, {})):
        assert not result.is_success
        assert result.diagnostics[0].code == "PROJECT_OPEN_IN_PROGRESS"
    context.project_view_failed = True
    assert context.finish_project_open(token)
    assert context.update_projected_entry("key").diagnostics[0].code == "PROJECT_VIEW_UNAVAILABLE"
    retry = context.begin_project_open()
    assert context.project_view_failed
    assert context.install_project_open(retry, _snapshot("new"), ())
    assert not context.project_view_failed
    assert context.finish_project_open(retry)


def test_unavailable_command_adapter_keeps_its_existing_exception_contract(binding):
    context, _, _ = binding
    token = context.begin_project_open()
    with pytest.raises(RuntimeError, match="adapter is unavailable"):
        context.update_projected_entry("key")
    with pytest.raises(RuntimeError, match="adapter is unavailable"):
        context.replace_projected_labels({}, {})
    assert context.finish_project_open(token)
