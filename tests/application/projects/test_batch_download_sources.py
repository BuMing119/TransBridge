import pytest

from tests.application.projects.test_categorized_upload import project as project_fixture
from tests.plugin_fixtures import write_plugin
from transbridge.application.io.identity import ExternalEntryRef
from transbridge.application.projects.variant_commands import EntryStatePatch

project = project_fixture


def snapshot(project, **options):
    _services, service, context, *_ = project
    return service.snapshot(context, endpoint="https://paratranz.cn", remote_project_id=42, **options)


def test_selected_source_never_hydrates_or_gets_blocked_by_an_unselected_broken_source(project, monkeypatch):
    _services, service, _context, first, second = project
    initial = snapshot(project).value
    selected = next(source for source in initial.sources if source.label == first.name)
    second.unlink()
    prepared = []
    original = service._source_preparer.prepare_source

    def prepare(request, *args, **kwargs):
        prepared.append(request.location)
        return original(request, *args, **kwargs)

    monkeypatch.setattr(service._source_preparer, "prepare_source", prepare)

    result = snapshot(project, source_ids=(selected.source_id,), tolerate_failures=True)

    assert result.is_success, result.diagnostics
    assert result.value.sources == (selected,)
    assert result.value.failures == ()
    assert prepared == []  # The complete selected source was already cached by the initial capture.


def test_tolerant_snapshot_keeps_complete_sources_and_reports_the_specific_failed_source(project):
    _services, _service, _context, _first, second = project
    initial = snapshot(project).value
    failed = next(source for source in initial.sources if source.label == second.name)
    write_plugin(second, [(0x800, "Shared", "Changed source content")])

    result = snapshot(project, tolerate_failures=True)

    assert result.is_success, result.diagnostics
    assert len(result.value.sources) == 1
    assert result.value.sources[0].source_id != failed.source_id
    assert len(result.value.failures) == 1
    source_id, label, error = result.value.failures[0]
    assert (source_id, label) == (failed.source_id, second.name)
    assert "指纹" in error


def test_default_upload_snapshot_still_rejects_an_incomplete_source(project):
    _services, _service, _context, _first, second = project
    write_plugin(second, [(0x800, "Shared", "Changed source content")])

    result = snapshot(project)

    assert not result.is_success
    assert result.value is None
    assert result.diagnostics[0].code == "PROJECT_SOURCE_FINGERPRINT_MISMATCH"


def test_download_snapshot_keeps_unsaved_translations_and_other_system_and_project_references(project):
    services, _service, context, *_ = project
    original = snapshot(project).value
    key = original.sources[0].entries[0].identity
    references = (
        ExternalEntryRef("paratranz", "project:42", 101),
        ExternalEntryRef("paratranz", "project:99", 102),
        ExternalEntryRef("another-system", "library", "shared"),
    )
    changed = services.gui_project_commands.replace_entry_records(
        {key: EntryStatePatch("Unsaved translation", 3, references)}, context
    )
    assert changed.is_success, changed.diagnostics
    before = services.project_lifecycle.active.variant.snapshot()
    disk_before = services.variants.read_snapshot(services.project_lifecycle.active.formal_variant_ref)

    result = services.gui_project_commands.file_sources_snapshot(
        context, endpoint="https://paratranz.cn", remote_project_id=42
    )

    assert result.is_success, result.diagnostics
    entry = next(entry for source in result.value.sources for entry in source.entries if entry.identity == key)
    assert (entry.translation, entry.stage, entry.external_refs) == ("Unsaved translation", 3, references)
    assert services.project_lifecycle.active.variant.snapshot() == before
    assert services.project_lifecycle.active.dirty
    assert services.variants.read_snapshot(services.project_lifecycle.active.formal_variant_ref) == disk_before


def test_file_source_facade_keeps_complete_sources_when_another_selected_source_fails(project):
    services, _service, context, _first, second = project
    second.unlink()

    result = services.gui_project_commands.file_sources_snapshot(
        context, endpoint="https://paratranz.cn", remote_project_id=42
    )

    assert result.is_success, result.diagnostics
    assert len(result.value.sources) == len(result.value.failures) == 1
    assert result.value.failures[0][1] == second.name


