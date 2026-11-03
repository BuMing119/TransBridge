from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from transbridge.application.contracts import RequestContext
from transbridge.application.io import FormatId, SourceDescriptor, SourceSnapshot
from transbridge.application.io.identity import EntryKey, ExternalEntryRef, SourceNamespace
from transbridge.application.projects import DirtyDecision, EntryStatePatch, ProjectSourceRequest
from transbridge.application.projects.prepared_source_results import prepare_parsed_source
from transbridge.application.projects.source_registry import migrate_legacy_source_registry
from transbridge.bootstrap import build_runtime
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.persistence.current_project import PROJECT_FILE_FILTER, CurrentProjectOpener
from transbridge.persistence.saved_project_sources import SavedProjectSourcePreparer
from transbridge.persistence.source_artifact_store import ProjectSourceArtifactStore
from transbridge.persistence.v2 import (
    SCHEMA_VERSION,
    ProjectDto,
    ProjectId,
    ProjectRef,
    SchemaEnvelope,
    VariantId,
    VariantRef,
    VariantSnapshot,
)


def _captured_source(location, namespace, key, translation):
    entry = TranslationEntry(
        key, key, "Original", translation, 1, "FULL", entry_key=EntryKey(SourceNamespace(namespace), key)
    )
    snapshot = SourceSnapshot.from_bytes(SourceDescriptor(location), FormatId.PLUGIN_SSE, b"fixture")
    prepared = prepare_parsed_source(location, (entry,), snapshot, FormatId.PLUGIN_SSE)
    return replace(prepared, descriptor=(*prepared.descriptor, ("display_name", Path(location).name)))


class _Ids:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> str:
        self.value += 1
        return f"id-{self.value}"


class _StaticSourcePreparer:
    def __init__(self, location: str) -> None:
        self.location = location
        self.prepared = _captured_source(location, "source:plugin:new", "new-entry", "导入的新译文")

    def prepare_source(self, request, context, *, role, common_options):
        assert request.location == self.location
        return self.prepared


def _records(services):
    project_ref = ProjectRef(ProjectId("project-a"))
    variant_ref = VariantRef(VariantId("variant-a"), project_ref.identity)
    path = Path(services.root) / "source.esp"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fixture")
    prepared = _captured_source(str(path), "source-a", "entry-a", "old")
    prepared = SavedProjectSourcePreparer(None, ProjectSourceArtifactStore(services.root)).persist_prepared_source(
        prepared
    )
    registry = migrate_legacy_source_registry(project_ref.identity.value, (prepared.to_dict(),))
    project = ProjectDto(
        SchemaEnvelope(
            SCHEMA_VERSION,
            project_ref.kind,
            project_ref.identity.value,
            0,
            {
                "name": "Project A",
                **registry.to_project_data(),
                "variant_ids": [variant_ref.identity.value],
                "active_variant_id": variant_ref.identity.value,
            },
        )
    )
    variant = VariantSnapshot(variant_ref, (prepared.baseline.fingerprint,), prepared.baseline.entries, 0)
    services.projects.save(project_ref, project)
    services.variants.save(variant_ref, variant.to_dto())
    return project_ref, variant_ref, prepared.baseline


def test_missing_source_reference_is_repaired_before_saved_translations_become_editable(tmp_path: Path) -> None:
    ids = _Ids()
    services = build_persistence_v2_services(
        tmp_path / "projects-v2",
        id_factory=ids,
        timestamp_factory=lambda: "2026-08-18T00:00:00+00:00",
    )
    project_ref, variant_ref, _baseline = _records(services)
    project = services.projects.read_snapshot(project_ref)
    source = project.envelope.data["sources"][0]
    source.pop("source_artifact_id")
    source["content_state"] = "missing"
    services.projects.save(project_ref, project)
    context = RequestContext("gui", run_id="run")

    activated = services.gui_project_commands.switch_v2(project_ref, variant_ref, context)

    assert activated.is_success
    assert services.project_lifecycle.active is not None
    assert not services.project_lifecycle.active.missing_inputs
    assert services.gui_project_commands.update_entry("entry-a", context, translation="rescued").is_success
    saved = services.gui_project_commands.save(context)
    assert saved.is_success, saved.diagnostics
    assert not services.project_lifecycle.active.dirty
    projection = services.project_projection.snapshot()
    assert projection is not None
    assert projection.to_dict()["values"]["variant_id"] == "variant-a"


