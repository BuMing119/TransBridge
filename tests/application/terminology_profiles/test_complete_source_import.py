from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.application.terminology_profiles.test_profile_effective import _BaseSnapshots, _profile_service
from tests.application.terminology_profiles.test_source_import import _decision, _importer, _snapshot, _source
from transbridge.application.terminology.effective import (
    EffectiveSnapshotStatus,
    EffectiveTerminologySnapshot,
    TerminologyLookupContext,
    resolve_snapshot,
)
from transbridge.application.terminology.models import DecisionStatus, TermScope
from transbridge.application.terminology_profiles import (
    ProfiledEffectiveTerminologySnapshotPort,
    ProfileOccurrenceBinding,
    ProfileTermMapping,
    TerminologyProfileContent,
    TerminologyProfileImportPolicy,
    TerminologySourceEntry,
    TerminologySourceSnapshot,
)
from transbridge.application.terminology_profiles.browse_queries import (
    ProfiledTerminologyBrowseSnapshotPort,
    ProfileMappingState,
)
from transbridge.application.terminology_profiles.entry_projection import ProfileEntryInput, ProfileEntryProjector
from transbridge.application.terminology_profiles.import_preview import ImportPreviewOutcome, import_preview_rows
from transbridge.application.translation.terminology_run_snapshot import TerminologyRunSnapshotFactory


@pytest.mark.parametrize("policy", tuple(TerminologyProfileImportPolicy))
def test_batch_policy_imports_source_only_and_preserves_base_and_other_schemes(policy):
    importer, profiles = _importer()
    other = profiles.create_with_content("project-1", "Existing scheme", TerminologyProfileContent())
    base = _snapshot(_decision("a", "A", "原译"))
    before = base
    preview = importer.preview("project-1", "variant-1", base, _source(("A", "新译"), ("B", "独有")), policy=policy)
    mappings = {item.original: item for item in preview.content.mappings}
    assert mappings["A"].translation == ("新译" if policy is TerminologyProfileImportPolicy.USE_IMPORTED else "原译")
    assert mappings["B"].translation == "独有"
    assert mappings["B"].scheme_owned and mappings["B"].base_translation == ""
    result = importer.create_and_publish("project-1", "variant-1", "Imported", preview)
    assert result.selection is None
    assert base == before
    assert profiles.list_profiles("project-1")[0].draft == other.draft


@pytest.mark.parametrize(
    "policy,expected",
    [(TerminologyProfileImportPolicy.KEEP_EXISTING, "首译"), (TerminologyProfileImportPolicy.USE_IMPORTED, "末译")],
)
def test_duplicate_source_keys_follow_source_order_and_preserve_scope(policy, expected):
    importer, _ = _importer()
    source = TerminologySourceSnapshot.capture(
        "json",
        "scope",
        (
            TerminologySourceEntry("A", "首译"),
            TerminologySourceEntry(" a ", "首译"),
            TerminologySourceEntry("A", "末译"),
            TerminologySourceEntry("A", "插件", "plugin", "A.esp"),
        ),
    )
    preview = importer.preview("project-1", "variant-1", _snapshot(), source, policy=policy)
    mappings = {item.plugin_id: item for item in preview.content.mappings}
    assert mappings[None].translation == expected
    assert mappings["A.esp"].translation == "插件"
    assert preview.duplicate_entry_count == 1
    assert preview.source_term_count == preview.source_only_term_count == 2
    assert preview.conflict_count == 1
    assert (
        source.source_digest
        != TerminologySourceSnapshot.capture("json", "scope", reversed(source.entries)).source_digest
    )


def test_unscoped_source_does_not_fan_out_to_matching_plugin():
    importer, _ = _importer()
    base = _snapshot(_decision("plugin", "A", "插件原译", TermScope.plugin("A.esp")))
    preview = importer.preview(
        "project-1", "variant-1", base, _source(("A", "全局")), policy=TerminologyProfileImportPolicy.USE_IMPORTED
    )
    assert {(item.plugin_id, item.translation) for item in preview.content.mappings} == {
        (None, "全局"),
        ("A.esp", "插件原译"),
    }


