from copy import deepcopy
from dataclasses import replace
from itertools import count
import struct

import pytest

from transbridge.application.contracts import RequestContext
from transbridge.application.io import FormatId, Stage
from transbridge.application.projects import ProjectProvisioningRequest, ProjectSourceRequest
from transbridge.application.projects.source_content import source_content_identity
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.persistence.v2.ids import VariantId, VariantRef
from transbridge.persistence.v2.models import ProjectDto
from transbridge.persistence.v2.variant import VariantSnapshot


def _field(kind, value):
    return struct.pack("<4sH", kind.encode(), len(value)) + value


def _record(kind, identifier, value, flags=0):
    return struct.pack("<4sIIIIHH", kind.encode(), len(value), flags, identifier, 0, 44, 0) + value


def _plugin(path, rows, *, localized=False):
    records = b"".join(
        _record(
            "WEAP",
            identifier,
            _field("EDID", f"Weapon{identifier}".encode() + b"\0")
            + _field("FULL", struct.pack("<I", text) if localized else text.encode() + b"\0"),
        )
        for identifier, text in rows
    )
    path.write_bytes(
        _record("TES4", 0, _field("HEDR", struct.pack("<fII", 1.7, len(rows), 0x900)), 0x80 if localized else 0)
        + struct.pack("<4sI4sIHHHH", b"GRUP", len(records) + 24, b"WEAP", 0, 0, 0, 0, 0)
        + records
    )


def _strings(path, text):
    path.parent.mkdir(exist_ok=True)
    payload = text.encode() + b"\0"
    path.write_bytes(struct.pack("<IIII", 1, len(payload), 1, 0) + payload)


@pytest.fixture
def services(tmp_path):
    ids = count()
    result = build_persistence_v2_services(
        tmp_path / "data",
        id_factory=lambda: str(next(ids)),
        timestamp_factory=lambda: "2026-10-01T00:00:00+00:00",
    )
    yield result
    result.close()


def _create(services, source, *, translated=None):
    context = RequestContext("plugin-update-test", run_id="source-update")
    migrations = () if translated is None else (ProjectSourceRequest(str(translated), FormatId.PLUGIN_SSE),)
    created = services.gui_project_commands.create_project(
        ProjectProvisioningRequest(
            "Plugin update", source=ProjectSourceRequest(str(source), FormatId.PLUGIN_SSE), migration_sources=migrations
        ),
        context,
    )
    assert created.is_success, created.diagnostics
    active = services.project_lifecycle.active
    return context, services.projects.path_for(active.project_ref)


def _preview(services, path, replacement, context):
    targets = services.project_source_updates.list_sources(path, context)
    assert targets.is_success, targets.diagnostics
    assert len(targets.value) == 1
    result = services.project_source_updates.prepare(path, targets.value[0].source_id, str(replacement), context)
    assert result.is_success, result.diagnostics
    return result.value


def test_original_qualified_source_update_preserves_exact_text_only(services, tmp_path):
    source, replacement = tmp_path / "old-conflict.esp", tmp_path / "new-conflict.esp"
    _plugin(source, [(0x800, "First"), (0x800, "Second")])
    context, path = _create(services, source)
    for item in services.project_lifecycle.active.variant.snapshot().entries:
        assert services.gui_project_commands.update_entry(
            item.entry_key, context, translation=f"Translated {item.entry_key.original}", stage=1
        ).is_success
    assert services.gui_project_commands.save(context).is_success
    _plugin(replacement, [(0x800, "Second"), (0x800, "New first")])

    preview = _preview(services, path, replacement, context)

    assert (preview.added, preview.removed, preview.unchanged) == (1, 1, 1)
    result = services.project_source_updates.commit(preview.token, context)
    assert result.is_success, result.diagnostics
    entries = {
        entry.entry_key.original: entry for entry in services.project_lifecycle.active.variant.snapshot().entries
    }
    assert entries["Second"].translation == "Translated Second"
    assert entries["New first"].translation == ""


