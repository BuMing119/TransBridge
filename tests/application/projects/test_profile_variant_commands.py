"""Real project saves keep naming-scheme text and SQLite associations together."""

from contextlib import closing
from dataclasses import asdict, replace
import json
from pathlib import Path

import pytest

from tests.persistence.v2.test_workflow_closure_audit import _open_current, service_factory as service_factory_fixture
from tests.plugin_fixtures import write_plugin
from transbridge.application.contracts import RequestContext
from transbridge.application.io import FormatId
from transbridge.application.projects import ProjectProvisioningRequest, ProjectSourceRequest
from transbridge.application.terminology_profiles import (
    ProfileTermMapping,
    TerminologyProfileContent,
    TerminologyProfileSelection,
    TerminologyProfileService,
)
from transbridge.persistence.project_store.connection import project_connection
from transbridge.persistence.terminology.project_selection import ProjectTerminologySelection
from transbridge.persistence.terminology.repository import SqliteTerminologyRepository
from transbridge.persistence.v2.variant import VariantSnapshot

service_factory = service_factory_fixture


def _profiles(services, project_id):
    return closing(SqliteTerminologyRepository.open(services.root, project_id))


def _select_fixture_revision(services, revision):
    active = services.project_lifecycle.active
    ref = active.formal_variant_ref
    selection = TerminologyProfileSelection(
        ref.project_id.value, ref.identity.value, revision.profile_id, revision.revision, "2026-10-07T12:00:00+08:00"
    )
    with project_connection(Path(services.projects.path_for(active.project_ref)), write=True) as connection:
        changed = connection.execute(
            "UPDATE variant SET terminology_selection = ? WHERE identity = ?",
            (json.dumps(asdict(selection)), ref.identity.value),
        )
        assert changed.rowcount == 1


@pytest.fixture
def scheme_project(service_factory, tmp_path):
    originals = (
        "Whiterun and Whiterun welcome the Dragonborn.",
        "Dragonborn greets an unmapped guard.",
        "An ordinary town.",
        "Whiterun awaits translation.",
    )
    translations = ("雪漫和雪漫欢迎龙裔。", "龙裔向一位守卫致意。", "这座城也叫雪漫。", "")
    source = write_plugin(tmp_path / "Names.esp", [(0x800 + i, f"Name{i}", text) for i, text in enumerate(originals)])
    services = service_factory()
    context = RequestContext("profile-save-test", run_id="profile-save")
    result = services.gui_project_commands.create_project(
        ProjectProvisioningRequest("Names", source=ProjectSourceRequest(str(source), FormatId.PLUGIN_SSE)), context
    )
    assert result.is_success, result.diagnostics
    active = services.project_lifecycle.active
    inputs = services.gui_project_commands._profile_entry_inputs(active.project, active.variant.snapshot(), context)
    keys = {entry.original: key for key, entry in inputs.items()}
    for original, translation in zip(originals, translations, strict=True):
        assert services.gui_project_commands.update_entry(
            keys[original], context, translation=translation, stage=0 if not translation else 3
        ).is_success
    assert services.gui_project_commands.save(context).is_success
    # First open fills canonical source display metadata; establish that stable
    # baseline before tests compare byte-for-byte transaction preimages.
    services = _open_current(service_factory, context)
    active = services.project_lifecycle.active
    participant = ProjectTerminologySelection(tmp_path)
    project_id = active.project_ref.identity.value
    with _profiles(services, project_id) as repository:
        profiles = TerminologyProfileService(repository.localization_profiles)
        revisions = []
        for name, mappings in (
            ("A", (ProfileTermMapping("Whiterun", "雪漫"), ProfileTermMapping("Dragonborn", "龙裔"))),
            ("B", (ProfileTermMapping("Whiterun", "白漫"),)),
        ):
            profile = profiles.create_with_content(project_id, name, TerminologyProfileContent(mappings=mappings))
            revisions.append(profiles.publish(profile.profile_id, expected_draft_revision=0))
    _select_fixture_revision(services, revisions[0])
    return services, context, participant, tuple(revisions), tuple(keys[original] for original in originals)