def test_variant_gui_commands_make_dirty_from_revision_then_save_clears_it(tmp_path: Path) -> None:
    ids = _Ids()
    services = build_persistence_v2_services(
        tmp_path / "projects-v2",
        id_factory=ids,
        timestamp_factory=lambda: "2026-08-18T00:00:00+00:00",
    )
    project_ref, variant_ref, baseline = _records(services)
    services.baselines.register(project_ref, variant_ref, (baseline,))
    context = RequestContext("gui", run_id="run")
    assert services.gui_project_commands.switch_v2(
        project_ref,
        variant_ref,
        context,
        dirty_decision=DirtyDecision.SAVE,
    ).is_success

    changed = services.gui_project_commands.update_entry(
        "entry-a",
        context,
        translation="new",
    )
    dirty = services.project_projection.snapshot()
    saved = services.gui_project_commands.save(context)
    clean = services.project_projection.snapshot()

    assert changed.is_success
    assert dirty is not None and dirty.dirty
    assert saved.is_success
    assert clean is not None and not clean.dirty
    assert clean.to_dict()["values"]["entries"][0]["translation"] == "new"


def test_variant_external_reference_survives_save_and_process_restart(tmp_path: Path) -> None:
    root = tmp_path / "projects-v2"
    services = build_persistence_v2_services(
        root,
        id_factory=_Ids(),
        timestamp_factory=lambda: "2026-08-30T00:00:00+00:00",
    )
    project_ref, variant_ref, baseline = _records(services)
    services.baselines.register(project_ref, variant_ref, (baseline,))
    context = RequestContext("gui", run_id="external-ref")
    assert services.gui_project_commands.switch_v2(project_ref, variant_ref, context).is_success
    entry_key = EntryKey(SourceNamespace("source-a"), "entry-a")
    reference = ExternalEntryRef("paratranz", "project:42", 71)

    committed = services.gui_project_commands.replace_entry_records(
        {entry_key: EntryStatePatch("remote", 1, (reference,))},
        context,
        expected_project_revision=0,
        expected_variant_revision=0,
    )
    assert committed.is_success
    assert services.gui_project_commands.save(context).is_success
    services.close()

    restarted = build_persistence_v2_services(
        root,
        id_factory=_Ids(),
        timestamp_factory=lambda: "2026-08-30T00:01:00+00:00",
    )
    loaded = restarted.variants.load(variant_ref)
    restored = VariantSnapshot.from_dto(loaded.value, variant_ref)

    assert restored.entries[0].external_refs == (reference,)
    restarted.close()


