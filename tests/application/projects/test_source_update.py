import base64
from dataclasses import replace
from itertools import count
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from transbridge.application.contracts import RequestContext
from transbridge.application.io import FormatId
from transbridge.application.projects import (
    DirtyDecision,
    ProjectProvisioningRequest,
    ProjectSourceRequest,
    TransitionTarget,
)
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.persistence.v2.variant import VariantSnapshot


def _xml(path, rows):
    template = Path("tests/contracts/io/fixtures/eet-small.xml").read_text(encoding="utf-8")
    row = template[template.index("  <ESP>") : template.index("</DocumentElement>")]
    items = [
        row
        .replace("00000001", f"{index:08X}")
        .replace("GreetingTopic", f"Topic{index}")
        .replace("Hello, traveler.", original)
        for index, original in rows
    ]
    path.write_text("<DocumentElement>\n" + "".join(items) + "</DocumentElement>", encoding="utf-8")


@pytest.fixture
def project(tmp_path):
    source = tmp_path / "old.xml"
    _xml(source, [(1, "One"), (2, "Two"), (3, "Three")])
    ids = count()
    services = build_persistence_v2_services(
        tmp_path / "data", id_factory=lambda: str(next(ids)), timestamp_factory=lambda: "2026-10-01T00:00:00+00:00"
    )
    context = RequestContext("source-test", run_id="update")
    result = services.gui_project_commands.create_project(
        ProjectProvisioningRequest("Update", source=ProjectSourceRequest(str(source), FormatId.XML_EET)), context
    )
    assert result.is_success, result.diagnostics
    active = services.project_lifecycle.active
    for entry in active.variant.snapshot().entries:
        assert services.gui_project_commands.update_entry(
            entry.entry_key, context, translation="译文", stage=5
        ).is_success
    assert services.gui_project_commands.save(context).is_success
    path = services.projects.path_for(active.project_ref)
    service = services.project_source_updates
    targets = service.list_sources(path, context)
    assert targets.is_success, targets.diagnostics
    yield services, service, context, source, path, targets.value[0].source_id
    services.close()


def _prepare(project, rows=None, *, overwrite=False):
    services, service, context, source, path, source_id = project
    replacement = source if overwrite else source.with_name("new.xml")
    _xml(replacement, rows or [(2, "Two"), (1, "One changed"), (4, "Four")])
    result = service.prepare(path, source_id, str(replacement), context)
    assert result.is_success, result.diagnostics
    return result.value, replacement


def test_update_migrates_all_variants_and_reopens_with_new_source(project):
    services, service, context, source, path, _ = project
    assert services.gui_project_commands.create_variant("第二版", context, copy_active=True).is_success
    assert services.gui_project_commands.save(context).is_success
    preview, replacement = _prepare(project)
    assert (preview.added, preview.removed, preview.changed, preview.unchanged, preview.unverified) == (1, 1, 1, 1, 0)
    assert preview.variant_count == 2
    assert preview.reordered == 2
    result = service.commit(preview.token, context)
    assert result.is_success, result.diagnostics
    assert Path(result.value.backup_path).exists()
    assert not services.project_lifecycle.active.dirty
    active = services.project_lifecycle.active
    assert services.gui_project_commands.save(context).is_success
    opened = services.current_project_opener.open_path(path, context)
    assert opened.is_success, opened.diagnostics
    assert "recovery" not in opened.value
    project_data = services.projects.read_snapshot(active.project_ref).envelope.data
    assert project_data["sources"][0]["location"] == str(replacement.resolve())
    from transbridge.persistence.v2.ids import VariantId, VariantRef

    for identity in project_data["variant_ids"]:
        snapshot = VariantSnapshot.from_dto(
            services.variants.read_snapshot(VariantRef(VariantId(identity), active.project_ref.identity))
        )
        entries = {entry.entry_key.local_key: entry for entry in snapshot.entries}
        translated = [entry for entry in entries.values() if entry.translation == "译文"]
        assert sorted(entry.stage.value for entry in translated) == [2, 5]
        assert len(entries) == 3
    hydrated = opened.value["hydrations"][0].entries
    assert [entry.original for entry in hydrated] == ["Two", "One changed", "Four"]


