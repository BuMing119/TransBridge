"""Exercise the Save button against a paged project and an isolated dictionary."""

from dataclasses import replace
from threading import Event

import pytest

from tests.ui.test_project_creation_reuse import _until, shared_plugin_window as window_fixture
from transbridge.application.contracts import DomainError, ErrorCategory, OperationResult
from transbridge.application.projects.dictionary_apply import dictionary_library
from transbridge.application.terminology_profiles.models import ProfileTermMapping, TerminologyProfileContent
from transbridge.ui.coordinators.profile_variant_save import ProfileVariantSaveCoordinator
from transbridge.ui.shell.action_catalog import IntentId
from transbridge.ui.shell.saved_dictionary import SavedDictionarySource
from transbridge.ui.worker_registry import get_api_worker_registry

shared_plugin_window = window_fixture


@pytest.fixture
def project_dictionary(shared_plugin_window, tmp_path, monkeypatch):
    monkeypatch.setattr("transbridge.translation_memory.library.default_library_dir", lambda: tmp_path / "dictionary")
    window = shared_plugin_window.window
    _until(lambda: window.workbench.preview._paging.resident_ready and not get_api_worker_registry().busy)
    return window, dictionary_library(window.context)


def test_explicit_save_updates_current_source_dictionary(project_dictionary):
    window, library = project_dictionary
    ctx = window.context
    entry = next(iter(ctx.collection))
    saved = []
    assert ctx.update_projected_entry(entry.identity, translation="初译", stage=1).is_success
    _until(lambda: window.workbench.preview._paging.resident_ready and not get_api_worker_registry().busy)
    assert window.save_current_project_async(automatic=True, on_finished=saved.append)
    _until(lambda: bool(saved))
    assert saved == [True] and list(library.lookup_rows()) == []

    for text in ("修订", "再次修订"):
        assert ctx.update_projected_entry(entry.identity, translation=text, stage=1).is_success
        window.workbench.project_bar.save_requested.emit()
        _until(lambda: list(library.lookup_rows()) == [(entry.original, text, "A")])
        _until(lambda: not window._project_save_coordinator._dictionary_writer.busy)
        assert not ctx.dirty


def test_failed_project_save_preserves_dictionary(project_dictionary, monkeypatch):
    window, library = project_dictionary
    error = DomainError(ErrorCategory.CONFLICT, "TEST_SAVE_FAILED", "保存失败")
    monkeypatch.setattr(window, "_save_current_project", lambda: OperationResult.failed(error))
    window._intent_composition.dispatch(IntentId.PROJECT_SAVE)
    _until(lambda: window.save_worker is None)
    assert list(library.lookup_rows()) == []


def test_saved_independent_scheme_publishes_default_and_applies_back_to_its_body(project_dictionary):
    window, library = project_dictionary
    ctx = window.context
    original_project, _ = ctx.active_version_identity
    source_key = ctx.active_key
    services = ctx.terminology_profile_service_factory
    profiles = services.profile_service_for(original_project)
    mapping = ProfileTermMapping("Original", "白漫城", "Project A translation", scheme_owned=True)
    draft = profiles.create_with_content(original_project, "方案 B", TerminologyProfileContent((mapping,)))
    published = profiles.publish(draft.profile_id, expected_draft_revision=0)
    assert [(row.original, row.translation) for row in ctx.collection] == [("Original", "Project A translation")]
    saved = window.project_commands.save_profile_variant(
        "独立 B", window.runtime_context, target_profile=published, source_profile=None
    )
    assert saved.is_success, saved.diagnostics
    ProfileVariantSaveCoordinator(window)._finished(saved, "独立 B", original_project, source_key)
    _until(lambda: list(library.lookup_rows()) == [("Original", "Project A translation", "A")])
    _until(lambda: not window._project_save_coordinator._dictionary_writer.busy)
    (entry,) = ctx.collection
    assert entry.translation == "白漫城"

    def edit(text, stage):
        assert ctx.update_projected_entry(entry.identity, translation=text, stage=stage).is_success
        _until(lambda: window.workbench.preview._paging.resident_ready and not get_api_worker_registry().busy)
        services.alignment_cache_for(*ctx.active_version_identity).flush()

    edit("抵达白漫城", 1)
    window.workbench.project_bar.save_requested.emit()
    _until(lambda: list(library.lookup_rows()) == [("Original", "抵达Project A translation", "A")])
    _until(lambda: not window._project_save_coordinator._dictionary_writer.busy)
    assert next(iter(ctx.collection)).translation == "抵达白漫城"
    edit("", 0)
    assert window._intent_composition.dispatch(IntentId.TRANSLATION_DICTIONARY_APPLY).accepted
    _until(lambda: not window._intent_composition._dictionary_commands.busy)
    assert next(iter(ctx.collection)).translation == "抵达白漫城"