def test_add_remove_source_survives_save_and_process_restart(tmp_path: Path) -> None:
    root = tmp_path / "projects-v2"
    source_path = tmp_path / "NewPlugin.esp"
    source_path.write_bytes(b"fixture")
    source_location = str(source_path.resolve())
    preparer = _StaticSourcePreparer(source_location)
    services = build_persistence_v2_services(
        root,
        id_factory=_Ids(),
        timestamp_factory=lambda: "2026-08-30T00:00:00+00:00",
        source_preparer=preparer,
    )
    project_ref = ProjectRef(ProjectId("p-source"))
    variant_ref = VariantRef(VariantId("v-source"), project_ref.identity)
    project = ProjectDto(
        SchemaEnvelope(
            2,
            project_ref.kind,
            project_ref.identity.value,
            0,
            {
                "name": "Source restart",
                "sources": [],
                "source_relations": [],
                "source_registry_diagnostics": [],
                "variant_ids": [variant_ref.identity.value],
                "active_variant_id": variant_ref.identity.value,
            },
        )
    )
    services.projects.save(project_ref, project)
    services.variants.save(variant_ref, VariantSnapshot(variant_ref, (), ()).to_dto())
    services.baselines.register(project_ref, variant_ref, (), allow_empty=True)
    context = RequestContext("gui", "source-restart", project_ref.identity.value, variant_ref.identity.value)
    assert services.gui_project_commands.switch_v2(project_ref, variant_ref, context).is_success

    added = services.gui_project_commands.add_source(
        ProjectSourceRequest(source_location, FormatId.PLUGIN_SSE),
        context,
    )
    assert added.is_success
    assert services.gui_project_commands.save(context).is_success
    project_path = services.projects.path_for(project_ref)
    services.close()

    restarted = build_persistence_v2_services(
        root,
        id_factory=_Ids(),
        timestamp_factory=lambda: "2026-08-30T00:01:00+00:00",
        source_preparer=preparer,
    )
    reopened = restarted.current_project_opener.open_path(project_path, context)
    assert reopened.is_success
    restored = restarted.project_projection.snapshot().to_dict()["values"]
    assert [source["location"] for source in restored["sources"]] == [source_location]
    assert [(entry["entry_key"]["local_key"], entry["translation"]) for entry in restored["entries"]] == [
        ("new-entry", "导入的新译文")
    ]

    assert restarted.gui_project_commands.remove_source(source_location, context).is_success
    assert restarted.gui_project_commands.save(context).is_success
    restarted.close()

    reopened_again = build_persistence_v2_services(
        root,
        id_factory=_Ids(),
        timestamp_factory=lambda: "2026-08-30T00:02:00+00:00",
        source_preparer=preparer,
    )
    assert reopened_again.current_project_opener.open_path(project_path, context).is_success
    final = reopened_again.project_projection.snapshot().to_dict()["values"]
    assert final["sources"] == []
    assert final["entries"] == []
    reopened_again.close()


def test_v2_variant_catalog_create_copy_switch_and_delete(tmp_path: Path) -> None:
    ids = _Ids()
    services = build_persistence_v2_services(
        tmp_path,
        id_factory=ids,
        timestamp_factory=lambda: "2026-08-18T00:00:00+00:00",
    )
    project_ref, original_ref, baseline = _records(services)
    services.baselines.register(project_ref, original_ref, (baseline,))
    context = RequestContext("gui", run_id="catalog")
    assert services.gui_project_commands.switch_v2(project_ref, original_ref, context).is_success

    created = services.gui_project_commands.create_variant("空白版", context)
    created_id = str(created.value["id"])
    changed = services.gui_project_commands.update_entry("entry-a", context, translation="copied")
    saved = services.gui_project_commands.save(context)
    copied = services.gui_project_commands.create_variant("复制版", context, copy_active=True)
    copied_id = str(copied.value["id"])
    deleted = services.gui_project_commands.delete_variant(created_id, context)
    projection = services.project_projection.snapshot().to_dict()["values"]

    assert created.is_success
    assert changed.is_success and saved.is_success and copied.is_success and deleted.is_success
    assert projection["project_name"] == "Project A"
    assert [(item["id"], item["name"]) for item in projection["variants"]] == [
        (original_ref.identity.value, "默认"),
        (copied_id, "复制版"),
    ]
    assert projection["active_variant_id"] == copied_id
    assert projection["entries"][0]["translation"] == "copied"
    assert not Path(services.variants.path_for(VariantRef(VariantId(created_id), project_ref.identity))).exists()


