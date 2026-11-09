from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from uuid import uuid4
from zipfile import ZipFile

from PyQt6.QtWidgets import QFileDialog
import pytest

from tests.plugin_fixtures import write_plugin
from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io import FormatId, ParseRequest, SourceDescriptor, TranslationIoUseCase, WriteRequest
from transbridge.application.projects import ProjectProvisioningRequest, ProjectSourceRequest
from transbridge.application.projects.working_copy import ProjectWorkingCopy
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.parser.xt.sst_codec import SstDocument
from transbridge.persistence.project_store.history import ProjectHistory
from transbridge.persistence.project_store.lifecycle import ProjectLifecycleStore
from transbridge.persistence.project_store.paths import project_path
from transbridge.persistence.project_store.records import WorkspaceProjects
from transbridge.persistence.project_store.repository import ProjectStore
from transbridge.persistence.task_store import TaskStore
from transbridge.persistence.v2 import OsPersistenceFilesystem, ProjectId, ProjectRef, ProjectRepository, VariantRef
from transbridge.ui.coordinators.project_coordinator import ProjectCoordinator

_LEGACY_ROOTS = {"projects", "source_artifacts", "active-project.json", "project-catalog.json"}


def _services(root):
    return build_persistence_v2_services(
        root, id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "2026-11-09T00:00:00+08:00"
    )


def _write_legacy_data(root):
    ref = ProjectRef(ProjectId("legacy-project"))
    record = Path(ProjectRepository(str(root), OsPersistenceFilesystem()).path_for(ref))
    record.parent.mkdir(parents=True)
    record.write_text(
        json.dumps({
            "schema_version": 2,
            "entity_type": "project",
            "id": ref.identity.value,
            "revision": 0,
            "data": {"name": "Legacy project", "sources": [], "variant_ids": [], "active_variant_id": None},
        }),
        encoding="utf-8",
    )
    (root / "active-project.json").write_text(
        '{"schema_version":1,"project_id":"legacy-project","variant_id":null}', encoding="utf-8"
    )
    artifact = root / "source_artifacts" / "legacy-artifact.bin"
    artifact.parent.mkdir()
    artifact.write_bytes(b"prior source artifact bytes")
    return record


def _legacy_files(root):
    return {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and path.relative_to(root).parts[0] in _LEGACY_ROOTS
    }


def test_normal_project_lifecycle_reopens_without_plugin_and_does_not_restore_legacy_data(tmp_path, monkeypatch):
    root = tmp_path / "data"
    legacy_record = _write_legacy_data(root)
    before = _legacy_files(root)
    input_dir = tmp_path / "private-source-location-394"
    input_dir.mkdir()
    source = write_plugin(input_dir / "Source.esp", [(0x800, "Greeter", "Welcome")])
    plugin = source.read_bytes()
    context = RequestContext("gui", run_id="fr32-storage-composition")
    path_open = Path.open

    def block_legacy_reads(path, *args, **kwargs):
        if path.is_relative_to(root) and _LEGACY_ROOTS.intersection(path.relative_to(root).parts[:1]):
            raise AssertionError(f"Normal project lifecycle tried to open legacy material: {path}")
        return path_open(path, *args, **kwargs)

    with monkeypatch.context() as guarded:
        guarded.setattr(Path, "open", block_legacy_reads)
        initial = _services(root)
        try:
            assert not initial.project_catalog.list_projects().projects
            preview = initial.gui_project_commands.prepare_create(
                ProjectProvisioningRequest("New project", source=ProjectSourceRequest(str(source))), context
            )
            assert preview.is_success, preview.diagnostics
            project_id = ProjectId(preview.value["project_id"])
            committed = initial.gui_project_commands.commit_create(preview.value["token"], context)
            assert committed.is_success, committed.diagnostics
            active = initial.project_lifecycle.active
            (entry,) = active.variant.snapshot().entries
            updated = initial.gui_project_commands.update_entry(entry.entry_key, context, translation="欢迎")
            assert updated.is_success, updated.diagnostics
            saved = initial.gui_project_commands.save(context)
            assert saved.is_success, saved.diagnostics
        finally:
            initial.close()

        source.unlink()
        reopened = _services(root)
        try:
            prepared = reopened.current_project_opener.prepare_active(context)
            assert prepared.is_success, prepared.diagnostics
            assert reopened.current_project_opener.activate(prepared.value, context).is_success
            active = reopened.project_lifecycle.active
            assert active.project_ref.identity == project_id
            assert not active.missing_inputs
            assert [entry.translation for entry in active.variant.snapshot().entries] == ["欢迎"]
            assert [item.project_id for item in reopened.project_catalog.list_projects().projects] == [project_id.value]
            persisted = ProjectStore(project_path(root, project_id))
            assert persisted.read_page(persisted.summary().active_variant)[0].state.translation == "欢迎"

            guarded.undo()
            rejected = reopened.current_project_opener.prepare_path(legacy_record, context)
            assert not rejected.is_success
            diagnostic = rejected.diagnostics[0].message.lower()
            assert "不支持的项目格式" in diagnostic or "unsupported project format" in diagnostic
            assert reopened.project_lifecycle.active is active
        finally:
            reopened.close()

    assert _legacy_files(root) == before
    for file in project_path(root, project_id).parent.rglob("*"):
        if file.is_file():
            content = file.read_bytes()
            assert plugin not in content
            assert input_dir.name.encode() not in content


