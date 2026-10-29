from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.application.projects.test_source_update import _xml, project as source_project  # noqa: F401
from transbridge.application.io import FormatId
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.projects import ProjectSourceRequest
from transbridge.application.projects.source_supplement_mapping import supplement_variants
from transbridge.persistence.project_provisioning import TranslationIoProjectSourcePreparer
from transbridge.persistence.v2.variant import SourceBaseline, SourceFingerprint, VariantSnapshot


@pytest.fixture
def project(source_project):  # noqa: F811 - imported pytest fixture
    return source_project


def _missing_artifact(case):
    services, _, context, _, _, source_id = case
    active = services.project_lifecycle.active
    for source in active.project.envelope.data["sources"]:
        source.pop("source_artifact_id", None)
        source["location"] = ""
    services.projects.save(active.project_ref, active.project)
    return services.project_source_supplements, context, source_id


def _prepare(case, rows):
    service, context, source_id = _missing_artifact(case)
    services, _, _, source, path, _ = case
    replacement = source.with_name("supplement.xml")
    _xml(replacement, rows)
    namespace = services.project_lifecycle.active.variant.snapshot().source_fingerprints[0].namespace.value
    result = service.prepare(path, source_id, namespace, str(replacement), context)
    assert result.is_success, result.diagnostics
    return service, result.value


def test_supplement_keeps_unmatched_translations_and_supplies_actual_input(project):
    services, _, context, original, path, _ = project
    original.write_bytes(b"reference contents must never be reparsed")
    service, preview = _prepare(project, [(2, "Two"), (4, "Four")])
    assert (preview.matched_count, preview.unmatched_count, preview.added_count) == (1, 2, 1)
    result = service.commit(preview.token, context)
    assert result.is_success, result.diagnostics
    saved = services.project_lifecycle.active.variant.snapshot()
    assert sum(row.translation == "译文" for row in saved.entries) == 3
    assert len(saved.entries) == 4
    assert services.project_lifecycle.active.missing_inputs
    assert Path(result.value.backup_path).exists()
    reopened = services.current_project_opener.open_path(path, context)
    assert reopened.is_success and "recovery" not in reopened.value, reopened.diagnostics
    assert services.project_lifecycle.active.variant.snapshot() == saved


def test_user_mapping_can_resolve_legacy_identity_without_dropping_other_rows(project):
    services, _, context, source, path, source_id = project
    active = services.project_lifecycle.active
    snapshot = active.variant.snapshot()
    legacy = SourceNamespace.legacy()
    old_keys = ("old-first", "old-second", "old-third")
    legacy_snapshot = VariantSnapshot(
        snapshot.ref,
        (SourceFingerprint(legacy, None),),
        tuple(
            replace(row, entry_key=EntryKey(legacy, key)) for key, row in zip(old_keys, snapshot.entries, strict=True)
        ),
        snapshot.revision + 1,
        snapshot.label_library,
    )
    services.variants.save(legacy_snapshot.ref, legacy_snapshot.to_dto())
    _missing_artifact(project)
    # Reopening makes the actual persisted nullable identities the edit authority.
    opened = services.current_project_opener.open_path(path, context)
    assert opened.is_success and "recovery" not in opened.value, opened.diagnostics
    replacement = source.with_name("chosen.xml")
    _xml(replacement, [(9, "Chosen original")])
    service = services.project_source_supplements
    result = service.prepare(path, source_id, legacy.value, str(replacement), context)
    assert result.is_success, result.diagnostics
    preview = result.value
    assert preview.matched_count == 0 and preview.unmatched_count == 3
    selected = preview.rows[0]
    mapped = service.remap(preview.token, {selected.old_key: preview.choices[0].key}, context)
    assert mapped.is_success and mapped.value.matched_count == 1
    committed = service.commit(preview.token, context)
    assert committed.is_success, committed.diagnostics
    entries = services.project_lifecycle.active.variant.snapshot().entries
    assert len(entries) == 3 and all(row.translation == "译文" for row in entries)
    assert {row.entry_key for row in entries} == {
        preview.choices[0].key,
        *(row.old_key for row in preview.rows[1:]),
    }


def test_cancel_and_stale_preview_do_not_change_saved_rows(project):
    services, _, context, _, path, _ = project
    service, preview = _prepare(project, [(1, "One"), (2, "Two"), (3, "Three")])
    before = Path(path).read_bytes()
    snapshot = services.project_lifecycle.active.variant.snapshot()
    service.discard(preview.token, context)
    assert not service.commit(preview.token, context).is_success
    assert Path(path).read_bytes() == before
    assert services.project_lifecycle.active.variant.snapshot() == snapshot
    service, preview = _prepare(project, [(1, "One"), (2, "Two"), (3, "Three")])
    entry = snapshot.entries[0]
    assert services.gui_project_commands.update_entry(entry.entry_key, context, translation="新编辑").is_success
    rejected = service.commit(preview.token, context)
    assert not rejected.is_success
    assert Path(path).read_bytes() == before
    assert services.project_lifecycle.active.variant.snapshot().entries[0].translation == "新编辑"


