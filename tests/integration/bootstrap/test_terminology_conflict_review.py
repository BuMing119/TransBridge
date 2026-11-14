from dataclasses import replace

import pytest

from tests.application.terminology.test_build import _entry
from tests.integration.bootstrap.test_terminology_build_review import _Harness
from tests.integration.bootstrap.test_terminology_publication_review import _finish_publish
from transbridge.application.terminology.conflict_queries import ConflictFilter
from transbridge.application.terminology.conflicts import ConflictResolutionOperation as Operation
from transbridge.application.terminology.errors import CursorStaleError, RepositoryConflictError, RevisionConflictError
from transbridge.application.terminology.models import ConflictStatus, ManualActionType, TermScope
from transbridge.application.terminology.ports import PageRequest
from transbridge.persistence.terminology.repository import SqliteTerminologyRepository

pytestmark = pytest.mark.integration


@pytest.fixture
def harness(tmp_path, monkeypatch):
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    h = _Harness(repository, monkeypatch)
    source = h.snapshot.sources[0]
    h.snapshot = replace(
        h.snapshot, sources=(replace(source, registration=replace(source.registration, plugin_scope="A.esm")),)
    )
    h.parser.entries["source-a"] = (
        _entry("source-a", "one", "Dragon", "龙"),
        _entry("source-a", "two", "Dragon", "飞龙"),
        _entry("source-a", "three", "Sword", "剑"),
        _entry("source-a", "four", "Sword", "长剑"),
    )
    try:
        yield h
    finally:
        repository.close()


def _details(harness, original="dragon"):
    build = harness.repository.latest_build("project-1", "main")
    conflict = next(item for item in build.conflicts if item.normalized_original == original)
    return harness.commands.conflict_details(conflict, harness.context)


def test_review_lists_overlay_current_draft_and_details_include_all_evidence(harness):
    harness.build()
    details = _details(harness)
    assert len(details.candidates) == 2
    assert len(details.evidence) == 2
    assert details.plugin_ids == ("A.esm",)
    assert harness.commands.review_counts(harness.context).total == 2
    assert {item.translation for item in details.evidence} == {"龙", "飞龙"}
    before = harness.repository.list_review_conflicts(details.build_ref, PageRequest(limit=1))
    assert before.next_cursor is not None

    harness.commands.resolve_review(details, Operation.UNIFY, harness.context, translation="巨龙")

    projected = harness.repository.list_review_conflicts(details.build_ref)
    dragon = next(item for item in projected.items if item.normalized_original == "dragon")
    assert dragon.status is ConflictStatus.UNIFIED
    assert dragon.recommended_translation == "巨龙"
    counts = harness.commands.review_counts(harness.context)
    assert counts.review_required_terms == 0 and counts.unresolved_conflicts == 1
    assert all(
        item.status is ConflictStatus.UNRESOLVED for item in harness.repository.list_conflicts(details.build_ref).items
    )
    with pytest.raises(CursorStaleError):
        harness.repository.list_review_conflicts(details.build_ref, PageRequest(limit=1, cursor=before.next_cursor))
    updated = _details(harness)
    assert updated.conflict.status is ConflictStatus.UNIFIED
    assert updated.decisions[0].translation == "巨龙"


def test_repeat_reviews_update_project_and_plugin_choices_without_duplicates(harness):
    harness.build()
    commands, context = harness.commands, harness.context
    commands.resolve_review(_details(harness), Operation.UNIFY, context, translation="巨龙")
    commands.resolve_review(
        _details(harness), Operation.PLUGIN_EXCEPTION, context, translation="插件龙", plugin_id="A.esm"
    )
    commands.resolve_review(
        _details(harness), Operation.PLUGIN_EXCEPTION, context, translation="插件飞龙", plugin_id="A.esm"
    )
    result = commands.resolve_review(_details(harness), Operation.IGNORE, context)
    project = next(item for item in result.draft.decisions if item.scope == TermScope.project())
    plugin = next(item for item in result.draft.decisions if item.scope == TermScope.plugin("A.esm"))
    assert project.suppressed
    assert not plugin.suppressed and plugin.translation == "插件飞龙"
    assert len(result.draft.decisions) == 2

    restored = commands.resolve_review(_details(harness), Operation.UNIFY, context, translation="统一龙")

    assert len(restored.draft.decisions) == 2
    project = next(item for item in restored.draft.decisions if item.scope == TermScope.project())
    assert not project.suppressed and project.translation == "统一龙"


def test_open_review_rejects_changed_draft_build_or_unknown_plugin(harness):
    harness.build()
    original = _details(harness)
    with pytest.raises(ValueError, match="已有的插件"):
        harness.commands.resolve_review(
            original, Operation.PLUGIN_EXCEPTION, harness.context, translation="龙", plugin_id="B.esm"
        )
    harness.commands.resolve_review(original, Operation.UNIFY, harness.context, translation="巨龙")
    with pytest.raises(RepositoryConflictError, match="重新审核"):
        harness.commands.resolve_review(original, Operation.IGNORE, harness.context)
    current = _details(harness)
    harness.build()
    with pytest.raises(RepositoryConflictError, match="重新审核"):
        harness.commands.resolve_review(current, Operation.IGNORE, harness.context)