def test_overwritten_source_preserves_translations_but_requires_review(project):
    services, service, context, _, path, _ = project
    preview, _ = _prepare(project, [(3, "Three"), (2, "Two"), (1, "One")], overwrite=True)
    assert preview.unverified == 3 and preview.warnings
    result = service.commit(preview.token, context)
    assert result.is_success, result.diagnostics
    assert all(
        entry.translation == "译文" and entry.stage.value == 2
        for entry in services.project_lifecycle.active.variant.snapshot().entries
    )
    assert "recovery" not in services.current_project_opener.open_path(path, context).value


def test_pure_reordering_preserves_reviewed_state_and_produces_new_order(project):
    services, service, context, _, path, _ = project
    preview, _ = _prepare(project, [(3, "Three"), (2, "Two"), (1, "One")])
    assert preview.changed == preview.unverified == 0
    assert preview.reordered == 2
    result = service.commit(preview.token, context)
    assert result.is_success, result.diagnostics
    opened = services.current_project_opener.open_path(path, context)
    assert [item.original for item in opened.value["hydrations"][0].entries] == ["Three", "Two", "One"]
    assert all(entry.stage.value == 5 for entry in services.project_lifecycle.active.variant.snapshot().entries)


def test_reordered_source_projects_into_context_only_remote_plan(project):
    from transbridge.application.io.paratranz_context_order import format_ordered_context
    from transbridge.application.ports.paratranz import ParaTranzEntry
    from transbridge.application.sync import SyncOperation, SyncPlanner
    from transbridge.paratranz.sync_snapshot import _snapshot
    from transbridge.ui.operations.production_support import local_snapshots
    from transbridge.ui.source_hydration import apply_variant_projection, collection_from_hydration

    services, service, context, _, path, _ = project

    def local():
        opened = services.current_project_opener.open_path(path, context)
        collection = collection_from_hydration(opened.value["hydrations"][0])
        states = tuple(entry.to_dict() for entry in services.project_lifecycle.active.variant.snapshot().entries)
        return local_snapshots(SimpleNamespace(collection=apply_variant_projection(collection, states)), 7)

    before = local()
    wire = tuple(
        ParaTranzEntry(
            index + 1,
            entry.entry_key.local_key,
            entry.original,
            entry.translation,
            format_ordered_context(entry.context, entry.context_order),
            entry.stage,
        )
        for index, entry in enumerate(before)
    )
    preview, _ = _prepare(project, [(3, "Three"), (2, "Two"), (1, "One")])
    assert service.commit(preview.token, context).is_success
    after = local()
    remote = tuple(_snapshot(entry, after[0].entry_key.namespace, "project:7") for entry in wire)
    plan = SyncPlanner().plan(after, remote, operation=SyncOperation.UPLOAD)
    assert [item.reason for item in plan.items].count("context_order_changed") == 2
    assert [item.reason for item in plan.items].count("unchanged") == 1
    assert plan.requires_confirmation


@pytest.mark.parametrize("change", ["source", "edit", "variant_disk", "project_disk"])
def test_stale_preview_rejected_without_source_registration_change(project, change):
    services, service, context, _, path, _ = project
    preview, replacement = _prepare(project)
    active = services.project_lifecycle.active
    if change == "source":
        replacement.write_text(replacement.read_text() + "\n")
    elif change == "edit":
        key = active.variant.snapshot().entries[0].entry_key
        assert services.gui_project_commands.update_entry(key, context, translation="new edit").is_success
    else:
        target = Path(path if change == "project_disk" else services.variants.path_for(active.formal_variant_ref))
        target.write_bytes(target.read_bytes() + b"\n")
    before = Path(path).read_bytes()
    result = service.commit(preview.token, context)
    assert not result.is_success
    assert Path(path).read_bytes() == before


