from concurrent.futures import ThreadPoolExecutor
from itertools import count
from threading import Event

import pytest

from tests.plugin_fixtures import write_plugin
from transbridge.application.contracts import RequestContext
from transbridge.application.projects import EntryStatePatch, ProjectProvisioningRequest, ProjectSourceRequest
from transbridge.application.projects.remote_binding import ParaTranzProjectBinding
from transbridge.application.projects.source_references import ProjectSourceReferences
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.persistence.project_store.lifecycle import ProjectLifecycleStore
from transbridge.persistence.v2.ids import VariantId, VariantRef


@pytest.fixture
def project(tmp_path):
    ids = count()
    services = build_persistence_v2_services(
        tmp_path / "workspace",
        id_factory=lambda: str(next(ids)),
        timestamp_factory=lambda: "2026-11-13T00:00:00+00:00",
    )
    context = RequestContext("pending-source-reference", run_id="pending-reference")
    commands = services.gui_project_commands
    result = commands.create_project(ProjectProvisioningRequest("Pending sources"), context)
    assert result.is_success, result.diagnostics
    try:
        yield services, context, ProjectSourceReferences(services.projects, services.project_lifecycle)
    finally:
        services.close()


def _add_source(project, tmp_path, name):
    services, context, _ = project
    path = write_plugin(tmp_path / f"{name}.esp", [(0x800, name, f"Original {name}")])
    result = services.gui_project_commands.add_source(ProjectSourceRequest(str(path)), context)
    assert result.is_success, result.diagnostics
    return result.value.source_id, path


def _capture(project):
    services, _, references = project
    active = services.project_lifecycle.active
    return references.capture_pending_sources(
        active.project_ref.identity,
        active.formal_variant_ref.identity,
        project_revision=active.project.envelope.revision,
        variant_revision=active.variant.revision,
    )


def _repair(project, capture, source_id, info, path):
    _, _, references = project
    return references.update_pending_source_reference(
        capture,
        source_id,
        info.namespace,
        expected_fingerprint=info.fingerprint,
        expected_path=info.reference_path,
        reference_path=str(path),
    )


def test_pending_reference_batch_is_saved_with_sources_without_advancing_translation_revisions(project, tmp_path):
    services, context, references = project
    first_id, first_path = _add_source(project, tmp_path, "First")
    second_id, second_path = _add_source(project, tmp_path, "Second")
    active = services.project_lifecycle.active
    store = references.store_for(active.project_ref.identity)
    assert not store.sources()
    capture = _capture(project)
    sources = {item.reference_path: item for item in capture.sources}
    first, second = sources[str(first_path)], sources[str(second_path)]
    first_moved, second_moved = tmp_path / "NewFirst.esp", tmp_path / "NewSecond.esp"

    assert not _repair(project, capture, second_id, first, first_moved)
    assert _repair(project, capture, first_id, first, first_moved)
    assert _repair(project, capture, second_id, second, second_moved)
    assert not _repair(project, capture, first_id, first, tmp_path / "Stale.esp")
    repaired = services.project_lifecycle.active
    assert repaired.summary() == active.summary()
    assert repaired.dirty and repaired.variant is active.variant
    assert not store.sources()

    result = services.gui_project_commands.save(context)
    assert result.is_success, result.diagnostics
    assert {item.reference_path for item in store.sources()} == {str(first_moved), str(second_moved)}
    assert not _capture(project).sources
    assert not _repair(project, capture, first_id, first, first_path)


def test_pending_reference_repair_rejects_a_save_in_progress(project, tmp_path, monkeypatch):
    services, context, references = project
    source_id, path = _add_source(project, tmp_path, "Racing")
    capture = _capture(project)
    (source,) = capture.sources
    entered, release = Event(), Event()
    original_save = ProjectLifecycleStore._save

    def pause(store, save):
        entered.set()
        assert release.wait(5)
        return original_save(store, save)

    monkeypatch.setattr(ProjectLifecycleStore, "_save", pause)
    with ThreadPoolExecutor(max_workers=1) as worker:
        saving = worker.submit(services.gui_project_commands.save, context)
        try:
            assert entered.wait(5)
            assert _capture(project) is None
            assert not _repair(project, capture, source_id, source, tmp_path / "Moved.esp")
        finally:
            release.set()
        result = saving.result(timeout=5)
    assert result.is_success, result.diagnostics
    active = services.project_lifecycle.active
    assert references.store_for(active.project_ref.identity).sources()[0].reference_path == str(path)


def test_pending_reference_can_be_saved_after_project_metadata_was_persisted(project, tmp_path):
    services, context, references = project
    source_id, _ = _add_source(project, tmp_path, "Metadata")
    binding = ParaTranzProjectBinding(42, "Linked project", "https://paratranz.cn")
    result = services.project_remote_bindings.set_paratranz_binding(binding, context)
    assert result.is_success, result.diagnostics
    active = services.project_lifecycle.active
    assert active.persisted_project_revision == active.project.envelope.revision
    store = references.store_for(active.project_ref.identity)
    assert not store.sources()
    capture = _capture(project)
    (source,) = capture.sources
    new_path = tmp_path / "Moved.esp"
    assert _repair(project, capture, source_id, source, new_path)

    result = services.gui_project_commands.save(context)
    assert result.is_success, result.diagnostics
    assert store.sources()[0].reference_path == str(new_path)
    assert store.read_page(active.formal_variant_ref.identity)[0].snapshot.original == "Original Metadata"


def test_only_unconfirmed_save_of_active_variant_blocks_pending_reference_repair(project, tmp_path):
    services, _, _ = project
    source_id, path = _add_source(project, tmp_path, "Unconfirmed")
    lifecycle = services.project_lifecycle
    active = lifecycle.active
    capture = _capture(project)
    (source,) = capture.sources

    def unexpected_check(_context):
        pytest.fail("A path update should leave unconfirmed save handling to the next content command")

    lifecycle.defer_variant_save_check(active.formal_variant_ref, unexpected_check)
    assert _capture(project) is None
    assert not _repair(project, capture, source_id, source, path)

    another = VariantRef(VariantId("other"), active.project_ref.identity)
    lifecycle.defer_variant_save_check(another, unexpected_check)
    assert _capture(project) == capture
    assert _repair(project, capture, source_id, source, tmp_path / "Moved.esp")


def test_durable_save_uses_reference_repaired_after_it_captured_a_source(project, tmp_path, monkeypatch):
    services, context, references = project
    source_id, _ = _add_source(project, tmp_path, "Durable")
    capture = _capture(project)
    (source,) = capture.sources
    path = tmp_path / "Repaired.esp"
    lifecycle = services.project_lifecycle
    active = lifecycle.active
    (entry,) = active.variant.snapshot().entries
    commit = lifecycle.commit_active_content

    def repair_before_commit(*args, **kwargs):
        assert _repair(project, capture, source_id, source, path)
        return commit(*args, **kwargs)

    monkeypatch.setattr(lifecycle, "commit_active_content", repair_before_commit)
    result = services.gui_project_commands.replace_entry_records(
        {entry.entry_key: EntryStatePatch("Saved translation", 3, ())},
        context,
        expected_project_revision=active.project.envelope.revision,
        expected_variant_revision=active.variant.revision,
        expected_variant_ref=active.formal_variant_ref,
        persist=True,
    )
    assert result.is_success, result.diagnostics
    store = references.store_for(active.project_ref.identity)
    assert store.sources()[0].reference_path == str(path)
    assert store.read_page(active.formal_variant_ref.identity)[0].state.translation == "Saved translation"
    assert not lifecycle.active.dirty
