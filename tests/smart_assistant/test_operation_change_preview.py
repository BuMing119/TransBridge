"""Proposals show known source values and exact resolved default scopes."""

from types import SimpleNamespace

from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.smart_assistant.operation_preview import build_operation_previews


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


def test_missing_target_and_unknown_proposal_are_not_filled_with_other_entries():
    context = _context()
    (result,) = build_operation_previews(
        [{"tool": "edit_translation", "args": {"entry_id": "gone", "new_translation": "new"}}], context
    )
    assert result.entries[0].original is None and result.entries[0].before is None
    assert result.entries[0].after == "new"


def test_polish_scope_and_duplicate_ids_have_truthful_counts():
    context = _context()
    (result,) = build_operation_previews([{"tool": "start_polish", "args": {"scope": "passed"}}], context)
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
    (preview,) = build_operation_previews([{"tool": "start_translation", "args": {}}], context)
    assert [row.entry_id for row in preview.entries] == ["a"]


def test_label_batch_preview_resolves_filter_and_explains_non_translation_change():
    context = _context()
    context.filter_state = {"stage": [1]}
    (preview,) = build_operation_previews(
        [{"tool": "manage_entry_labels", "args": {"action": "batch_assign", "name": "待校对"}}], context
    )
    assert [row.entry_id for row in preview.entries] == ["a"]
    assert "添加标签：待校对" in preview.entries[0].detail
    assert preview.entries[0].after == preview.entries[0].before