def test_desktop_project_picker_exposes_the_database_and_routes_it_to_the_opener(tmp_path, monkeypatch):
    root = tmp_path / "data"
    context = RequestContext("gui", run_id="pick-current-project")
    setup = _services(root)
    try:
        assert setup.gui_project_commands.create_project(ProjectProvisioningRequest("Pickable"), context).is_success
        project_id = setup.project_lifecycle.active.project_ref.identity
        database = project_path(root, project_id)
    finally:
        setup.close()
    services = _services(root)
    try:
        opener = services.current_project_opener
        host = SimpleNamespace(
            current_project_opener=opener, runtime_context=context, context=SimpleNamespace(dirty=False)
        )
        coordinator = ProjectCoordinator(host)

        def choose_current_project(_parent, _caption, directory, file_filter):
            assert database.is_relative_to(directory)
            assert database.name in file_filter.split(";;", 1)[0]
            return str(database), file_filter

        def finish_synchronous_open(prepare, *, dirty_decision, **_options):
            prepared = prepare()
            assert prepared.is_success, prepared.diagnostics
            activated = opener.activate(prepared.value, context, dirty_decision=dirty_decision)
            assert activated.is_success, activated.diagnostics

        monkeypatch.setattr(QFileDialog, "getOpenFileName", choose_current_project)
        monkeypatch.setattr(coordinator, "_start_current_project_open", finish_synchronous_open)
        coordinator.open_project()

        assert services.project_lifecycle.active.project_ref.identity == project_id
    finally:
        services.close()


def test_fresh_variant_after_reopen_uses_imported_default_translation(tmp_path):
    root = tmp_path / "data"
    source = tmp_path / "Baseline.json"
    source.write_text(
        json.dumps([{"id": 71, "key": "welcome", "original": "Welcome", "translation": "Imported", "stage": 1}]),
        encoding="utf-8",
    )
    context = RequestContext("gui", run_id="fresh-variant-after-reopen")
    services = _services(root)
    try:
        commands = services.gui_project_commands
        created = commands.create_project(
            ProjectProvisioningRequest("Imported project", source=ProjectSourceRequest(str(source))), context
        )
        assert created.is_success, created.diagnostics
        active = services.project_lifecycle.active
        original = active.formal_variant_ref
        store = ProjectStore(project_path(root, active.project_ref.identity))
        (imported,) = store.read_page(original.identity)
        assert imported.state.translation == "Imported"
        assert commands.update_entry(imported.state.entry_key, context, translation="Edited main", stage=3).is_success
        assert commands.save(context).is_success
    finally:
        services.close()
    source.unlink()

    reopened = _services(root)
    try:
        prepared = reopened.current_project_opener.prepare_active(context)
        assert prepared.is_success, prepared.diagnostics
        assert reopened.current_project_opener.activate(prepared.value, context).is_success
        commands = reopened.gui_project_commands
        fresh_result = commands.create_variant("From imported source", context, copy_active=False)
        assert fresh_result.is_success, fresh_result.diagnostics
        fresh = reopened.project_lifecycle.active.formal_variant_ref
        (fresh_row,) = store.read_page(fresh.identity)
        assert fresh != original and fresh_row.state.entry_key == imported.state.entry_key
        assert (fresh_row.state.translation, fresh_row.state.stage) == (
            imported.state.translation,
            imported.state.stage,
        )
        (main_row,) = store.read_page(original.identity)
        assert main_row.state.translation == "Edited main"
    finally:
        reopened.close()