def _save(scheme_project, **options):
    services, context, _participant, (source, target), _keys = scheme_project
    return services.gui_project_commands.save_profile_variant(
        "B saved", context, source_profile=source, target_profile=target, **options
    )


def _translations(services, ref=None):
    if ref is None:
        snapshot = services.project_lifecycle.active.variant.snapshot()
    else:
        snapshot = VariantSnapshot.from_dto(services.variants.read_snapshot(ref), ref)
    return {entry.entry_key: entry.translation for entry in snapshot.entries}


def test_new_variant_saves_full_partial_conversion_and_exact_association_without_changing_source(scheme_project):
    services, context, participant, (source, target), keys = scheme_project
    original = services.project_lifecycle.active
    before = services.variants.read_snapshot(original.formal_variant_ref)
    selection = participant.selection(original.project_ref.identity.value, original.formal_variant_ref.identity.value)
    source_states = {entry.entry_key: entry for entry in original.variant.snapshot().entries}

    result = _save(scheme_project)

    assert result.is_success, result.diagnostics
    active = services.project_lifecycle.active
    assert active.formal_variant_ref != original.formal_variant_ref
    assert not active.dirty
    assert _translations(services) == dict(
        zip(keys, ("白漫和白漫欢迎龙裔。", "龙裔向一位守卫致意。", "这座城也叫雪漫。", ""), strict=True)
    )
    assert _translations(services, active.formal_variant_ref) == _translations(services)
    target_states = {entry.entry_key: entry for entry in active.variant.snapshot().entries}
    for key in keys[1:]:
        assert target_states[key] == source_states[key]
    assert target_states[keys[0]].revision == source_states[keys[0]].revision.next()
    assert target_states[keys[0]].stage == source_states[keys[0]].stage
    assert services.variants.read_snapshot(original.formal_variant_ref) == before
    assert (
        participant.selection(original.project_ref.identity.value, original.formal_variant_ref.identity.value)
        == selection
    )
    saved = participant.selection(active.project_ref.identity.value, active.formal_variant_ref.identity.value)
    assert (saved.profile_id, saved.revision) == (target.profile_id, target.revision)
    back = services.gui_project_commands.save_profile_variant(
        "A again", context, source_profile=target, target_profile=source
    )
    assert back.is_success, back.diagnostics
    assert _translations(services) == _translations(services, original.formal_variant_ref)


def test_ambiguous_save_requires_frozen_alignment_and_reuses_preview_without_model(scheme_project):
    from transbridge.application.terminology_profiles.occurrence_alignment import AlignmentDecision
    from transbridge.application.terminology_profiles.profile_alignment import align_profile_entries
    from transbridge.application.terminology_profiles.projection import TerminologyProfileProjector

    services, context, _participant, (source, target), keys = scheme_project
    assert services.gui_project_commands.update_entry(
        keys[0], context, translation="雪漫和雪漫欢迎大雪漫。", stage=3
    ).is_success
    assert services.gui_project_commands.save(context).is_success
    active = services.project_lifecycle.active
    inputs = services.gui_project_commands._profile_entry_inputs(active.project, active.variant.snapshot(), context)
    failed = _save(scheme_project)
    assert not failed.is_success
    assert services.project_lifecycle.active.formal_variant_ref == active.formal_variant_ref

    class Service:
        def resolve_many(self, requests, **_kwargs):
            assert len(requests) == 1
            return (AlignmentDecision((0, 1), True, "仅前两处对应原文的两个 Whiterun"),)

    frozen, preview = align_profile_entries(
        tuple(inputs.values()), target, None, source_profile=source, service=Service()
    )
    result = _save(scheme_project, projection_projector=TerminologyProfileProjector(resolver=frozen))
    assert result.is_success, result.diagnostics
    assert _translations(services)[keys[0]] == preview[keys[0].serialize()].translation == "白漫和白漫欢迎大雪漫。"