def test_real_plugin_updates_all_variants_and_preserves_reordered_reviewed_entries(services, tmp_path):
    source, replacement = tmp_path / "old.esp", tmp_path / "new.esp"
    _plugin(source, [(0x800, "One"), (0x801, "Two"), (0x802, "Three")])
    context, path = _create(services, source)
    active = services.project_lifecycle.active
    old_namespace = active.variant.snapshot().source_fingerprints[0].namespace
    for item in active.variant.snapshot().entries:
        assert services.gui_project_commands.update_entry(
            item.entry_key, context, translation="译文", stage=Stage.REVIEWED.value
        ).is_success
    assert services.gui_project_commands.save(context).is_success
    assert services.gui_project_commands.create_variant("Second", context, copy_active=True).is_success
    assert services.gui_project_commands.save(context).is_success
    _plugin(replacement, [(0x801, "Two"), (0x800, "One changed"), (0x803, "Four")])
    preview = _preview(services, path, replacement, context)
    assert (preview.added, preview.removed, preview.changed, preview.unchanged, preview.reordered) == (1, 1, 1, 1, 2)
    assert preview.variant_count == 2
    committed = services.project_source_updates.commit(preview.token, context)
    assert committed.is_success, committed.diagnostics
    assert services.gui_project_commands.save(context).is_success
    opened = services.current_project_opener.open_path(path, context)
    assert opened.is_success, opened.diagnostics
    assert "recovery" not in opened.value
    assert [item.original for item in opened.value["hydrations"][0].entries] == ["Two", "One changed", "Four"]
    active = services.project_lifecycle.active
    for identity in active.project.envelope.data["variant_ids"]:
        ref = VariantRef(VariantId(identity), active.project_ref.identity)
        snapshot = VariantSnapshot.from_dto(services.variants.read_snapshot(ref), ref)
        assert snapshot.source_fingerprints[0].namespace != old_namespace
        translated = [item for item in snapshot.entries if item.translation == "译文"]
        assert sorted(item.stage.value for item in translated) == [Stage.QUESTIONABLE.value, Stage.REVIEWED.value]
        assert len(snapshot.entries) == 3


@pytest.mark.parametrize("remove_target", ["primary", "migration"])
@pytest.mark.parametrize("legacy_unmarked_fold", [False, True])
def test_folded_plugin_pair_keeps_registration_scope_and_reopens_after_namespace_change(
    services, tmp_path, remove_target, legacy_unmarked_fold
):
    source, translated, replacement = (tmp_path / name for name in ("old.esp", "translated.esp", "new.esp"))
    _plugin(source, [(0x800, "One")])
    _plugin(translated, [(0x800, "Existing translation")])
    context, path = _create(services, source, translated=translated)
    active = services.project_lifecycle.active
    old_data = deepcopy(active.project.envelope.data)
    if legacy_unmarked_fold:
        for relation in old_data["source_relations"]:
            relation.pop("folded_into_baseline", None)
    primary = next(item for item in old_data["sources"] if item["legacy"]["role"] == "primary")
    primary["plugin_scope"] = "my-mod"
    scoped = ProjectDto(replace(active.project.envelope, data=old_data, revision=active.project.envelope.revision + 1))
    scoped_result = services.project_lifecycle.commit_project_update(scoped, active.project.envelope.revision, context)
    assert scoped_result.is_success, scoped_result.diagnostics
    old_namespace = source_content_identity(primary)
    _plugin(replacement, [(0x800, "One"), (0x801, "Two")])
    preview = _preview(services, path, replacement, context)
    committed = services.project_source_updates.commit(preview.token, context)
    assert committed.is_success, committed.diagnostics
    # A retained translated import must not be reopened as a second baseline.
    translated.unlink()
    opened = services.current_project_opener.open_path(path, context)
    assert opened.is_success, opened.diagnostics
    assert "recovery" not in opened.value
    assert len(opened.value["hydrations"]) == 1
    active = services.project_lifecycle.active
    data = active.project.envelope.data
    assert data["source_relations"] == [
        {**relation, "folded_into_baseline": True} for relation in old_data["source_relations"]
    ]
    current_primary = next(item for item in data["sources"] if item["legacy"]["role"] == "primary")
    assert current_primary["source_id"] == primary["source_id"]
    assert current_primary["plugin_scope"] == "my-mod"
    current_translation = next(item for item in data["sources"] if item["legacy"]["role"] == "migration")
    previous_translation = next(item for item in old_data["sources"] if item["legacy"]["role"] == "migration")
    assert source_content_identity(current_primary) != old_namespace
    assert current_translation == {
        **previous_translation,
        "location": "",
        "display_name": translated.name,
        "legacy": {**previous_translation["legacy"], "path": ""},
    }
    assert any(item.translation == "Existing translation" for item in active.variant.snapshot().entries)
    # Archive all original inputs from their captured artifacts after the fold
    # target moves to a new namespace and its live inputs disappear.
    source.unlink()
    replacement.unlink()
    bundle = tmp_path / "updated.zip"
    services.project_archive.export_project(str(bundle), context)
    assert bundle.is_file()
    removed = services.gui_project_commands.remove_source(
        current_primary["source_id"] if remove_target == "primary" else current_translation["source_id"], context
    )
    assert removed.is_success, removed.diagnostics
    remaining = services.project_lifecycle.active.project.envelope.data["sources"]
    assert remaining == ([] if remove_target == "primary" else [current_primary])