@pytest.mark.parametrize("shared_plugin_window", ["two_entries"], indirect=True)
def test_saves_read_only_pending_rows_across_autosave_and_retry(project_dictionary, monkeypatch):
    window, library = project_dictionary
    ctx = window.context
    terminology = window._app_runtime.use_cases.resolve("terminology_profile_service_factory")
    first, second = sorted(ctx.collection, key=lambda row: row.original)
    reads = []
    read = type(ctx.collection).saved_entries

    def observe(self, project_revision, variant_revision, keys=None):
        rows = list(read(self, project_revision, variant_revision, keys))
        reads.append((keys is None, {row.identity for row in rows}))
        return rows

    monkeypatch.setattr(type(ctx.collection), "saved_entries", observe)

    def save(*, automatic=False):
        # Independent background invalidations use the same project write lease.
        terminology.alignment_cache_for(*ctx.active_version_identity).flush()
        done = []
        assert window.save_current_project_async(
            automatic=automatic, include_dictionary=not automatic, on_finished=done.append
        )
        _until(lambda: bool(done) and not window._project_save_coordinator._dictionary_writer.busy)
        assert done == [True]

    def edit(row, translation):
        assert ctx.update_projected_entry(row.identity, translation=translation, stage=1).is_success
        _until(lambda: window.workbench.preview._paging.resident_ready and not get_api_worker_registry().busy)

    def values():
        return {original: text for original, text, mod in library.lookup_rows() if mod == "A"}

    save()
    assert reads == [(True, {first.identity, second.identity})]
    save()
    assert len(reads) == 1
    edit(first, "甲")
    save(automatic=True)
    assert len(reads) == 1
    edit(second, "乙")
    save()
    assert reads[1:] == [(False, {first.identity, second.identity})]
    assert values() == {first.original: "甲", second.original: "乙"}

    # Another project saved a newer translation under the same source mod and locator.
    library.save([replace(first, translation="另一个工程", stage=1)], "A")
    edit(second, "修订")
    with monkeypatch.context() as failure:

        def unavailable(*args, **kwargs):
            raise OSError("temporary dictionary error")

        failure.setattr(type(library), "save_revision", unavailable)
        save()
    save()
    assert reads[2:] == [(False, {second.identity})]
    assert values() == {first.original: "另一个工程", second.original: "修订"}


def test_disabling_save_preference_preserves_project_and_manual_dictionary_command(project_dictionary):
    window, library = project_dictionary
    ctx = window.context
    ctx.dictionary_preferences.set_save_on_project_save(False)
    entry = next(iter(ctx.collection))
    assert ctx.update_projected_entry(entry.identity, translation="手动入库", stage=1).is_success
    _until(lambda: not get_api_worker_registry().busy)
    window.variant_coordinator.manual_save()
    _until(lambda: window.save_worker is None and not ctx.dirty)
    assert not list(library.lookup_rows())
    assert window._intent_composition.dispatch(IntentId.TRANSLATION_DICTIONARY_SAVE).accepted
    _until(lambda: list(library.lookup_rows()) == [(entry.original, "手动入库", "A")])
    _until(lambda: not window._intent_composition._dictionary_commands.busy)


def test_second_save_cannot_invalidate_first_dictionary_read(project_dictionary, monkeypatch):
    window, library = project_dictionary
    ctx = window.context
    entry = next(iter(ctx.collection))
    started, release = Event(), Event()
    publish = SavedDictionarySource.publish
    observed = []

    def slow_publish(source, receipt, batches):
        started.set()
        assert release.wait(5)
        count = publish(source, receipt, batches)
        observed.extend(library.lookup_rows())
        return count

    monkeypatch.setattr(SavedDictionarySource, "publish", slow_publish)
    try:
        assert ctx.update_projected_entry(entry.identity, translation="甲", stage=1).is_success
        window.variant_coordinator.manual_save()
        _until(started.is_set)
        assert ctx.update_projected_entry(entry.identity, translation="乙", stage=1).is_success
        window.variant_coordinator.manual_save()
    finally:
        release.set()
    _until(lambda: len(observed) == 2 and not window._project_save_coordinator._dictionary_writer.busy)
    assert observed == [(entry.original, "甲", "A"), (entry.original, "乙", "A")]


def test_dictionary_error_keeps_project_save_successful(project_dictionary, monkeypatch, caplog):
    window, library = project_dictionary
    ctx = window.context
    entry = next(iter(ctx.collection))
    assert ctx.update_projected_entry(entry.identity, translation="修订", stage=1).is_success

    def fail(*args, **kwargs):
        raise OSError("dictionary unavailable")

    monkeypatch.setattr(type(library), "save_revision", fail)
    saved = []
    assert window.save_current_project_async(include_dictionary=True, on_finished=saved.append)
    _until(lambda: bool(saved) and not window._project_save_coordinator._dictionary_writer.busy)
    assert saved == [True] and not ctx.dirty
    assert "dictionary unavailable" in caplog.text