def test_session_gui_commands_publish_only_committed_full_conversation(tmp_path: Path) -> None:
    ids = _Ids()
    services = build_persistence_v2_services(
        tmp_path / "projects-v2",
        id_factory=ids,
        timestamp_factory=lambda: "2026-08-18T00:00:00+00:00",
    )
    context = RequestContext("gui", run_id="run")
    created = services.gui_session_commands.create_and_activate("Session A", context)
    session_id = created.value["session_id"]
    saved = services.gui_session_commands.save_conversation(
        services.session_lifecycle.active.aggregate.ref,
        [{"role": "user", "content": "visible"}],
        [{"role": "user", "content": "backend"}],
        context,
    )
    projection = services.session_projection.snapshot()

    assert created.is_success
    assert saved.is_success
    assert projection is not None and not projection.dirty
    assert projection.to_dict()["values"]["session_id"] == session_id
    assert projection.to_dict()["values"]["backend_history"][0]["content"] == "backend"

    services.close()
    restarted = build_persistence_v2_services(
        tmp_path / "projects-v2",
        id_factory=_Ids(),
        timestamp_factory=lambda: "2026-08-18T00:01:00+00:00",
    )
    catalog = restarted.gui_session_commands.list_sessions()
    restored = restarted.gui_session_commands.switch(
        services.session_lifecycle.active.aggregate.ref,
        context,
    )

    assert catalog[0]["session_id"] == session_id
    assert catalog[0]["message_count"] == 1
    assert restored.is_success
    restored_projection = restarted.session_projection.snapshot()
    assert restored_projection.to_dict()["values"]["backend_history"][0]["content"] == "backend"


def test_runtime_registers_real_v2_services_and_releases_projection_subscriptions(tmp_path: Path) -> None:
    root = tmp_path / "projects-v2"
    runtime = build_runtime({"persistence_v2_root": str(root)})
    services = runtime.use_cases.resolve("persistence_v2")
    subscription = services.project_projection.subscribe(lambda snapshot: None)

    result = runtime.close()

    assert services.root == str(root.resolve())
    assert result.is_success
    assert services.project_projection.listener_count == 0
    assert subscription.closed


def test_current_project_file_opens_but_variant_file_is_rejected(tmp_path: Path, monkeypatch) -> None:
    services = build_persistence_v2_services(
        tmp_path,
        id_factory=_Ids(),
        timestamp_factory=lambda: "2026-08-18T00:00:00+00:00",
    )
    project_ref, variant_ref, _baseline = _records(services)
    opener = CurrentProjectOpener(
        str(tmp_path),
        services.projects,
        services.variants,
        services.baselines,
        services.gui_project_commands,
    )
    context = RequestContext("gui", run_id="open")

    prepared = opener.prepare_path(services.projects.path_for(project_ref), context)

    assert prepared.is_success
    assert services.project_lifecycle.active is None

    project_saves: list[object] = []
    variant_saves: list[object] = []
    monkeypatch.setattr(services.projects, "save", lambda *args: project_saves.append(args))
    monkeypatch.setattr(services.variants, "save", lambda *args: variant_saves.append(args))

    opened = opener.activate(prepared.value, context)
    rejected = opener.prepare_path(services.variants.path_for(variant_ref), context)

    assert opened.is_success
    assert opened.value["name"] == "Project A"
    assert services.project_lifecycle.active is not None
    assert project_saves == []
    assert variant_saves == []
    assert rejected.diagnostics[0].code == "PROJECT_RECORD_REQUIRED"
    assert "*.json" in PROJECT_FILE_FILTER

    (tmp_path / "active-project.json").write_text(
        '{"schema_version":1,"project_id":"project-a","variant_id":"variant-a","source_ref":null}',
        encoding="utf-8",
    )
    active_prepared = opener.prepare_active(RequestContext("gui", run_id="restore"))
    assert active_prepared.is_success


def test_default_runtime_uses_unversioned_data_root(tmp_path: Path, monkeypatch) -> None:
    import transbridge.bootstrap.composition as composition

    monkeypatch.setattr(composition, "get_data_dir", lambda: tmp_path)
    runtime = build_runtime()
    services = runtime.use_cases.resolve("persistence_v2")

    assert services.root == str(tmp_path.resolve())
    assert runtime.use_cases.resolve("current_project_opener") is services.current_project_opener
    runtime.close()
