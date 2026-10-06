from dataclasses import replace
import logging
from types import SimpleNamespace

import pytest

from transbridge.application.assistant_requests.models import RequestError
from transbridge.application.ports.paratranz import ParaTranzEntry
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.paratranz.config_manager import LLMConfig
from transbridge.smart_assistant.request_scope import apply_captured_scope
from transbridge.smart_assistant.tools._entry_upload import upload_entries
from transbridge.smart_assistant.tools._polish_execution import execute_polish
from transbridge.smart_assistant.tools._postprocess_tool_runtime import _resolve_entries
from transbridge.smart_assistant.tools.base import resolve_scope_to_entry_ids
from transbridge.smart_assistant.tools.tool_editor import EditorController
from transbridge.ui.tools.smart_assistant.request_binding import RequestBinding


def _entries():
    return tuple(
        TranslationEntry("same", "same", original, f"old-{original}", 1, "QUST:CNAM", requires_original_match=True)
        for original in ("first", "second")
    )


def test_assistant_query_ids_support_exact_edit_stage_and_selection():
    first, second = _entries()
    collection = TranslationEntryCollection((first, second))
    selected = set()

    def select_entries(ids, action):
        selected.update(ids)
        return len(selected)

    context = SimpleNamespace(
        collection=collection,
        filter_state={},
        entry_labels={},
        publish_collection_modified=lambda: None,
        select_entries=select_entries,
        selected_ids=selected,
    )
    controller = EditorController()
    rows = controller.get_visible_entries({}, context, collection).data["entries"]
    first_id = rows[0]["key"]
    assert first_id == first.identity.serialize()
    assert rows[1]["key"] == second.identity.serialize()
    assert controller.edit_translation({"entry_id": first_id, "new_translation": "chosen"}, context, collection).success
    assert (first.translation, second.translation) == ("chosen", "old-second")
    assert controller.set_stage({"entry_ids": [first_id], "stage": 3}, context, collection).success
    assert (first.stage, second.stage) == (3, 1)
    assert controller.select_entries({"entry_ids": [first_id]}, context).success
    assert selected == {first_id}
    assert not controller.select_entries({"entry_ids": ["same"]}, context).success
    with pytest.warns(RuntimeWarning, match="ambiguous"):
        assert not controller.edit_translation(
            {"entry_id": "same", "new_translation": "wrong"}, context, collection
        ).success
    assert (first.id, first.key) == ("same", "same")


def test_captured_selection_roundtrip_keeps_only_selected_original():
    first, second = _entries()
    collection = TranslationEntryCollection((first, second))
    app = SimpleNamespace(selected_entries=(first,), filter_state={}, translation_scope={})
    selection = RequestBinding._selection(SimpleNamespace(facade=SimpleNamespace(_ctx=app)))
    assert selection["selected_entry_ids"] == [first.identity.serialize()]
    context = SimpleNamespace(_target_collection=collection)
    request = SimpleNamespace(source_message_ids=("message",), execution_version_json="")
    apply_captured_scope(request, [{"message_id": "message", "selection": selection}], context)
    assert context.selected_entries == (first,)
    selection["selected_entry_ids"] = ["same"]
    with pytest.raises(RequestError, match="REQUEST_SCOPE_MISMATCH"):
        apply_captured_scope(request, [{"message_id": "message", "selection": selection}], context)


def test_postprocess_scope_preserves_both_original_qualified_entries():
    entries = _entries()
    collection = TranslationEntryCollection(entries)
    context = SimpleNamespace(translation_scope={"stages": [1]}, entry_labels={})
    assert resolve_scope_to_entry_ids(context, collection) == [entry.identity.serialize() for entry in entries]
    resolved, scope = _resolve_entries({"scope": "set_scope"}, context, collection, SimpleNamespace())
    assert resolved == entries
    assert scope == "set_scope"


def test_assistant_polish_applies_each_accepted_original_result(monkeypatch):
    entries = _entries()
    collection = TranslationEntryCollection(entries)
    results = {
        entry.identity.serialize(): SimpleNamespace(
            accepted=True, polished_translation=f"new-{entry.original}", target_stage=3
        )
        for entry in entries
    }
    monkeypatch.setattr(
        "transbridge.ai_translator.post_processor.proofread_pipeline.ProofreadPipeline.create",
        lambda **kwargs: SimpleNamespace(process=lambda *args, **options: results),
    )
    summary = execute_polish(
        strategy="proofread",
        intensity="medium",
        llm_config=LLMConfig(),
        llm_client=object(),
        term_manager=object(),
        targets=list(entries),
        collection=collection,
        stop_event=None,
    )
    assert (summary.polished_count, summary.failed_count) == (2, 0)
    assert [(entry.original, entry.translation, entry.stage) for entry in collection] == [
        ("first", "new-first", 3),
        ("second", "new-second", 3),
    ]


@pytest.mark.parametrize("with_ordinary", [False, True])
@pytest.mark.parametrize("force_overwrite", [False, True])
def test_assistant_upload_skips_original_matched_entries_before_remote_write(
    monkeypatch, caplog, with_ordinary, force_overwrite
):
    entries = _entries()
    ordinary = TranslationEntry("normal", "normal", "plain", "translated", 1, "BOOK:FULL")
    collection = TranslationEntryCollection((*entries, ordinary) if with_ordinary else entries)
    sent, committed = [], []

    def upsert(project_id, entry, **kwargs):
        sent.append(entry)
        return replace(entry, remote_id=7)

    monkeypatch.setattr(
        "transbridge.smart_assistant.tools._entry_upload.ProjectToolTarget.capture",
        lambda context: SimpleNamespace(check=lambda: None, commit_records=lambda rows: committed.extend(rows)),
    )
    with caplog.at_level(logging.WARNING):
        result = upload_entries(
            {"force_overwrite": force_overwrite},
            SimpleNamespace(),
            collection,
            SimpleNamespace(upsert_entry=upsert),
            11,
            None,
        )
    assert result.success and result.partial
    assert result.data["skipped"] == 2
    assert result.data["uploaded"] == int(with_ordinary)
    assert result.data["total"] == 2 + int(with_ordinary)
    assert result.warnings
    assert "PARATRANZ_ORIGINAL_MATCH_SKIPPED" in caplog.text
    assert [entry.key for entry in sent] == (["normal"] if with_ordinary else [])
    assert all(isinstance(entry, ParaTranzEntry) for entry in sent)
    assert [entry.key for entry in committed] == (["normal"] if with_ordinary else [])