def test_edit_during_normal_save_stays_dirty_until_the_next_save(tmp_path, monkeypatch):
    root = tmp_path / "data"
    source = write_plugin(tmp_path / "Example.esp", [(0x800, "Greeter", "Welcome")])
    context = RequestContext("gui", run_id="save-interleaving")
    services = _services(root)
    try:
        commands = services.gui_project_commands
        created = commands.create_project(
            ProjectProvisioningRequest("Save interleaving", source=ProjectSourceRequest(str(source))), context
        )
        assert created.is_success, created.diagnostics
        active = services.project_lifecycle.active
        variant = active.formal_variant_ref.identity
        store = ProjectStore(project_path(root, active.project_ref.identity))
        (before,) = store.read_page(variant)
        key = before.state.entry_key
        assert commands.update_entry(key, context, translation="First", stage=1).is_success
        entered, release = Event(), Event()
        save = ProjectLifecycleStore._save

        def paused(lifecycle_store, mutation):
            entered.set()
            assert release.wait(5)
            return save(lifecycle_store, mutation)

        with monkeypatch.context() as patch, ThreadPoolExecutor(max_workers=2) as workers:
            patch.setattr(ProjectLifecycleStore, "_save", paused)
            pending = workers.submit(commands.save, context)
            try:
                assert entered.wait(5)
                later = workers.submit(commands.update_entry, key, context, translation="Second", stage=3)
                edited = later.result(timeout=3)
                assert edited.is_success, edited.diagnostics
            finally:
                release.set()
            acknowledged = pending.result(timeout=5)
            assert acknowledged.is_success, acknowledged.diagnostics
        assert store.read_entries(variant, (key,))[0].state.translation == "First"
        assert services.project_lifecycle.active.dirty
        assert services.project_lifecycle.active.variant.snapshot().entries[0].translation == "Second"

        next_save = commands.save(context)
        assert next_save.is_success, next_save.diagnostics
        assert not services.project_lifecycle.active.dirty
        assert store.read_entries(variant, (key,))[0].state.translation == "Second"
    finally:
        services.close()


def test_plugin_ssu9_export_after_project_reopen_requires_only_extracted_facts(tmp_path):
    root = tmp_path / "data"
    source = write_plugin(
        tmp_path / "Native.esp", [(0x801, "FirstNpc", "First name"), (0x800, "SecondNpc", "Second name")]
    )
    context = RequestContext("gui", run_id="stored-plugin-ssu9-export")
    io = TranslationIoUseCase()
    parsed = io.parse(ParseRequest(SourceDescriptor(str(source)), context, FormatId.PLUGIN_SSE))
    assert parsed.outcome is OperationOutcome.COMPLETED, parsed.diagnostics
    baseline_path = tmp_path / "baseline.sst"
    request = WriteRequest(
        SourceDescriptor(str(baseline_path)),
        FormatId.SST_SSU9,
        tuple(replace(row, translation=f"Translated {row.original}", stage=1) for row in parsed.entries),
        0,
        context,
        source_snapshot=parsed.source_snapshot,
    )
    baseline = io.write(request)
    assert baseline.is_success, baseline.diagnostics

    services = _services(root)
    try:
        commands = services.gui_project_commands
        created = commands.create_project(
            ProjectProvisioningRequest("Native source", source=ProjectSourceRequest(str(source))), context
        )
        assert created.is_success, created.diagnostics
        active = services.project_lifecycle.active
        store = ProjectStore(project_path(root, active.project_ref.identity))
        for row in store.read_page(active.formal_variant_ref.identity):
            edited = commands.update_entry(
                row.snapshot.entry_key, context, translation=f"Translated {row.snapshot.original}", stage=1
            )
            assert edited.is_success, edited.diagnostics
        assert commands.save(context).is_success
    finally:
        services.close()
    plugin = source.read_bytes()
    source.unlink()

    reopened = _services(root)
    try:
        prepared = reopened.current_project_opener.prepare_active(context)
        assert prepared.is_success, prepared.diagnostics
        (hydration,) = prepared.value.hydrations
        assert hydration.source_snapshot.content is None
        output = tmp_path / "exported.sst"
        stored_request = replace(
            request,
            target=SourceDescriptor(str(output)),
            entries=hydration.entries,
            source_snapshot=hydration.source_snapshot,
        )
        exported = io.write(stored_request)
        assert exported.is_success, exported.diagnostics
        assert SstDocument.from_bytes(output.read_bytes()) == SstDocument.from_bytes(baseline_path.read_bytes())

        renamed_plugin = tmp_path / "renamed-plugin.sst"
        renamed_plugin.write_bytes(plugin)
        refused = io.write(replace(stored_request, target=SourceDescriptor(str(renamed_plugin))))
        assert not refused.is_success and refused.diagnostics[0].code == "SST_TARGET_IS_SOURCE_PLUGIN"
        assert renamed_plugin.read_bytes() == plugin
    finally:
        reopened.close()


