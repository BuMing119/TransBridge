import pytest

from tests.application.terminology.test_build import _entry
from tests.integration.bootstrap.test_terminology_build_review import _Harness
from transbridge.application.terminology.drafts import DraftWriteConflict, DraftWriteExpectation, revised_draft
from transbridge.application.terminology.errors import RepositoryConflictError, StaleBuildError
from transbridge.application.terminology.identity import term_id
from transbridge.application.terminology.models import ConflictStatus, DecisionStatus, TermDecision, TermScope
from transbridge.bootstrap.terminology_draft_access import LifecycleDraftLineReader
from transbridge.persistence.terminology.repository import SqliteTerminologyRepository

pytestmark = pytest.mark.integration


@pytest.fixture
def harness(tmp_path, monkeypatch):
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    try:
        yield _Harness(repository, monkeypatch)
    finally:
        repository.close()


def test_prepare_without_published_base_does_not_fabricate_a_draft(harness):
    assert harness.commands.prepare_draft(harness.context) is None
    assert harness.repository.active_draft("project-1", "main") is None
    draft = harness.build()
    assert harness.commands.prepare_draft(harness.context) == draft
    harness.commands.discard_draft(draft.ref, harness.context)
    assert harness.commands.prepare_draft(harness.context) is None


def test_published_version_can_be_edited_and_published_again_without_rebuilding(harness):
    original = harness.build()
    harness.commands.apply_decision(
        "change_translation", harness.context, term_id=original.decisions[0].term_id, translation="巨龙"
    )
    published = harness.publish()
    draft = harness.commands.prepare_draft(harness.context)
    assert draft.ref.base_version_id == published.ref.version_id
    assert draft.decisions == published.decisions
    assert draft.conflict_resolutions == published.conflicts
    assert draft.actions == ()
    updated = harness.commands.edit_term(
        draft.decisions[0].term_id,
        harness.context,
        expected_ref=draft.ref,
        original="Dragon",
        translation="巨龍",
        scope=TermScope.project(),
        variants=("Dragons",),
        notes="统一译法",
    )
    second = harness.publish()
    assert second.parent_version_id == published.ref.version_id
    assert second.decisions[0].translation == "巨龍"
    assert second.decisions[0].variants == ("Dragons",)
    assert second.manual_actions == updated.actions
    assert harness.repository.get_version(published.ref) == published


def test_discarding_resumed_draft_can_resume_the_published_content_again(harness):
    harness.build()
    published = harness.publish()
    draft = harness.commands.prepare_draft(harness.context)
    changed = harness.commands.apply_decision(
        "change_translation",
        harness.context,
        expected_ref=draft.ref,
        term_id=draft.decisions[0].term_id,
        translation="不会保留",
    )
    harness.commands.discard_draft(changed.ref, harness.context)
    resumed = harness.commands.prepare_draft(harness.context)
    assert resumed.ref.draft_id != draft.ref.draft_id
    assert resumed.decisions == published.decisions


def test_prepare_rejects_changed_sources_without_creating_a_draft(harness):
    harness.build()
    harness.publish()
    harness.change_source()
    with pytest.raises(StaleBuildError):
        harness.commands.prepare_draft(harness.context)
    assert harness.repository.active_draft("project-1", "main") is None


def test_resume_cas_rejects_variant_change_during_capture(harness, monkeypatch):
    harness.build()
    harness.publish()
    capture = harness.capture_build_input

    def changed_context(context, *, config):
        result = capture(context, config=config)
        harness.lifecycle.active.variant.revision += 1
        return result

    monkeypatch.setattr(harness, "capture_build_input", changed_context)
    with pytest.raises(RepositoryConflictError):
        harness.commands.prepare_draft(harness.context)
    assert harness.repository.active_draft("project-1", "main") is None


@pytest.mark.parametrize("operation", ["edit", "batch", "add"])
def test_edit_dialog_stale_ref_is_rejected_without_overwriting_changes(harness, operation):
    draft = harness.build()
    current = harness.commands.apply_decision(
        "change_translation", harness.context, term_id=draft.decisions[0].term_id, translation="其他窗口的修改"
    )
    with pytest.raises(RepositoryConflictError, match="草稿已变化"):
        if operation == "edit":
            harness.commands.edit_term(
                draft.decisions[0].term_id,
                harness.context,
                expected_ref=draft.ref,
                original="Dragon",
                translation="旧窗口",
                scope=TermScope.project(),
                variants=(),
                notes="",
            )
        elif operation == "batch":
            harness.commands.batch_terms(
                (draft.decisions[0].term_id,), "suppress", harness.context, expected_ref=draft.ref
            )
        else:
            harness.commands.apply_decision(
                "add", harness.context, expected_ref=draft.ref, original="New", translation="新增"
            )
    assert harness.repository.active_draft("project-1", "main") == current


