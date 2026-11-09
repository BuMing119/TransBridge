from dataclasses import replace
from itertools import count
from pathlib import Path

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
from transbridge.persistence.project_store.connection import project_connection
from transbridge.persistence.project_store.history import ProjectHistory
from transbridge.persistence.project_store.repository import ProjectStore
from transbridge.persistence.v2.ids import VariantRef
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
    history = ProjectHistory(services.projects)
    store = ProjectStore(path)
    assert result.value.backup_path.startswith("project-snapshot:")
    assert not services.project_lifecycle.active.dirty
    active = services.project_lifecycle.active
    assert services.gui_project_commands.save(context).is_success
    source.unlink()
    replacement.unlink()
    opened = services.current_project_opener.open_path(path, context)
    assert opened.is_success, opened.diagnostics
    assert "recovery" not in opened.value
    assert opened.value["sources"][0]["location"] == ""
    for variant in store.summary().variants:
        ref = VariantRef(variant.identity, active.project_ref.identity)
        (backup,) = history.list(ref)
        assert backup.name.startswith("更新来源前")
        snapshot = VariantSnapshot.from_dto(services.variants.read_snapshot(ref), ref)
        entries = {entry.entry_key.local_key: entry for entry in snapshot.entries}
        translated = [entry for entry in entries.values() if entry.translation == "译文"]
        assert sorted(entry.stage.value for entry in translated) == [2, 5]
        assert len(entries) == 3
    hydrated = opened.value["hydrations"][0].entries
    assert [entry.original for entry in hydrated] == ["Two", "One changed", "Four"]


def test_overwritten_source_compares_saved_original_and_preserves_reviewed_state(project):
    services, service, context, _, path, _ = project
    preview, _ = _prepare(project, [(3, "Three"), (2, "Two"), (1, "One")], overwrite=True)
    assert preview.unverified == 0 and not preview.warnings
    result = service.commit(preview.token, context)
    assert result.is_success, result.diagnostics
    assert all(
        entry.translation == "译文" and entry.stage.value == 5
        for entry in services.project_lifecycle.active.variant.snapshot().entries
    )
    assert "recovery" not in services.current_project_opener.open_path(path, context).value


@pytest.mark.parametrize("original_state", ["removed", "overwritten"])
def test_update_uses_saved_originals_and_only_reads_explicit_input(project, monkeypatch, original_state):
    services, service, context, source, path, source_id = project
    if original_state == "removed":
        source.unlink()
    else:
        source.write_bytes(b"historical input contents are irrelevant")
    replacement = source.with_name("replacement.xml")
    _xml(replacement, [(1, "One changed"), (2, "Two"), (3, "Three")])
    requests = []
    prepare = service._preparer.prepare_source

    def prepare_explicit(request, *args, **kwargs):
        requests.append(request.location)
        assert request.location == str(replacement.resolve())
        return prepare(request, *args, **kwargs)

    monkeypatch.setattr(service._preparer, "prepare_source", prepare_explicit)
    result = service.prepare(path, source_id, str(replacement), context)
    assert result.is_success, result.diagnostics
    assert (result.value.changed, result.value.unchanged, result.value.unverified) == (1, 2, 0)
    assert not result.value.warnings
    committed = service.commit(result.value.token, context)
    assert committed.is_success, committed.diagnostics
    assert requests == [str(replacement.resolve())] * 2
    assert {entry.translation for entry in services.project_lifecycle.active.variant.snapshot().entries} == {"译文"}
    assert [entry.stage.value for entry in services.project_lifecycle.active.variant.snapshot().entries] == [2, 5, 5]


def test_update_allows_reused_input_path_but_rejects_duplicate_source_content(project):
    services, service, context, source, path, source_id = project
    other = source.with_name("other.xml")
    _xml(other, [(9, "Independent content")])
    added = services.gui_project_commands.add_source(ProjectSourceRequest(str(other), FormatId.XML_EET), context)
    assert added.is_success, added.diagnostics
    assert services.gui_project_commands.save(context).is_success
    other_original = other.read_bytes()
    _xml(other, [(1, "One changed"), (2, "Two"), (3, "Three")])

    preview = service.prepare(path, source_id, str(other), context)
    assert preview.is_success, preview.diagnostics
    assert preview.value.changed == 1
    committed = service.commit(preview.value.token, context)
    assert committed.is_success, committed.diagnostics
    registrations = services.project_lifecycle.active.project.envelope.data["sources"]
    assert len(registrations) == 2
    assert {item["location"] for item in registrations} == {""}
    assert {item["source_id"] for item in registrations} == {source_id, added.value.source_id}
    assert len({item["fingerprint"] for item in registrations}) == 2
    before = Path(path).read_bytes()
    duplicate = source.with_name("duplicate.xml")
    duplicate.write_bytes(other_original)
    conflict = service.prepare(path, source_id, str(duplicate), context)
    assert not conflict.is_success
    assert conflict.diagnostics[0].code == "INVALID_INPUT"
    assert Path(path).read_bytes() == before


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


def test_reordered_source_projects_into_current_download_snapshot_order(project):
    from transbridge.application.sync.batch_download import snapshots_from_entries
    from transbridge.ui.source_hydration import apply_variant_projection, collection_from_hydration

    services, service, context, _, path, _ = project

    def local():
        opened = services.current_project_opener.open_path(path, context)
        collection = collection_from_hydration(opened.value["hydrations"][0])
        states = tuple(entry.to_dict() for entry in services.project_lifecycle.active.variant.snapshot().entries)
        return snapshots_from_entries(apply_variant_projection(collection, states))

    before = local()
    preview, _ = _prepare(project, [(3, "Three"), (2, "Two"), (1, "One")])
    assert service.commit(preview.token, context).is_success
    after = local()
    assert [entry.entry_key for entry in after] == list(reversed([entry.entry_key for entry in before]))
    assert [entry.context_order for entry in after] == [0, 1, 2]
    assert all(entry.external_ref is None for entry in after)


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
        with project_connection(Path(path), write=True) as database:
            if change == "project_disk":
                database.execute("UPDATE project SET revision = revision + 1")
            else:
                database.execute(
                    "UPDATE variant SET revision = revision + 1 WHERE identity = ?",
                    (active.formal_variant_ref.identity.value,),
                )
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
    services, service, context, _, path, _ = project
    ref = services.project_lifecycle.active.formal_variant_ref
    preview, _ = _prepare(project, [(1, "One")])
    result = service.commit(preview.token, context)
    assert result.is_success, result.diagnostics
    history = ProjectHistory(services.projects)
    (backup,) = history.list(ref)
    assert result.value.backup_path == f"project-snapshot:{backup.identity.rsplit('-', 1)[0]}"
    previous = history.read_page(backup.identity, ref)
    assert [row.snapshot.original for row in previous] == ["One", "Two", "Three"]
    assert all(row.state.translation == "译文" and row.state.stage.value == 5 for row in previous)
    assert [row.snapshot.original for row in ProjectStore(path).read_page(ref.identity)] == ["One"]


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