def test_saved_b_reopens_edits_independently_and_copy_delete_manage_its_association(scheme_project, service_factory):
    services, context, participant, (_source, target), keys = scheme_project
    source_ref = services.project_lifecycle.active.formal_variant_ref
    source_before = _translations(services, source_ref)
    assert _save(scheme_project).is_success
    reopened = _open_current(service_factory, context)
    b_ref = reopened.project_lifecycle.active.formal_variant_ref
    assert _translations(reopened)[keys[0]] == "白漫和白漫欢迎龙裔。"
    assert reopened.gui_project_commands.update_entry(
        keys[0], context, translation="独立修订白漫。", stage=5
    ).is_success
    assert reopened.gui_project_commands.save(context).is_success
    assert _translations(reopened, b_ref)[keys[0]] == "独立修订白漫。"
    assert _translations(reopened, source_ref) == source_before
    assert reopened.gui_project_commands.create_variant("B copy", context, copy_active=True).is_success
    copy_ref = reopened.project_lifecycle.active.formal_variant_ref
    copied = participant.selection(copy_ref.project_id.value, copy_ref.identity.value)
    assert (copied.profile_id, copied.revision) == (target.profile_id, target.revision)
    assert _translations(reopened, copy_ref) == _translations(reopened, b_ref)
    assert reopened.gui_project_commands.delete_variant(b_ref.identity.value, context).is_success
    assert participant.selection(b_ref.project_id.value, b_ref.identity.value) is None
    assert participant.selection(copy_ref.project_id.value, copy_ref.identity.value) == copied


def test_overwrite_requires_explicit_current_target_and_never_overwrites_source(scheme_project):
    services, context, participant, (_source, target), _keys = scheme_project
    original = services.project_lifecycle.active
    assert _save(scheme_project).is_success
    b_ref = services.project_lifecycle.active.formal_variant_ref
    assert services.gui_project_commands.switch_v2(
        original.project_ref, original.formal_variant_ref, context
    ).is_success
    targets = services.gui_project_commands.profile_variant_targets(context, target)
    assert targets.is_success, targets.diagnostics
    assert [(item["id"], item["name"]) for item in targets.value] == [(b_ref.identity.value, "B saved")]
    revision = targets.value[0]["revision"]
    b_path = Path(services.variants.path_for(b_ref))
    before = b_path.read_bytes()
    selection = participant.selection(b_ref.project_id.value, b_ref.identity.value)
    for options, code in (
        ({"target_variant_id": b_ref.identity.value}, "TARGET_VARIANT_REVISION_REQUIRED"),
        (
            {"target_variant_id": b_ref.identity.value, "expected_target_revision": revision + 1},
            "TARGET_VARIANT_REVISION_CHANGED",
        ),
        ({"target_variant_id": original.formal_variant_ref.identity.value}, "SOURCE_VARIANT_OVERWRITE_REFUSED"),
    ):
        result = _save(scheme_project, **options)
        assert not result.is_success
        assert result.diagnostics[0].code == code
        assert b_path.read_bytes() == before
        assert participant.selection(b_ref.project_id.value, b_ref.identity.value) == selection
    result = _save(scheme_project, target_variant_id=b_ref.identity.value, expected_target_revision=revision)
    assert result.is_success, result.diagnostics
    assert services.project_lifecycle.active.formal_variant_ref == b_ref
    assert services.project_lifecycle.active.variant.revision == revision + 1


def test_default_save_creates_another_b_even_when_target_exists(scheme_project):
    services, context, _participant, _profiles, _keys = scheme_project
    original = services.project_lifecycle.active
    assert _save(scheme_project).is_success
    first = services.project_lifecycle.active.formal_variant_ref
    assert services.gui_project_commands.switch_v2(
        original.project_ref, original.formal_variant_ref, context
    ).is_success
    result = services.gui_project_commands.save_profile_variant(
        "Another B", context, source_profile=_profiles[0], target_profile=_profiles[1]
    )
    assert result.is_success, result.diagnostics
    assert services.project_lifecycle.active.formal_variant_ref != first
    assert len(services.project_lifecycle.active.project.envelope.data["variant_ids"]) == 3


@pytest.mark.parametrize("which", ["project", "variant"])
def test_stale_preview_input_revision_is_refused(scheme_project, which):
    services, _context, _participant, _profiles, _keys = scheme_project
    active = services.project_lifecycle.active
    revision = active.project.envelope.revision if which == "project" else active.variant.revision
    result = _save(scheme_project, **{f"expected_{which}_revision": revision + 1})
    assert not result.is_success
    assert result.diagnostics[0].code == f"ACTIVE_{which.upper()}_REVISION_CHANGED"
    assert services.project_lifecycle.active is active