@pytest.mark.parametrize("policy", tuple(TerminologyProfileImportPolicy))
def test_duplicate_counts_distinguish_exact_translation_repeats_from_raw_spelling_conflicts(policy):
    importer, _ = _importer()
    preview = importer.preview(
        "project-1", "variant-1", _snapshot(), _source(("A", "A"), ("a", "A"), ("A", "Ａ")), policy=policy
    )
    assert preview.duplicate_entry_count == 1
    assert preview.conflict_count == 1
    assert preview.conflicts[0].source_translations == ("A", "Ａ")
    assert preview.content.mappings[0].translation == (
        "A" if policy is TerminologyProfileImportPolicy.KEEP_EXISTING else "Ａ"
    )
    distinct_only = importer.preview(
        "project-1", "variant-1", _snapshot(), _source(("A", "A"), ("a", "Ａ")), policy=policy
    )
    assert distinct_only.duplicate_entry_count == 0
    assert distinct_only.conflict_count == 1


@pytest.mark.parametrize("policy", tuple(TerminologyProfileImportPolicy))
def test_unchanged_matches_count_as_kept_so_result_categories_cover_every_mapping(policy):
    importer, _ = _importer()
    preview = importer.preview(
        "project-1", "variant-1", _snapshot(_decision("a", "A", "甲")), _source(("A", "甲"), ("B", "乙")), policy=policy
    )
    assert (preview.source_only_term_count, preview.overwritten_mapping_count, preview.kept_mapping_count) == (1, 0, 1)
    assert sum((preview.source_only_term_count, preview.overwritten_mapping_count, preview.kept_mapping_count)) == 2
    assert next(row for row in import_preview_rows(preview) if row.original == "A").outcome is ImportPreviewOutcome.KEPT


@pytest.mark.parametrize("policy", tuple(TerminologyProfileImportPolicy))
@pytest.mark.parametrize("existing,imported", [("字母A", "字母Ａ"), ("甲  乙", "甲 乙"), ("Term", "term")])
def test_keep_preserves_exact_existing_text_while_imported_policy_counts_actual_spelling_changes(
    policy, existing, imported
):
    importer, _ = _importer()
    preview = importer.preview(
        "project-1", "variant-1", _snapshot(_decision("a", "A", existing)), _source(("A", imported)), policy=policy
    )
    changed = policy is TerminologyProfileImportPolicy.USE_IMPORTED
    assert preview.content.mappings[0].translation == (imported if changed else existing)
    assert preview.overwritten_mapping_count == int(changed)
    assert preview.kept_mapping_count == int(not changed)
    assert preview.changed_mapping_count == int(changed)
    assert import_preview_rows(preview)[0].outcome is (
        ImportPreviewOutcome.ADOPTED if changed else ImportPreviewOutcome.KEPT
    )


def test_owned_terms_activate_only_selected_scheme_and_do_not_revive_inactive_base():
    profiles = _profile_service()
    base = _BaseSnapshots()
    current = base.snapshots[base.current]
    inactive = replace(
        current.decisions[0],
        original="Inactive",
        normalized_original="inactive",
        term_id="inactive",
        status=DecisionStatus.REVIEW_REQUIRED,
    )
    base.snapshots[base.current] = replace(current, decisions=(*current.decisions, inactive))
    content = TerminologyProfileContent(
        mappings=(
            ProfileTermMapping("New", "新增", scheme_owned=True),
            ProfileTermMapping("Orphan", "旧孤立"),
            ProfileTermMapping("Inactive", "不得复活", scheme_owned=True),
            ProfileTermMapping("New", "插件新词", scope_kind="plugin", plugin_id="A.esp", scheme_owned=True),
        )
    )
    profile = profiles.create_with_content("project-1", "Imported", content)
    profiles.publish(profile.profile_id, expected_draft_revision=0)
    port = ProfiledEffectiveTerminologySnapshotPort(base, profiles)
    assert port.snapshot("project-1", "variant-1") is base.snapshots[base.current]
    profiles.select("project-1", "variant-1", profile.profile_id)
    selected = port.snapshot("project-1", "variant-1")
    assert {item.translation for item in selected.decisions if item.is_effective} == {"新增", "插件新词", "旧孤立"}
    context = TerminologyLookupContext("project-1", "variant-1", plugin_id="A.esp")
    assert resolve_snapshot(selected, "New", context).decision.translation == "插件新词"
    browsing = ProfiledTerminologyBrowseSnapshotPort(base, profiles).describe("project-1", "variant-1")
    rows = {(row.original, row.plugin_id): row for row in browsing.mapping_rows}
    assert rows["New", None].state is ProfileMappingState.SCHEME_OWNED
    assert rows["Orphan", None].state is ProfileMappingState.EFFECTIVE
    assert rows["Inactive", None].state is ProfileMappingState.BASE_INACTIVE
    profiles.clear_selection("project-1", "variant-1")
    assert port.snapshot("project-1", "variant-1") is base.snapshots[base.current]


