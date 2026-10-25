from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import get_ident
from types import SimpleNamespace

import pytest

from tests.application.projects.test_gui_facade_ai_snapshots import _facade
from tests.application.projects.test_lifecycle import _active, _context, _harness, _project, _refs
from transbridge.application.projects import ActiveProject
from transbridge.application.projects.gui_facade import GuiProjectCommandFacade
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.ui.version_persistence import VersionPersistence


def frozen_save():
    active = _active(*_refs("project", "variant"), revision=1, persisted_revision=0)
    harness = _harness(active, {})
    gui = get_ident()

    class GuiContext:
        active_version_identity = ("project", "variant")
        project_revision = 0
        variant_revision = 1
        uses_authoritative_projection = True
        project_commands = GuiProjectCommandFacade(harness.service, None)
        runtime_context = _context()

        def __getattribute__(self, name):
            assert get_ident() == gui, f"background read of live UI context: {name}"
            return super().__getattribute__(name)

    persistence = VersionPersistence(GuiContext(), ("project", "variant"))
    operation = persistence.freeze_save((), "after")
    assert not harness.store.calls
    return harness, persistence, operation


def execute_background(operation):
    with ThreadPoolExecutor(max_workers=1) as worker:
        return worker.submit(operation).result(timeout=5)


@pytest.mark.parametrize("change", ["project", "variant", "project_revision", "variant_revision", "clean_project"])
def test_frozen_save_rejects_changed_active_identity_or_revision_before_any_io(change):
    harness, persistence, operation = frozen_save()
    previous = harness.service.active
    if change in {"project", "clean_project"}:
        current = _active(
            *_refs("other-project", "other-variant"),
            revision=2,
            persisted_revision=2 if change == "clean_project" else 0,
        )
    elif change == "variant":
        current = _active(*_refs("project", "other-variant"), revision=2, persisted_revision=0)
    elif change == "project_revision":
        current = replace(
            previous,
            project=_project(previous.project_ref, (previous.formal_variant_ref,), revision=1),
        )
    else:
        current = _active(*_refs("project", "variant"), revision=2, persisted_revision=0)
    # Reproduce another lifecycle operation winning between GUI freeze and worker dispatch.
    with harness.service._lock:
        harness.service._active = current

    result = execute_background(operation)

    assert not result.is_success
    assert result.diagnostics[0].code == "ACTIVE_SAVE_TARGET_CHANGED"
    assert harness.store.calls == []
    assert harness.service.active is current
    assert not persistence.project_saved and not persistence.snapshot_saved


def test_frozen_save_of_same_committed_revision_saves_then_snapshots_off_gui():
    harness, persistence, operation = frozen_save()

    result = execute_background(operation)

    assert result.is_success
    assert harness.store.saved_revisions == [1]
    assert len(harness.store.snapshots) == 1
    assert harness.store.snapshots[0].variant.revision == 1
    assert not harness.service.active.dirty
    assert persistence.project_saved and persistence.snapshot_saved


def test_frozen_save_uses_revision_returned_by_its_own_translation_commit():
    _, fixture, aggregate, key, runtime_context, _ = _facade()
    active = ActiveProject(fixture.active.project, aggregate, aggregate.ref, 0, 0)
    harness = _harness(active, {})
    context = SimpleNamespace(
        active_version_identity=("project", "variant"),
        project_revision=0,
        variant_revision=0,
        uses_authoritative_projection=True,
        project_commands=GuiProjectCommandFacade(harness.service, None),
        runtime_context=runtime_context,
    )
    persistence = VersionPersistence(context, context.active_version_identity)
    entry = TranslationEntry("entry", "entry", "Source", "已翻译", 1, "NPC_:FULL", entry_key=key)
    operation = persistence.freeze_save((entry,), "after")
    assert aggregate.revision == 1

    result = execute_background(operation)

    assert result.is_success and harness.store.saved_revisions == [1]
    assert harness.store.snapshots[0].variant.entries[0].translation == "已翻译"
    assert persistence.project_saved and persistence.snapshot_saved


def test_frozen_save_guard_is_rechecked_after_waiting_for_lifecycle_lock():
    from threading import Event

    harness, persistence, operation = frozen_save()
    entered = Event()
    current = _active(*_refs("other-project", "other-variant"), revision=2, persisted_revision=0)

    def delayed():
        entered.set()
        return operation()

    with ThreadPoolExecutor(max_workers=1) as worker:
        with harness.service._lock:
            future = worker.submit(delayed)
            assert entered.wait(5)
            assert not future.done()
            harness.service._active = current
        result = future.result(timeout=5)

    assert not result.is_success and result.diagnostics[0].code == "ACTIVE_SAVE_TARGET_CHANGED"
    assert not harness.store.calls and harness.service.active is current
    assert not persistence.project_saved