@pytest.mark.parametrize("guard_error", [RuntimeError("Target account changed"), ValueError("Cancelled before commit")])
def test_commit_guard_rejection_leaves_every_entry_and_revision_untouched(project, guard_error):
    services, _service, context, *_ = project
    captured = snapshot(project).value
    active = services.project_lifecycle.active
    before = active.variant.snapshot()
    projection_before = services.project_projection.snapshot()
    generation = services.project_lifecycle.generation
    patches = {
        source.entries[0].identity: EntryStatePatch(f"Download {source.label}", 3, ()) for source in captured.sources
    }
    calls = []

    def reject(_publish):
        calls.append("guard")
        raise guard_error

    result = services.gui_project_commands.replace_entry_records(
        patches,
        context,
        expected_project_revision=captured.project_revision,
        expected_variant_revision=captured.variant_revision,
        commit_mutation=reject,
    )

    assert not result.is_success
    assert calls == ["guard"]
    assert active.variant.snapshot() == before
    assert services.project_lifecycle.generation == generation
    assert services.project_projection.snapshot() == projection_before
    assert not active.dirty


def test_one_commit_publishes_both_sources_and_retains_untouched_source_owned_state(project):
    services, _service, context, *_ = project
    captured = snapshot(project).value
    active = services.project_lifecycle.active
    before = active.variant.snapshot()
    generation = services.project_lifecycle.generation
    patches = {
        source.entries[0].identity: EntryStatePatch(f"Download {source.label}", 3, ()) for source in captured.sources
    }
    calls = []
    projections = []
    subscription = services.project_projection.subscribe(projections.append, replay=False)

    def publish_once(publish):
        calls.append("guard")
        assert active.variant.snapshot() == before
        publish()

    result = services.gui_project_commands.replace_entry_records(
        patches,
        context,
        expected_project_revision=captured.project_revision,
        expected_variant_revision=captured.variant_revision,
        commit_mutation=publish_once,
    )

    assert result.is_success, result.diagnostics
    assert calls == ["guard"]
    after = active.variant.snapshot()
    assert after.revision == before.revision + 1
    assert services.project_lifecycle.generation == generation + 1
    assert len(projections) == 1
    assert projections[0] == services.project_projection.snapshot()
    assert projections[0].dirty
    projected = projections[0].to_dict()["values"]["entries"]
    assert {entry["translation"] for entry in projected} == {patch.translation for patch in patches.values()}
    assert after.source_fingerprints == before.source_fingerprints
    assert after.label_library == before.label_library
    assert [entry.entry_key for entry in after.entries] == [entry.entry_key for entry in before.entries]
    assert all(
        (entry.translation, entry.stage.value) == (patches[entry.entry_key].translation, 3) for entry in after.entries
    )
    subscription.close()


def test_stale_revision_is_rejected_before_commit_guard_or_any_source_changes(project):
    services, _service, context, *_ = project
    captured = snapshot(project).value
    key = captured.sources[0].entries[0].identity
    assert services.gui_project_commands.update_entry(
        key, context, translation="Concurrent local edit", stage=3
    ).is_success
    before = services.project_lifecycle.active.variant.snapshot()
    calls = []

    result = services.gui_project_commands.replace_entry_records(
        {key: EntryStatePatch("Stale download", 1, ())},
        context,
        expected_variant_revision=captured.variant_revision,
        commit_mutation=lambda publish: calls.append(publish),
    )

    assert not result.is_success
    assert result.diagnostics[0].code == "ACTIVE_VARIANT_REVISION_CHANGED"
    assert calls == []
    assert services.project_lifecycle.active.variant.snapshot() == before


def test_recovery_snapshot_write_failure_keeps_unsaved_state_and_allows_same_window_retry(project, monkeypatch, caplog):
    services, _service, context, *_ = project
    captured = snapshot(project).value
    key = captured.sources[0].entries[0].identity
    assert services.gui_project_commands.update_entry(
        key, context, translation="Unsaved local edit", stage=3
    ).is_success
    active = services.project_lifecycle.active
    before = active.variant.snapshot()
    projection_before = services.project_projection.snapshot()
    generation = services.project_lifecycle.generation
    original_replace = services.filesystem.replace

    def fail_snapshot(source, destination):
        if "/snapshots/" in str(destination).replace("\\", "/"):
            raise OSError("Snapshot destination is full")
        return original_replace(source, destination)

    monkeypatch.setattr(services.filesystem, "replace", fail_snapshot)
    failed = services.gui_project_commands.save_snapshot("download-recovery", context)

    assert not failed.is_success
    assert failed.diagnostics[0].code == "SNAPSHOT_SAVE_FAILED"
    assert "Snapshot destination is full" in caplog.text
    assert active.variant.snapshot() == before
    assert services.project_projection.snapshot() == projection_before
    assert services.project_lifecycle.generation == generation
    assert active.dirty

    monkeypatch.setattr(services.filesystem, "replace", original_replace)
    retried = services.gui_project_commands.save_snapshot("download-recovery", context)

    assert retried.is_success, retried.diagnostics
    assert active.variant.snapshot() == before
    assert services.project_lifecycle.generation == generation
    snapshots = services.project_snapshots.list(context)
    assert any(item.name == "download-recovery" for item in snapshots)