def test_source_replacement_preserves_variant_independence_and_historical_order(tmp_path):
    inputs = tmp_path / "private-snapshot-source-293"
    inputs.mkdir()
    first = write_plugin(inputs / "ZSource.esp", [(0x801, "Welcome", "Welcome"), (0x800, "Older", "Older")])
    second = write_plugin(inputs / "ASource.esp", [(0x800, "Other", "Another source")])
    replacement = write_plugin(inputs / "Updated.esp", [(0x805, "New", "New"), (0x801, "Welcome", "Welcome")])
    source_bytes = tuple(path.read_bytes() for path in (first, second, replacement))
    root = tmp_path / "data"
    context = RequestContext("gui", run_id="snapshot-source-update")
    services = _services(root)
    try:
        commands = services.gui_project_commands
        created = commands.create_project(
            ProjectProvisioningRequest(
                "Historical sources",
                source=ProjectSourceRequest(str(first)),
                additional_sources=(ProjectSourceRequest(str(second)),),
            ),
            context,
        )
        assert created.is_success, created.diagnostics
        active = services.project_lifecycle.active
        project_ref, original_variant = active.project_ref, active.formal_variant_ref
        project = ProjectStore(project_path(root, project_ref.identity))
        initial = project.read_page(original_variant.identity)
        assert [item.snapshot.original for item in initial] == ["Welcome", "Older", "Another source"]
        welcome = initial[0].snapshot.entry_key
        assert commands.update_entry(welcome, context, translation="Main translation", stage=3).is_success
        assert commands.save(context).is_success
        assert commands.save_snapshot("Before the source replacement", context).is_success
        history = ProjectHistory(WorkspaceProjects(root))
        (snapshot,) = history.list(original_variant)
        frozen = project.read_page(original_variant.identity)

        copied = commands.create_variant("Independent copy", context, copy_active=True)
        assert copied.is_success, copied.diagnostics
        alternate = services.project_lifecycle.active.formal_variant_ref
        assert alternate != original_variant
        assert project.read_entries(alternate.identity, (welcome,))[0].state.translation == "Main translation"
        assert commands.update_entry(welcome, context, translation="Copy translation", stage=3).is_success
        assert commands.save(context).is_success
        assert commands.switch_v2(project_ref, original_variant, context).is_success

        tasks = TaskStore(root / "task-store-v1").recovery()
        tasks.create({
            "task_id": "removed-task",
            "project_id": project_ref.identity.value,
            "variant_id": original_variant.identity.value,
            "mode": "download",
            "config_digest": "synthetic",
            "state": "completed",
            "created_at": "2026-11-09T00:00:00+08:00",
            "sources": [],
            "result_kind": "download",
            "results_only": True,
        })
        tasks.delete("removed-task")
        assert commands.update_entry(welcome, context, translation="Later change", stage=1).is_success
        assert commands.save(context).is_success
        restored = services.project_snapshots.restore(snapshot.identity, context)
        assert restored.is_success, restored.diagnostics
        assert commands.save(context).is_success
        assert tasks.list_summaries() == ()

        updates = services.project_source_updates
        targets = updates.list_sources(str(project.path), context)
        assert targets.is_success, targets.diagnostics
        selected = next(target for target in targets.value if target.label == first.name)
        preview = updates.prepare(str(project.path), selected.source_id, str(replacement), context)
        assert preview.is_success, preview.diagnostics
        assert preview.value.variant_count == 2
        updated = updates.commit(preview.value.token, context)
        assert updated.is_success, updated.diagnostics
    finally:
        services.close()

    for path in (first, second, replacement):
        path.unlink()
    reopened = ProjectStore(project.path)
    for variant, translation in ((original_variant, "Main translation"), (alternate, "Copy translation")):
        entries = reopened.read_page(variant.identity)
        assert [item.snapshot.original for item in entries] == ["New", "Welcome", "Another source"]
        assert [item.state.translation for item in entries] == ["", translation, ""]
    history = ProjectHistory(WorkspaceProjects(root))
    historical = history.load(snapshot.identity, original_variant)
    assert {entry.entry_key: entry for entry in historical.entries} == {
        item.state.entry_key: item.state for item in frozen
    }
    assert history.read_page(snapshot.identity, original_variant) == frozen

    services = _services(root)
    try:
        commands = services.gui_project_commands
        assert commands.switch_v2(project_ref, original_variant, context).is_success
        restored = services.project_snapshots.restore(snapshot.identity, context)
        assert restored.is_success, restored.diagnostics
        assert commands.save(context).is_success
    finally:
        services.close()

    restored_store = ProjectStore(project.path)
    assert [source.name for source in restored_store.sources()] == [first.name, second.name]
    restored = restored_store.read_page(original_variant.identity)
    assert [
        (item.snapshot.entry_key, item.snapshot.original, item.state.translation, item.state.stage) for item in restored
    ] == [
        (item.snapshot.entry_key, item.snapshot.original, item.state.translation, item.state.stage) for item in frozen
    ]
    assert tasks.list_summaries() == ()
    for file in root.rglob("*"):
        if file.is_file():
            data = file.read_bytes()
            assert inputs.name.encode() not in data
            assert all(plugin not in data for plugin in source_bytes)


