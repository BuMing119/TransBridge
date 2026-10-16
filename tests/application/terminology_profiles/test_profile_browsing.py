from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.application.terminology_profiles.test_profile_effective import _BaseSnapshots, _profile_service
from transbridge.application.terminology.browse_queries import TermBrowseQuery, TerminologyBrowseQueries
from transbridge.application.terminology.effective import EffectiveSnapshotStatus, EffectiveTerminologySnapshot
from transbridge.application.terminology.errors import CursorStaleError
from transbridge.application.terminology.models import DecisionStatus, TermScope
from transbridge.application.terminology.ports import PageRequest
from transbridge.application.terminology_profiles.browse_queries import (
    ProfiledTerminologyBrowseSnapshotPort,
    ProfileMappingState,
)
from transbridge.application.terminology_profiles.effective import ProfiledEffectiveTerminologySnapshotPort
from transbridge.application.terminology_profiles.models import ProfileTermMapping, TerminologyProfileContent


def _select(profiles, mappings):
    profile = profiles.create_with_content("project-1", "方案 A", TerminologyProfileContent(mappings=tuple(mappings)))
    profiles.publish(profile.profile_id, expected_draft_revision=0)
    profiles.select("project-1", "variant-1", profile.profile_id)
    return profile


def test_profile_mapping_states_match_ai_and_preserve_plugin_scope():
    base, profiles = _BaseSnapshots(), _profile_service()
    original = base.snapshots[base.current]
    inactive = replace(
        original.decisions[0],
        term_id="inactive",
        original="Inactive",
        normalized_original="inactive",
        status=DecisionStatus.REVIEW_REQUIRED,
    )
    excluded = replace(
        inactive, term_id="excluded", original="Excluded", normalized_original="excluded", suppressed=True
    )
    plugin = replace(original.decisions[0], term_id="plugin", scope=TermScope.plugin("A.esm"))
    base.snapshots[base.current] = replace(original, decisions=(*original.decisions, inactive, excluded, plugin))
    _select(
        profiles,
        (
            ProfileTermMapping("Whiterun", "白漫城"),
            ProfileTermMapping("Solitude", "插件独孤", scope_kind="plugin", plugin_id="A.esm"),
            ProfileTermMapping("Inactive", "未确认"),
            ProfileTermMapping("Excluded", "已排除"),
            ProfileTermMapping("Only in profile", "孤立映射"),
        ),
    )
    reader = ProfiledTerminologyBrowseSnapshotPort(base, profiles)
    context = reader.describe("project-1", "variant-1")
    ai = ProfiledEffectiveTerminologySnapshotPort(base, profiles).snapshot("project-1", "variant-1")

    assert context.effective_snapshot == ai
    assert (context.base_version_id, context.profile_id, context.profile_name, context.profile_revision) == (
        "base-2",
        "profile-a",
        "方案 A",
        1,
    )
    rows = {(row.original, row.plugin_id): row for row in context.mapping_rows}
    assert rows["Whiterun", None].state is ProfileMappingState.EFFECTIVE
    assert rows["Solitude", "A.esm"].state is ProfileMappingState.EFFECTIVE
    assert rows["Solitude", None].state is ProfileMappingState.UNMAPPED
    assert rows["Inactive", None].state is ProfileMappingState.BASE_INACTIVE
    assert rows["Excluded", None].state is ProfileMappingState.BASE_INACTIVE
    assert rows["Only in profile", None].state is ProfileMappingState.BASE_MISSING
    assert rows["Only in profile", None].base_translation == ""
    assert rows["Solitude", None].translation == ""
    assert rows["Whiterun", None].base_translation == "雪漫城"
    effective = TerminologyBrowseQueries(SimpleNamespace(active_draft=lambda *args: None), reader).list_effective_terms(
        TermBrowseQuery("project-1", "variant-1")
    )
    assert {item.translation for item in effective.items} == {"白漫城", "插件独孤"}
    assert context.diagnostics == ai.diagnostics


def test_unpublished_base_explains_all_mappings_without_enabling_them():
    profiles = _profile_service()
    _select(profiles, (ProfileTermMapping(f"Term {index}", f"译名 {index}") for index in range(601)))
    base = SimpleNamespace(
        snapshot=lambda *args: EffectiveTerminologySnapshot(
            "project-1",
            "variant-1",
            EffectiveSnapshotStatus.NO_PROJECT_VERSION,
        )
    )
    reader = ProfiledTerminologyBrowseSnapshotPort(base, profiles)
    context = reader.describe("project-1", "variant-1")
    assert len(context.mapping_rows) == 601
    assert all(row.state is ProfileMappingState.BASE_MISSING for row in context.mapping_rows)
    assert context.base_version_id is None
    assert context.effective_snapshot.decisions == ()


def test_default_profile_context_uses_base_decisions_and_has_no_mapping_rows():
    base = _BaseSnapshots()
    context = ProfiledTerminologyBrowseSnapshotPort(base, _profile_service()).describe("project-1", "variant-1")
    assert context.effective_snapshot is base.snapshots[base.current]
    assert context.profile_id is None and context.mapping_rows == ()


@pytest.mark.parametrize("change", ["revision", "selection"])
def test_profile_change_invalidates_effective_cursor_even_when_translations_are_identical(change):
    base, profiles = _BaseSnapshots(), _profile_service()
    mappings = (ProfileTermMapping("Whiterun", "白漫城"), ProfileTermMapping("Solitude", "独孤城"))
    profile = _select(profiles, mappings)
    browsing = TerminologyBrowseQueries(
        SimpleNamespace(active_draft=lambda *args: None), ProfiledTerminologyBrowseSnapshotPort(base, profiles)
    )
    query = TermBrowseQuery("project-1", "variant-1")
    first = browsing.list_effective_terms(query, PageRequest(limit=1))
    if change == "revision":
        profiles.publish(profile.profile_id, expected_draft_revision=0)
    else:
        profiles.clear_selection("project-1", "variant-1")
    with pytest.raises(CursorStaleError):
        browsing.list_effective_terms(query, PageRequest(cursor=first.next_cursor))


def test_unreadable_base_does_not_claim_that_the_profile_mappings_are_missing():
    profiles = _profile_service()
    _select(profiles, (ProfileTermMapping("Whiterun", "白漫城"),))
    base = SimpleNamespace(
        snapshot=lambda *args: EffectiveTerminologySnapshot(
            "project-1",
            "variant-1",
            EffectiveSnapshotStatus.UNAVAILABLE,
            diagnostics=("read-only repository",),
        )
    )
    context = ProfiledTerminologyBrowseSnapshotPort(base, profiles).describe("project-1", "variant-1")
    assert context.status is EffectiveSnapshotStatus.UNAVAILABLE
    assert context.mapping_rows == ()
    assert context.diagnostics == ("read-only repository",)


def test_description_captures_the_exact_selection_read_by_ai_once():
    base, profiles = _BaseSnapshots(), _profile_service()
    _select(profiles, (ProfileTermMapping("Whiterun", "白漫城"),))
    first = profiles.selected_revision("project-1", "variant-1")
    reads = []

    def selected(*args):
        reads.append(args)
        return first if len(reads) == 1 else None

    changing_profiles = SimpleNamespace(selected_revision=selected)
    context = ProfiledTerminologyBrowseSnapshotPort(base, changing_profiles).describe("project-1", "variant-1")
    assert context.profile_id == first.profile_id
    assert context.profile_revision == first.revision
    assert len(reads) == 1
    assert next(item for item in context.effective_snapshot.decisions if item.is_effective).translation == "白漫城"