def test_owner_cancel_and_duplicate_confirmation(project):
    _, service, context, _, _, _ = project
    preview, _ = _prepare(project)
    assert not service.commit(preview.token, replace(context, owner_id="other")).is_success
    assert service.commit(preview.token, context).is_success
    assert not service.commit(preview.token, context).is_success
    preview, _ = _prepare(project, [(1, "One")])
    service.discard(preview.token, context)
    assert not service.commit(preview.token, context).is_success


def test_recovery_update_without_activating_target(project):
    services, service, context, source, path, source_id = project
    closing = services.project_lifecycle.prepare_transition(
        TransitionTarget(None), context, dirty_decision=DirtyDecision.DISCARD
    )
    assert closing.is_success
    assert services.project_lifecycle.commit_transition(closing.value["token"], context).is_success
    preview, _ = _prepare(project, overwrite=True)
    result = service.commit(preview.token, context)
    assert result.is_success, result.diagnostics
    assert services.project_lifecycle.active is None
    assert "recovery" not in services.current_project_opener.open_path(path, context).value


def test_removed_entries_are_in_durable_backup(project):
    _, service, context, _, _, _ = project
    preview, _ = _prepare(project, [(1, "One")])
    result = service.commit(preview.token, context)
    assert result.is_success, result.diagnostics
    backup = json.loads(Path(result.value.backup_path).read_bytes())
    previous = [
        json.loads(base64.b64decode(item["previous"])) for item in backup["records"] if item["kind"] == "variant"
    ]
    assert len(previous[0]["data"]["entries"]) == 3
    assert all(item["translation"] == "译文" for item in previous[0]["data"]["entries"])


def test_xtranslator_update_survives_runtime_restart(tmp_path):
    source, replacement = tmp_path / "old.xml", tmp_path / "new.xml"
    raw = Path("tests/contracts/io/fixtures/xt-small.xml").read_text(encoding="utf-8")
    source.write_text(raw, encoding="utf-8")
    replacement.write_text(raw.replace("Hello, traveler.", "Hello again."), encoding="utf-8")
    ids = count()

    def runtime():
        return build_persistence_v2_services(
            tmp_path / "data", id_factory=lambda: str(next(ids)), timestamp_factory=lambda: "2026-10-01T00:00:00+00:00"
        )

    services = runtime()
    context = RequestContext("xt-update", run_id="xt-update")
    try:
        created = services.gui_project_commands.create_project(
            ProjectProvisioningRequest("XT", source=ProjectSourceRequest(str(source), FormatId.XML_XT)), context
        )
        assert created.is_success, created.diagnostics
        active = services.project_lifecycle.active
        key = active.variant.snapshot().entries[0].entry_key
        assert services.gui_project_commands.update_entry(key, context, translation="旧译文", stage=5).is_success
        assert services.gui_project_commands.save(context).is_success
        path = services.projects.path_for(active.project_ref)
        target = services.project_source_updates.list_sources(path, context).value[0]
        prepared = services.project_source_updates.prepare(path, target.source_id, str(replacement), context)
        assert prepared.is_success, prepared.diagnostics
        assert prepared.value.changed == 1
        assert services.project_source_updates.commit(prepared.value.token, context).is_success
    finally:
        services.close()
    restarted = runtime()
    try:
        opened = restarted.current_project_opener.open_path(path, context)
        assert opened.is_success, opened.diagnostics
        assert "recovery" not in opened.value
        assert opened.value["hydrations"][0].entries[0].original == "Hello again."
        entry = restarted.project_lifecycle.active.variant.snapshot().entries[0]
        assert entry.translation == "旧译文" and entry.stage.value == 2
    finally:
        restarted.close()