def test_absent_base_ai_snapshot_restores_exactly_after_base_publication_and_profile_edit():
    importer, profiles = _importer()
    state = [EffectiveTerminologySnapshot("project-1", "variant-1", EffectiveSnapshotStatus.NO_PROJECT_VERSION)]
    base = SimpleNamespace(snapshot=lambda *args: state[0])
    preview = importer.preview("project-1", "variant-1", state[0], _source(("B", "独有")))
    result = importer.create_and_publish("project-1", "variant-1", "Source-only", preview, select=True)
    port = ProfiledEffectiveTerminologySnapshotPort(base, profiles)
    factory = TerminologyRunSnapshotFactory(port)
    frozen = factory.freeze("project-1", "variant-1")
    assert frozen.ref.status is EffectiveSnapshotStatus.READY
    state[0] = _snapshot(_decision("new-base", "A", "后来发布"))
    edited = profiles.save_draft(result.profile.profile_id, TerminologyProfileContent(), expected_revision=0)
    profiles.publish(result.profile.profile_id, expected_draft_revision=edited.draft_revision)
    profiles.clear_selection("project-1", "variant-1")
    restored = factory.restore(frozen.ref)
    assert restored == frozen
    assert [(item.original, item.translation) for item in restored.decisions] == [("B", "独有")]


@pytest.mark.parametrize(
    "status",
    [
        EffectiveSnapshotStatus.NO_PROJECT_VERSION,
        EffectiveSnapshotStatus.READY,
        EffectiveSnapshotStatus.UNAVAILABLE,
        EffectiveSnapshotStatus.CORRUPT,
    ],
)
def test_owned_projection_requires_position_evidence_and_fails_closed_for_unreadable_base(status):
    profiles = _profile_service()
    mapping = ProfileTermMapping("B", "新词", scheme_owned=True)
    profile = profiles.create_with_content("project-1", "Source-only", TerminologyProfileContent(mappings=(mapping,)))
    unbound = profiles.publish(profile.profile_id, expected_draft_revision=0)
    base = (
        _snapshot()
        if status is EffectiveSnapshotStatus.READY
        else EffectiveTerminologySnapshot(
            "project-1",
            "variant-1",
            status,
            diagnostics=("unreadable",)
            if status in {EffectiveSnapshotStatus.UNAVAILABLE, EffectiveSnapshotStatus.CORRUPT}
            else (),
        )
    )
    entry = ProfileEntryInput("entry", "Use B", "旧词正文", None)
    assert ProfileEntryProjector(unbound, base).project_input(entry).translation == "旧词正文"
    bound_content = TerminologyProfileContent(
        mappings=(mapping,), bindings=(ProfileOccurrenceBinding("entry", mapping.term_key, 0, 2, "旧词"),)
    )
    edited = profiles.save_draft(profile.profile_id, bound_content, expected_revision=0)
    bound = profiles.publish(profile.profile_id, expected_draft_revision=edited.draft_revision)
    projected = ProfileEntryProjector(bound, base).project_input(entry)
    assert projected.translation == (
        "新词正文"
        if status in {EffectiveSnapshotStatus.READY, EffectiveSnapshotStatus.NO_PROJECT_VERSION}
        else "旧词正文"
    )
    explicit_without_source_match = replace(entry, original="Unrelated wording")
    projected_without_source = ProfileEntryProjector(bound, base).project_input(explicit_without_source_match)
    assert projected_without_source.translation == projected.translation