def test_invalid_batch_term_does_not_commit_valid_terms(harness):
    draft = harness.build()
    with pytest.raises(ValueError, match="不存在"):
        harness.commands.batch_terms((draft.decisions[0].term_id, "missing"), "suppress", harness.context)
    assert harness.repository.active_draft("project-1", "main") == draft


def test_batch_confirm_writes_all_reviewed_terms_in_one_revision(harness):
    draft = harness.build()
    updated = harness.commands.batch_terms(tuple(item.term_id for item in draft.decisions), "confirm", harness.context)
    assert updated.ref.revision == draft.ref.revision + 1
    assert all(item.status is DecisionStatus.MANUAL_CONFIRMED for item in updated.decisions)


@pytest.mark.parametrize("operation", ["edit", "batch"])
def test_variant_change_at_commit_rolls_back_all_edits(harness, monkeypatch, operation):
    draft = harness.build()
    read_line = LifecycleDraftLineReader.read_line
    reads = 0

    def changed_line(reader, *args):
        nonlocal reads
        reads += 1
        if reads == 2:
            harness.lifecycle.active.variant.revision += 1
        return read_line(reader, *args)

    monkeypatch.setattr(LifecycleDraftLineReader, "read_line", changed_line)
    with pytest.raises(DraftWriteConflict):
        if operation == "edit":
            harness.commands.edit_term(
                draft.decisions[0].term_id,
                harness.context,
                expected_ref=draft.ref,
                original="Dragon",
                translation="不应保存",
                scope=TermScope.project(),
                variants=(),
                notes="",
            )
        else:
            harness.commands.batch_terms((draft.decisions[0].term_id,), "suppress", harness.context)
    assert harness.repository.active_draft("project-1", "main") == draft


def _conflicting_build(harness):
    harness.parser.entries["source-a"] = (
        _entry("source-a", "one", "Dragon", "龙"),
        _entry("source-a", "two", "Dragon", "飞龙"),
        _entry("source-a", "three", "Sword", "剑"),
        _entry("source-a", "four", "Sword", "长剑"),
    )
    harness.build()
    return harness.repository.latest_build("project-1", "main")


def _legacy_unreviewed_draft(harness, scope=TermScope.project()):
    """Model an older saved draft created before ADD gained conflict validation."""
    _conflicting_build(harness)
    current = harness.repository.active_draft("project-1", "main")
    decision = TermDecision(
        term_id(project_id="project-1", variant_id="main", original="Dragon", scope=scope),
        "project-1",
        "main",
        "Dragon",
        "dragon",
        "手工词",
        scope,
        DecisionStatus.MANUAL_CONFIRMED,
    )
    legacy = revised_draft(current, decisions=(decision,))
    reader = LifecycleDraftLineReader(harness.lifecycle, harness.repository)
    harness.repository.draft_transactions(reader).save_draft(
        legacy, expectation=DraftWriteExpectation.from_draft(current, reader.read_line(None, "project-1", "main"))
    )
    return legacy


def test_resume_preserves_resolved_conflicts_and_leaves_unresolved_groups_pending(harness):
    build = _conflicting_build(harness)
    dragon = next(item for item in build.conflicts if item.normalized_original == "dragon")
    details = harness.commands.conflict_details(dragon, harness.context)
    harness.commands.resolve_review(details, "unify", harness.context, translation="巨龙")
    first = harness.publish()
    resumed = harness.commands.prepare_draft(harness.context)
    assert len(resumed.conflict_resolutions) == 1
    assert resumed.conflict_resolutions[0].status is ConflictStatus.UNIFIED
    assert harness.commands.review_counts(harness.context).unresolved_conflicts == 1
    second = harness.publish()
    assert second.conflicts == first.conflicts