def test_publishing_new_revision_keeps_frozen_save_and_existing_target_pinned(scheme_project):
    services, context, participant, (source, target), keys = scheme_project
    original = services.project_lifecycle.active
    assert _save(scheme_project).is_success
    b_ref = services.project_lifecycle.active.formal_variant_ref
    with _profiles(services, original.project_ref.identity.value) as repository:
        profiles = TerminologyProfileService(repository.localization_profiles)
        draft = profiles.save_draft(
            target.profile_id,
            TerminologyProfileContent(mappings=(ProfileTermMapping("Whiterun", "白漫城"),)),
            expected_revision=0,
        )
        latest = profiles.publish(target.profile_id, expected_draft_revision=draft.draft_revision)
    assert latest.revision == target.revision + 1
    assert participant.selection(b_ref.project_id.value, b_ref.identity.value).revision == target.revision
    assert services.gui_project_commands.switch_v2(
        original.project_ref, original.formal_variant_ref, context
    ).is_success
    frozen = services.gui_project_commands.save_profile_variant(
        "Frozen B", context, source_profile=source, target_profile=target
    )
    assert frozen.is_success, frozen.diagnostics
    assert _translations(services)[keys[0]] == "白漫和白漫欢迎龙裔。"
    frozen_ref = services.project_lifecycle.active.formal_variant_ref
    assert participant.selection(frozen_ref.project_id.value, frozen_ref.identity.value).revision == target.revision


def test_changed_actual_source_association_refuses_stale_preview(scheme_project):
    services, _context, _participant, (_source, target), _keys = scheme_project
    active = services.project_lifecycle.active
    _select_fixture_revision(services, target)
    result = _save(scheme_project)
    assert not result.is_success
    assert result.diagnostics[0].code == "SOURCE_PROFILE_CHANGED"
    assert services.project_lifecycle.active is active


@pytest.mark.parametrize("field,value", [("local_variant_id", "another-variant"), ("content_digest", "changed")])
def test_changed_frozen_base_refuses_save_to_default(scheme_project, field, value):
    services, context, participant, (source, _target), _keys = scheme_project
    active = services.project_lifecycle.active
    base = participant.base_snapshot(active.project_ref.identity.value, active.formal_variant_ref.identity.value)
    result = services.gui_project_commands.save_profile_variant(
        "Default", context, source_profile=source, target_profile=None, base_snapshot=replace(base, **{field: value})
    )
    assert not result.is_success
    assert result.diagnostics[0].code == "BASE_TERMINOLOGY_CHANGED"
    assert services.project_lifecycle.active is active


@pytest.mark.parametrize(
    "plugin,expected", [("Names.esp", "霜城和霜城欢迎龙裔。"), ("Other.esp", "白漫和白漫欢迎龙裔。")]
)
def test_save_uses_installed_entry_plugin_scope(scheme_project, plugin, expected):
    services, context, _participant, (source, target), keys = scheme_project
    active = services.project_lifecycle.active
    published = []
    with _profiles(services, active.project_ref.identity.value) as repository:
        profiles = TerminologyProfileService(repository.localization_profiles)
        for profile, project_text, scoped_text in ((source, "雪漫", "雪漫"), (target, "白漫", "霜城")):
            draft = profiles.save_draft(
                profile.profile_id,
                TerminologyProfileContent(
                    mappings=(
                        ProfileTermMapping("Whiterun", project_text),
                        ProfileTermMapping("Whiterun", scoped_text, scope_kind="plugin", plugin_id=plugin),
                    )
                ),
                expected_revision=0,
            )
            published.append(profiles.publish(profile.profile_id, expected_draft_revision=draft.draft_revision))
    _select_fixture_revision(services, published[0])
    result = services.gui_project_commands.save_profile_variant(
        "Scoped B", context, source_profile=published[0], target_profile=published[1]
    )
    assert result.is_success, result.diagnostics
    assert _translations(services)[keys[0]] == expected