@pytest.mark.parametrize("include_snapshots", [False, True])
def test_portable_project_keeps_only_selected_history_and_translations(tmp_path, include_snapshots):
    inputs = tmp_path / "private-portable-source-129"
    inputs.mkdir()
    source = write_plugin(inputs / "Portable.esp", [(0x800, "Greeter", "Welcome")])
    plugin = source.read_bytes()
    origin = tmp_path / "origin"
    destination = tmp_path / "destination"
    package = tmp_path / "portable.transbridge"
    context = RequestContext("gui", run_id="portable-storage")
    services = _services(origin)
    try:
        commands = services.gui_project_commands
        created = commands.create_project(
            ProjectProvisioningRequest("Portable", source=ProjectSourceRequest(str(source))), context
        )
        assert created.is_success, created.diagnostics
        active = services.project_lifecycle.active
        project_ref, variant_ref = active.project_ref, active.formal_variant_ref
        assert commands.save_snapshot("Original wording", context).is_success
        (historical,) = services.project_snapshots.list(context)
    finally:
        services.close()
    store = ProjectStore(project_path(origin, project_ref.identity))
    (entry,) = store.read_page(variant_ref.identity)
    working = ProjectWorkingCopy(store, variant_ref.identity)
    translation = r"C:\Game\Dialogue\quoted-in-translated-text"
    working.edit(entry.state.entry_key, translation=translation, stage=3)
    working.save("private-apply-receipt")
    task_id = "private-task-payload-751"
    TaskStore(origin / "task-store-v1").recovery().create({
        "task_id": task_id,
        "project_id": project_ref.identity.value,
        "variant_id": variant_ref.identity.value,
        "mode": "download",
        "config_digest": "synthetic",
        "state": "completed",
        "created_at": "2026-11-09T00:00:00+08:00",
        "sources": [],
        "result_kind": "download",
        "results_only": True,
    })
    source.unlink()
    services = _services(origin)
    try:
        selected = services.current_project_opener.prepare_active(context)
        assert selected.is_success, selected.diagnostics
        assert services.current_project_opener.activate(selected.value, context).is_success
        exported = services.project_archive.export_project(str(package), context, include_snapshots=include_snapshots)
        assert Path(exported) == package
    finally:
        services.close()

    with ZipFile(package) as archive:
        assert set(archive.namelist()) == {"manifest.json", "project.sqlite3"}
        payload = archive.read("project.sqlite3")
        assert b"private-apply-receipt" not in payload
        assert task_id.encode() not in payload
        assert plugin not in payload
        assert inputs.name.encode() not in payload
    services = _services(destination)
    try:
        imported_path = services.project_archive.import_project(str(package), context)
        opened = services.current_project_opener.open_path(imported_path, context)
        assert opened.is_success, opened.diagnostics
        imported = ProjectStore(imported_path)
        assert imported.summary().identity == project_ref.identity
        (restored,) = imported.read_page(variant_ref.identity)
        assert restored.state.entry_key == entry.state.entry_key
        assert restored.state.translation == translation
        assert imported.receipt("private-apply-receipt") is None
        history = ProjectHistory(WorkspaceProjects(destination))
        assert history.list(variant_ref) == ((historical,) if include_snapshots else ())
        if include_snapshots:
            assert history.load(historical.identity, variant_ref).entries[0].translation == ""

        copy_path = services.project_archive.import_project(
            str(package), context, requested_name="Independent import", copy_on_identity_conflict=True
        )
        copy = ProjectStore(copy_path)
        copy_summary = copy.summary()
        assert copy_summary.identity != project_ref.identity
        assert copy.read_page(copy_summary.active_variant) == (restored,)
        copy_variant = VariantRef(copy_summary.active_variant, copy_summary.identity)
        assert history.list(copy_variant) == history.list(variant_ref)
    finally:
        services.close()
    assert TaskStore(destination / "task-store-v1").recovery().list_summaries() == ()
