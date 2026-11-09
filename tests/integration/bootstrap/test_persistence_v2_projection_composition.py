from __future__ import annotations

from contextlib import closing
from pathlib import Path

from tests.plugin_fixtures import write_plugin
from transbridge.application.contracts import RequestContext
from transbridge.application.io.identity import ExternalEntryRef
from transbridge.application.projects import EntryStatePatch, ProjectProvisioningRequest, ProjectSourceRequest
from transbridge.bootstrap import build_runtime
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.persistence.project_store.repository import ProjectStore
from transbridge.persistence.v2 import VariantId, VariantRef, VariantSnapshot


class _Ids:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> str:
        self.value += 1
        return f"id-{self.value}"


def _services(root):
    return build_persistence_v2_services(root, id_factory=_Ids(), timestamp_factory=lambda: "2026-08-18T00:00:00+00:00")


def _project_with_source(services, tmp_path, context):
    source = write_plugin(tmp_path / "source.esp", [(0x800, "Greeter", "Original")])
    result = services.gui_project_commands.create_project(
        ProjectProvisioningRequest("Project A", source=ProjectSourceRequest(str(source))), context
    )
    assert result.is_success, result.diagnostics
    active = services.project_lifecycle.active
    (entry,) = active.variant.snapshot().entries
    return active.project_ref, active.formal_variant_ref, entry.entry_key


def test_legacy_project_with_missing_source_is_rejected_before_source_repair(tmp_path: Path) -> None:
    root = tmp_path / "data"
    legacy = tmp_path / "legacy-project.json"
    old = b'{"schema_version":2,"entity_type":"project","id":"old","data":{"sources":[{"location":"missing.esp"}]}}'
    legacy.write_bytes(old)
    context = RequestContext("gui", run_id="reject-legacy")

    with closing(_services(root)) as services:
        _project_with_source(services, tmp_path, context)
        before = services.project_lifecycle.active
        projection = services.project_projection.snapshot()
        rejected = services.current_project_opener.open_path(legacy, context)

        assert not rejected.is_success
        assert rejected.diagnostics[0].code == "PROJECT_FORMAT_UNSUPPORTED"
        assert services.project_lifecycle.active is before
        assert services.project_projection.snapshot() is projection

    assert legacy.read_bytes() == old
    assert not (root / "source_artifacts").exists()


def test_variant_gui_commands_make_dirty_from_revision_then_save_clears_it(tmp_path: Path) -> None:
    context = RequestContext("gui", run_id="run")
    with closing(_services(tmp_path / "data")) as services:
        _project_ref, _variant_ref, entry_key = _project_with_source(services, tmp_path, context)

        changed = services.gui_project_commands.update_entry(entry_key, context, translation="new")
        dirty = services.project_projection.snapshot()
        saved = services.gui_project_commands.save(context)
        clean = services.project_projection.snapshot()

        assert changed.is_success
        assert dirty is not None and dirty.dirty
        assert saved.is_success
        assert clean is not None and not clean.dirty
        assert clean.to_dict()["values"]["entries"][0]["translation"] == "new"


def test_variant_external_reference_survives_save_and_process_restart(tmp_path: Path) -> None:
    root = tmp_path / "data"
    context = RequestContext("gui", run_id="external-ref")
    reference = ExternalEntryRef("paratranz", "project:42", 71)
    with closing(_services(root)) as services:
        _project_ref, variant_ref, entry_key = _project_with_source(services, tmp_path, context)
        active = services.project_lifecycle.active

        committed = services.gui_project_commands.replace_entry_records(
            {entry_key: EntryStatePatch("remote", 1, (reference,))},
            context,
            expected_project_revision=active.project.envelope.revision,
            expected_variant_revision=active.variant.revision,
        )
        assert committed.is_success, committed.diagnostics
        assert services.gui_project_commands.save(context).is_success

    with closing(_services(root)) as restarted:
        loaded = restarted.variants.load(variant_ref)
        restored = VariantSnapshot.from_dto(loaded.value, variant_ref)
        assert restored.entries[0].external_refs == (reference,)