def test_changed_input_after_preview_does_not_publish_or_lose_saved_translations(project):
    services, _, context, _, path, _ = project
    service, preview = _prepare(project, [(1, "One"), (2, "Two"), (3, "Three")])
    before = Path(path).read_bytes()
    snapshot = services.project_lifecycle.active.variant.snapshot()
    _xml(Path(preview.replacement_path), [(1, "Changed after preview")])

    rejected = service.commit(preview.token, context)

    assert not rejected.is_success
    assert Path(path).read_bytes() == before
    assert services.project_lifecycle.active.variant.snapshot() == snapshot


def test_supplied_input_restores_all_variants_and_preserves_saved_stage_and_labels(project):
    services, _, context, _, path, _ = project
    original_rows = services.project_lifecycle.active.variant.snapshot().entries
    assert services.gui_project_commands.replace_labels(
        {row.entry_key: {"保留标签"} for row in original_rows}, {"保留标签": {}}, context
    ).is_success
    assert services.gui_project_commands.save(context).is_success
    assert services.gui_project_commands.create_variant("另一版本", context, copy_active=True).is_success
    assert services.gui_project_commands.save(context).is_success
    service, preview = _prepare(project, [(1, "One"), (2, "Two"), (3, "Three")])
    assert preview.variant_count == 2 and preview.unmatched_count == 0
    result = service.commit(preview.token, context)
    assert result.is_success, result.diagnostics
    assert not services.project_lifecycle.active.missing_inputs
    assert all(
        row.translation == "译文" and row.stage.value == 5 and row.labels == ("保留标签",)
        for row in services.project_lifecycle.active.variant.snapshot().entries
    )
    reopened = services.current_project_opener.open_path(path, context)
    assert reopened.is_success and "recovery" not in reopened.value


def test_unregistered_saved_rows_can_choose_a_file_and_format_then_continue(project):
    services, _, context, source, path, _ = project
    active = services.project_lifecycle.active
    namespace = active.variant.snapshot().source_fingerprints[0].namespace.value
    active.project.envelope.data["sources"] = []
    active.project.envelope.data["source_relations"] = []
    services.projects.save(active.project_ref, active.project)
    reopened = services.current_project_opener.open_path(path, context)
    assert reopened.is_success and "recovery" not in reopened.value
    assert services.project_lifecycle.active.missing_inputs[0].source_id is None
    service = services.project_source_supplements
    prepared = service.prepare(path, None, namespace, str(source), context, format_id=FormatId.XML_EET)
    assert prepared.is_success, prepared.diagnostics
    assert prepared.value.matched_count == 3
    committed = service.commit(prepared.value.token, context)
    assert committed.is_success, committed.diagnostics
    active = services.project_lifecycle.active
    assert len(active.project.envelope.data["sources"]) == 1
    assert len(active.variant.snapshot().entries) == 3
    assert not active.missing_inputs


def test_supplement_does_not_remove_an_unrelated_empty_source_fingerprint(project):
    services = project[0]
    snapshot = services.project_lifecycle.active.variant.snapshot()
    other = SourceFingerprint(SourceNamespace("empty-other-source"), "a" * 64)
    snapshot = replace(snapshot, source_fingerprints=(*snapshot.source_fingerprints, other))
    incoming = SourceFingerprint(SourceNamespace("explicit-new-source"), "b" * 64)
    prepared = SimpleNamespace(baseline=SourceBaseline(incoming, ()))
    result = supplement_variants((snapshot,), prepared, (), snapshot.entries[0].entry_key.namespace.value)[0]
    assert other in result.source_fingerprints
    assert result.entries == snapshot.entries


def test_supplement_partial_source_preserves_its_already_available_originals(project):
    services, _, context, source, path, source_id = project
    active = services.project_lifecycle.active
    saved = active.variant.snapshot()
    old_artifact = active.project.envelope.data["sources"][0]["source_artifact_id"]
    selected = source.with_name("additional.xml")
    _xml(selected, [(4, "Fourth original")])
    parsed = TranslationIoProjectSourcePreparer().prepare_source(
        ProjectSourceRequest(str(selected), FormatId.XML_EET), context, role="primary", common_options=()
    )
    orphan = replace(
        parsed.baseline.entries[0],
        entry_key=replace(parsed.baseline.entries[0].entry_key, namespace=saved.source_fingerprints[0].namespace),
        translation="额外保存译文",
    )
    with_orphan = replace(saved, entries=(*saved.entries, orphan), revision=saved.revision + 1)
    services.variants.save(with_orphan.ref, with_orphan.to_dto())
    opened = services.current_project_opener.open_path(path, context)
    assert opened.is_success and "recovery" not in opened.value
    service = services.project_source_supplements
    result = service.prepare(path, source_id, orphan.entry_key.namespace.value, str(selected), context)
    assert result.is_success, result.diagnostics
    assert len(result.value.rows) == result.value.matched_count == 1
    assert service.commit(result.value.token, context).is_success
    reopened = services.current_project_opener.open_path(path, context)
    assert reopened.is_success, reopened.diagnostics
    assert not services.project_lifecycle.active.missing_inputs
    sources = services.project_lifecycle.active.project.envelope.data["sources"]
    assert any(item["source_artifact_id"] == old_artifact for item in sources)
    originals = {row.original for hydration in reopened.value["hydrations"] for row in hydration.entries}
    assert originals == {"One", "Two", "Three", "Fourth original"}
    assert sum(row.translation == "译文" for row in services.project_lifecycle.active.variant.snapshot().entries) == 3