@pytest.mark.parametrize("operation", ["edit", "confirm", "reenable", "rename_into_conflict"])
def test_manual_edit_and_batch_cannot_bypass_unresolved_conflict_review(harness, operation):
    draft = _legacy_unreviewed_draft(harness)
    if operation == "rename_into_conflict":
        draft = harness.commands.apply_decision("add", harness.context, original="Safe", translation="其他词")
    original = "Safe" if operation == "rename_into_conflict" else "Dragon"
    selected = next(item for item in draft.decisions if item.original == original)
    with pytest.raises(ValueError, match="异译"):
        if operation in {"edit", "rename_into_conflict"}:
            harness.commands.edit_term(
                selected.term_id,
                harness.context,
                original="Dragon",
                translation="不应保存",
                scope=TermScope.project(),
                variants=(),
                notes="",
            )
        else:
            harness.commands.batch_terms((selected.term_id,), operation, harness.context)
    assert harness.repository.active_draft("project-1", "main") == draft


@pytest.mark.parametrize("scope", [TermScope.project(), TermScope.plugin("Dragons.esp")])
def test_add_cannot_create_an_effective_term_for_an_unreviewed_conflict(harness, scope):
    _conflicting_build(harness)
    draft = harness.commands.prepare_draft(harness.context)
    with pytest.raises(ValueError, match="异译"):
        harness.commands.apply_decision(
            "add",
            harness.context,
            expected_ref=draft.ref,
            original="DRAGON",
            translation="未经审核",
            scope=scope,
        )
    assert harness.repository.active_draft("project-1", "main") == draft


@pytest.mark.parametrize("scope", [TermScope.project(), TermScope.plugin("Dragons.esp")])
def test_direct_publish_rejects_legacy_unreviewed_effective_terms_without_changing_state(harness, scope):
    draft = _legacy_unreviewed_draft(harness, scope)
    with pytest.raises(ValueError, match="异译"):
        harness.commands.publish(harness.context)
    assert harness.repository.effective_version("project-1", "main") is None
    assert harness.repository.active_draft("project-1", "main") == draft


@pytest.mark.parametrize(
    "operation", ["change_translation", "change_scope", "change_notes", "reenable", "unify_translation"]
)
def test_legacy_single_field_commands_cannot_bypass_unreviewed_conflicts(harness, operation):
    draft = _legacy_unreviewed_draft(harness)
    fields = {
        "change_translation": {"translation": "不应保存"},
        "change_scope": {"scope": TermScope.plugin("Dragons.esp")},
        "change_notes": {"notes": "不应保存"},
        "reenable": {},
        "unify_translation": {"translation": "不应保存"},
    }[operation]
    with pytest.raises(ValueError, match="异译"):
        harness.commands.apply_decision(operation, harness.context, term_id=draft.decisions[0].term_id, **fields)
    assert harness.repository.active_draft("project-1", "main") == draft


def test_unrelated_manual_term_and_suppressed_conflict_can_still_be_published(harness):
    draft = _legacy_unreviewed_draft(harness)
    harness.commands.apply_decision("suppress", harness.context, term_id=draft.decisions[0].term_id)
    harness.commands.apply_decision("add", harness.context, original="Safe", translation="可用词条")
    published = harness.publish()
    assert {item.original for item in published.decisions if item.is_effective} == {"Safe"}
    assert all(item.status is ConflictStatus.UNRESOLVED for item in published.conflicts)


def test_restore_own_identity_after_publication_keeps_history_and_latest_edit_values(harness):
    harness.build()
    first = harness.publish()
    draft = harness.commands.prepare_draft(harness.context)
    original = draft.decisions[0]
    harness.commands.edit_term(
        original.term_id,
        harness.context,
        expected_ref=draft.ref,
        original="Ancient Dragon",
        translation="古龙",
        scope=TermScope.plugin("Dragons.esp"),
        variants=("Ancient Dragons",),
        notes="第一次",
    )
    second = harness.publish()
    draft = harness.commands.prepare_draft(harness.context)
    selected = next(item for item in draft.decisions if not item.suppressed)
    harness.commands.edit_term(
        selected.term_id,
        harness.context,
        expected_ref=draft.ref,
        original=original.original,
        translation="新人工译名",
        scope=original.scope,
        variants=("Dragons",),
        notes="恢复身份后的备注",
    )
    restored = harness.publish()
    active = next(item for item in restored.decisions if item.is_effective)
    assert (active.term_id, active.translation, active.variants, active.notes) == (
        original.term_id,
        "新人工译名",
        ("Dragons",),
        "恢复身份后的备注",
    )
    assert len(restored.decisions) == 2
    assert restored.manual_actions[-1].term_id == selected.term_id
    assert restored.manual_actions[-1].replacement_term_id == original.term_id
    assert harness.repository.get_version(first.ref) == first
    assert harness.repository.get_version(second.ref) == second