def test_add_remove_source_survives_save_and_process_restart(tmp_path: Path) -> None:
    root = tmp_path / "data"
    source = write_plugin(tmp_path / "NewPlugin.esp", [(0x800, "Greeter", "New greeting")])
    context = RequestContext("gui", run_id="source-restart")
    with closing(_services(root)) as services:
        commands = services.gui_project_commands
        created = commands.create_project(ProjectProvisioningRequest("Source restart"), context)
        assert created.is_success, created.diagnostics
        project_ref = services.project_lifecycle.active.project_ref
        project_path = services.projects.path_for(project_ref)

        added = commands.add_source(ProjectSourceRequest(str(source)), context)
        assert added.is_success, added.diagnostics
        (entry,) = services.project_lifecycle.active.variant.snapshot().entries
        changed = commands.update_entry(entry.entry_key, context, translation="导入的新译文")
        assert changed.is_success, changed.diagnostics
        assert commands.save(context).is_success

    source.unlink()
    with closing(_services(root)) as restarted:
        reopened = restarted.current_project_opener.open_path(project_path, context)
        assert reopened.is_success, reopened.diagnostics
        restored = restarted.project_projection.snapshot().to_dict()["values"]
        (source_record,) = restored["sources"]
        assert source_record["display_name"] == source.name
        assert str(source) not in str(source_record)
        assert [(item["entry_key"], item["translation"]) for item in restored["entries"]] == [
            (entry.entry_key.to_dict(), "导入的新译文")
        ]

        removed = restarted.gui_project_commands.remove_source(added.value.source_id, context)
        assert removed.is_success, removed.diagnostics
        assert restarted.gui_project_commands.save(context).is_success

    with closing(_services(root)) as reopened_again:
        assert reopened_again.current_project_opener.open_path(project_path, context).is_success
        final = reopened_again.project_projection.snapshot().to_dict()["values"]
        assert final["sources"] == []
        assert final["entries"] == []


def test_variant_catalog_create_copy_switch_and_delete(tmp_path: Path) -> None:
    context = RequestContext("gui", run_id="catalog")
    with closing(_services(tmp_path / "data")) as services:
        project_ref, original_ref, entry_key = _project_with_source(services, tmp_path, context)
        commands = services.gui_project_commands

        created = commands.create_variant("空白版", context)
        assert created.is_success, created.diagnostics
        created_id = VariantId(str(created.value["id"]))
        changed = commands.update_entry(entry_key, context, translation="copied")
        assert changed.is_success, changed.diagnostics
        assert commands.save(context).is_success
        copied = commands.create_variant("复制版", context, copy_active=True)
        assert copied.is_success, copied.diagnostics
        copied_id = str(copied.value["id"])
        deleted = commands.delete_variant(created_id.value, context)
        assert deleted.is_success, deleted.diagnostics
        projection = services.project_projection.snapshot().to_dict()["values"]

        assert projection["project_name"] == "Project A"
        assert [(item["id"], item["name"]) for item in projection["variants"]] == [
            (original_ref.identity.value, "默认"),
            (copied_id, "复制版"),
        ]
        assert projection["active_variant_id"] == copied_id
        assert projection["entries"][0]["translation"] == "copied"
        assert not services.variants.exists(VariantRef(created_id, project_ref.identity))
        project = ProjectStore(services.projects.path_for(project_ref))
        assert [item.identity.value for item in project.summary().variants] == [original_ref.identity.value, copied_id]


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


def test_current_project_file_opens_but_legacy_variant_file_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "data"
    context = RequestContext("gui", run_id="open")
    with closing(_services(root)) as services:
        project_ref, variant_ref, _entry_key = _project_with_source(services, tmp_path, context)
        database = Path(services.projects.path_for(project_ref))
    legacy_variant = tmp_path / "variant.json"
    legacy_variant.write_bytes(b'{"schema_version":2,"entity_type":"variant"}')
    before = database.read_bytes()

    with closing(_services(root)) as services:
        opener = services.current_project_opener
        prepared = opener.prepare_path(database, context)
        assert prepared.is_success, prepared.diagnostics
        assert services.project_lifecycle.active is None
        assert database.read_bytes() == before

        opened = opener.activate(prepared.value, context)
        assert opened.is_success, opened.diagnostics
        assert opened.value["name"] == "Project A"
        assert opened.value["variant_id"] == variant_ref.identity.value
        assert services.project_lifecycle.active is not None
        assert database.read_bytes() == before
        rejected = opener.prepare_path(legacy_variant, context)
        assert not rejected.is_success
        assert rejected.diagnostics[0].code == "PROJECT_FORMAT_UNSUPPORTED"

        active_prepared = opener.prepare_active(RequestContext("gui", run_id="restore"))
        assert active_prepared.is_success, active_prepared.diagnostics
        assert active_prepared.value.project_ref == project_ref


def test_default_runtime_uses_unversioned_data_root(tmp_path: Path, monkeypatch) -> None:
    import transbridge.bootstrap.composition as composition

    monkeypatch.setattr(composition, "get_data_dir", lambda: tmp_path)
    runtime = build_runtime()
    services = runtime.use_cases.resolve("persistence_v2")

    assert services.root == str(tmp_path.resolve())
    assert runtime.use_cases.resolve("current_project_opener") is services.current_project_opener
    runtime.close()