def test_overwritten_localized_strings_compare_saved_companion_despite_unchanged_plugin_hash(services, tmp_path):
    source = tmp_path / "localized.esp"
    strings = tmp_path / "Strings" / "localized_English.strings"
    _plugin(source, [(0x800, 1)], localized=True)
    _strings(strings, "Old original")
    context, path = _create(services, source)
    active = services.project_lifecycle.active
    item = active.variant.snapshot().entries[0]
    assert services.gui_project_commands.update_entry(
        item.entry_key, context, translation="译文", stage=Stage.REVIEWED.value
    ).is_success
    assert services.gui_project_commands.save(context).is_success
    previous_plugin = source.read_bytes()
    old_artifact_id = active.project.envelope.data["sources"][0]["source_artifact_id"]
    _strings(strings, "Changed original")
    preview = _preview(services, path, source, context)
    assert source.read_bytes() == previous_plugin
    assert preview.unverified == 0 and preview.changed == 1 and not preview.warnings
    committed = services.project_source_updates.commit(preview.token, context)
    assert committed.is_success, committed.diagnostics
    updated_source = services.project_lifecycle.active.project.envelope.data["sources"][0]
    assert updated_source["source_artifact_id"] != old_artifact_id
    opened = services.current_project_opener.open_path(path, context)
    assert opened.is_success, opened.diagnostics
    assert "recovery" not in opened.value
    assert opened.value["hydrations"][0].entries[0].original == "Changed original"
    item = services.project_lifecycle.active.variant.snapshot().entries[0]
    assert item.translation == "译文" and item.stage is Stage.QUESTIONABLE


def test_independent_migration_plugin_relation_does_not_fold_an_unmerged_baseline(services, tmp_path):
    source, translated = tmp_path / "primary.esp", tmp_path / "independent.esp"
    _plugin(source, [(0x800, "One")])
    _plugin(translated, [(0x801, "Other")])
    context, path = _create(services, source, translated=translated)
    active = services.project_lifecycle.active
    assert len(active.variant.snapshot().source_fingerprints) == 2
    assert not active.project.envelope.data["source_relations"][0].get("folded_into_baseline", False)
    prepared = services.current_project_opener.prepare_path(path, context)
    assert prepared.is_success, prepared.diagnostics
    assert prepared.value.recovery is None
    assert len(prepared.value.baselines) == 2
    assert services.current_project_opener.activate(prepared.value, context).is_success
