"""Proposals show known source values and exact resolved default scopes."""

from types import SimpleNamespace

import pytest

from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.smart_assistant.operation_preview import build_operation_previews
from transbridge.smart_assistant.tools.tool_translator import TranslationController
from transbridge.smart_assistant.tools.translation_settings import TranslationSettings


def _context():
    entries = [
        TranslationEntry("a", "a", "Source A", "old A", 1, "BOOK:FULL"),
        TranslationEntry("b", "b", "Source B", "", 0, "BOOK:FULL"),
    ]
    return SimpleNamespace(collection=TranslationEntryCollection(entries), translation_scope={})


def test_edit_preview_has_full_real_source_old_and_proposed_text_without_mutation():
    context = _context()
    proposed = "new\ntoken=literal " + "译文" * 200
    (result,) = build_operation_previews(
        [{"tool": "edit_translation", "args": {"entry_id": "a", "new_translation": proposed}}], context
    )
    (row,) = result.entries
    assert (row.original, row.before, row.after) == ("Source A", "old A", proposed)
    assert context.collection.get("a").translation == "old A"
    context.collection = TranslationEntryCollection([
        TranslationEntry("a", "a", "Source A", "later edit", 1, "BOOK:FULL")
    ])
    assert row.before == "old A"


def test_default_translation_scope_matches_untranslated_entries_without_changing_scope():
    context = _context()
    (result,) = build_operation_previews([{"tool": "start_translation", "args": {}}], context)
    assert [row.entry_id for row in result.entries] == ["b"]
    assert result.entries[0].after is None
    assert "执行时生成" in result.entries[0].detail
    assert context.translation_scope == {}


def test_empty_filtered_scope_stays_empty_at_preview_and_launch(monkeypatch):
    context = _context()
    settings = TranslationSettings()
    assert settings.set_scope({"stages": [5]}, context).success
    assert settings.get_scope_preview({}, context).data["matched"] == 0
    (preview,) = build_operation_previews([{"tool": "start_translation", "args": {}}], context)
    assert preview.resolved and not preview.entries
    controller = TranslationController()
    monkeypatch.setattr(controller, "_launch", lambda *_a, **_kw: pytest.fail("empty scope started a task"))
    assert not controller.start_translation({}, context, context.collection).success


@pytest.mark.parametrize("args", [{}, {"mode": "mixed"}, {"mode": "polish"}, {"entry_ids": ["a"]}])
def test_retired_exclusion_fails_before_preview_or_launch(args, monkeypatch):
    context = _context()
    settings = TranslationSettings()
    assert settings.set_scope({"stages": [1]}, context).success
    assert not settings.set_scope({"action": "exclude", "stages": [0]}, context).success
    assert context.translation_scope == {"stages": [1], "categories": [], "labels": []}
    context.translation_scope = {"action": "exclude", "stages": [0]}
    assert not settings.get_scope_preview({}, context).success
    with pytest.raises(ValueError, match="重新设置范围"):
        build_operation_previews([{"tool": "start_translation", "args": args}], context)
    controller = TranslationController()
    monkeypatch.setattr(controller, "_launch", lambda *_a, **_kw: pytest.fail("retired scope started a task"))
    result = controller.start_translation(args, context, context.collection)
    assert not result.success and "重新设置范围" in result.message


def test_missing_target_and_unknown_proposal_are_not_filled_with_other_entries():
    context = _context()
    (result,) = build_operation_previews(
        [{"tool": "edit_translation", "args": {"entry_id": "gone", "new_translation": "new"}}], context
    )
    assert result.entries[0].original is None and result.entries[0].before is None
    assert result.entries[0].after == "new"


def test_polish_scope_and_duplicate_ids_have_truthful_counts():
    context = _context()
    with pytest.raises(ValueError, match="set_scope"):
        build_operation_previews([{"tool": "start_polish", "args": {"scope": "passed"}}], context)
    _, result = build_operation_previews(
        [{"tool": "set_scope", "args": {"stages": [1]}}, {"tool": "start_translation", "args": {"mode": "polish"}}],
        context,
    )
    assert [row.entry_id for row in result.entries] == ["a"]
    (result,) = build_operation_previews(
        [{"tool": "set_stage", "args": {"entry_ids": ["a", "a", "b"], "stage": 3}}], context
    )
    assert len(result.entries) == 2
    assert result.entries[0].before == result.entries[0].after == "old A"


def test_preview_uses_saved_request_scope_instead_of_later_ui_scope():
    context = _context()
    context._target_collection = context.collection
    context.translation_scope = {"stages": [0]}
    request = SimpleNamespace(source_message_ids=("input",), execution_version_json="")
    state = {"ingress": [{"message_id": "input", "selection": {"translation_scope": {"stages": [1]}}}]}
    context.assistant_gate = SimpleNamespace(
        current_request=lambda: request, service=SimpleNamespace(state=lambda _: state), context=object()
    )
    (preview,) = build_operation_previews([{"tool": "start_translation", "args": {"mode": "mixed"}}], context)
    assert [row.entry_id for row in preview.entries] == ["a"]


def test_label_batch_preview_resolves_filter_and_explains_non_translation_change():
    context = _context()
    context.filter_state = {"stage": [1]}
    context.label_library = {"review": {"name": "待校对"}}
    (preview,) = build_operation_previews(
        [{"tool": "manage_entry_labels", "args": {"action": "batch_assign", "name": "待校对"}}], context
    )
    assert [row.entry_id for row in preview.entries] == ["a"]
    assert "添加标签：待校对" in preview.entries[0].detail
    assert preview.entries[0].after == preview.entries[0].before


def test_batch_preview_uses_preceding_desktop_filter_and_omits_already_assigned_rows():
    context = _context()
    context.filter_state = {}
    context.label_library = {"review": {"name": "待校对"}, "ready": {"name": "已确认"}}
    context.entry_labels = {}
    steps = [
        {"tool": "set_filters", "args": {"labels": ["review"]}},
        {"tool": "manage_entry_labels", "args": {"action": "batch_assign", "name": "已确认"}},
    ]
    _, preview = build_operation_previews(steps, context)
    assert preview.resolved and preview.entries == ()
    context.entry_labels = {"a": {"review", "ready"}, "b": {"review"}}
    _, preview = build_operation_previews(steps, context)
    assert [row.entry_id for row in preview.entries] == ["b"]
    assert context.filter_state == {}
    assert context.entry_labels["b"] == {"review"}


def test_duplicate_label_names_refuse_preview_and_write(current_project_factory):
    from transbridge.smart_assistant.tools.tool_editor import _tool_manage_entry_labels
    from transbridge.smart_assistant.tools.types import ExecutionContext

    project = current_project_factory(
        _context().collection, label_library={"a": {"name": "待校对"}, "b": {"name": "待校对"}}
    )
    ctx = ExecutionContext(app_context=project.context)
    args = {"action": "batch_assign", "name": "待校对"}
    (preview,) = build_operation_previews([{"tool": "manage_entry_labels", "args": args}], ctx)
    assert not preview.resolved and "多个标签" in preview.notice
    before = project.aggregate.snapshot()
    result = _tool_manage_entry_labels(args, ctx)
    assert not result.success and "多个标签" in result.message
    assert project.aggregate.snapshot() == before