def test_discard_compares_complete_ref_and_keeps_published_history(harness):
    harness.build()
    harness.commands.resolve_review(_details(harness), Operation.UNIFY, harness.context, translation="巨龙")
    first = harness.publish()
    draft = harness.build()
    with pytest.raises(PermissionError):
        harness.commands.discard_draft(replace(draft.ref, variant_id="other"), harness.context)
    with pytest.raises(RepositoryConflictError):
        harness.commands.discard_draft(replace(draft.ref, base_content_digest="wrong"), harness.context)
    harness.commands.apply_decision("add", harness.context, original="Manual", translation="人工")
    with pytest.raises((RevisionConflictError, RepositoryConflictError)):
        harness.commands.discard_draft(draft.ref, harness.context)
    current = harness.commands.active_draft(harness.context)

    harness.commands.discard_draft(current.ref, harness.context)

    assert harness.commands.active_draft(harness.context) is None
    assert harness.repository.get_version(first.ref) == first
    assert harness.repository.effective_version("project-1", "main") == first
    harness.commands.restore(first.ref, harness.context)
    assert _finish_publish(harness).current
    assert len(harness.repository.list_versions("project-1", "main").items) == 2


def test_review_state_filters_use_saved_overlay_before_paging(harness):
    harness.build()
    details = _details(harness)
    harness.commands.resolve_review(details, Operation.UNIFY, harness.context, translation="巨龙")
    for state, original in (("pending", "sword"), ("resolved", "dragon")):
        page = harness.repository.list_review_conflicts(
            details.build_ref, PageRequest(limit=1), filters=ConflictFilter(review_state=state)
        )
        assert page.total == 1
        assert page.items[0].normalized_original == original


@pytest.mark.parametrize(
    "operation, values",
    [
        (Operation.UNIFY, {"translation": "巨龙"}),
        (Operation.PLUGIN_EXCEPTION, {"translation": "插件龙", "plugin_id": "A.esm"}),
        (Operation.IGNORE, {}),
    ],
)
def test_undo_restores_previous_group_and_keeps_other_decisions_and_audit(harness, operation, values):
    harness.build()
    commands, context = harness.commands, harness.context
    commands.resolve_review(_details(harness, "sword"), Operation.UNIFY, context, translation="长剑")
    before = _details(harness)
    draft_before = commands.active_draft(context)
    result = commands.resolve_review(before, operation, context, **values)
    undone = commands.undo_review(before, result.draft.ref, context)
    assert undone.draft.decisions == draft_before.decisions
    assert undone.conflict == before.conflict
    assert undone.draft.conflict_resolutions == draft_before.conflict_resolutions
    assert set(result.draft.actions).issubset(undone.draft.actions)
    assert sum(action.action_type is ManualActionType.UNDO_CONFLICT for action in undone.draft.actions) == 1
    assert _details(harness).conflict.status is ConflictStatus.UNRESOLVED
    with pytest.raises(RepositoryConflictError):
        commands.undo_review(before, result.draft.ref, context)


def test_undo_edit_of_resolved_choice_restores_previous_translation(harness):
    harness.build()
    commands, context = harness.commands, harness.context
    commands.resolve_review(_details(harness), Operation.UNIFY, context, translation="龙")
    before = _details(harness)
    result = commands.resolve_review(before, Operation.UNIFY, context, translation="巨龙")
    undone = commands.undo_review(before, result.draft.ref, context)
    assert undone.conflict.recommended_translation == "龙"
    assert _details(harness).decisions == before.decisions


def test_undo_rejects_intervening_write_and_wrong_context(harness):
    harness.build()
    commands, context = harness.commands, harness.context
    before = _details(harness)
    result = commands.resolve_review(before, Operation.UNIFY, context, translation="龙")
    with pytest.raises(PermissionError):
        commands.undo_review(before, result.draft.ref, replace(context, variant_id="other"))
    commands.resolve_review(_details(harness, "sword"), Operation.UNIFY, context, translation="长剑")
    current = commands.active_draft(context)
    with pytest.raises(RepositoryConflictError):
        commands.undo_review(before, result.draft.ref, context)
    with pytest.raises(RepositoryConflictError):
        commands.undo_review(before, current.ref, context)
    assert commands.active_draft(context) == current


def test_undo_rejects_forged_previous_decisions_and_new_build(harness):
    harness.build()
    commands, context = harness.commands, harness.context
    commands.resolve_review(_details(harness), Operation.UNIFY, context, translation="龙")
    before = _details(harness)
    result = commands.resolve_review(before, Operation.UNIFY, context, translation="巨龙")
    forged = replace(before, decisions=(replace(before.decisions[0], translation="伪造"),))
    with pytest.raises(RepositoryConflictError, match="快照"):
        commands.undo_review(forged, result.draft.ref, context)
    assert commands.active_draft(context) == result.draft
    harness.build()
    with pytest.raises(RepositoryConflictError):
        commands.undo_review(before, result.draft.ref, context)
