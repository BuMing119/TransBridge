"""Real commands retain parser and dictionary candidates across local changes."""

from dataclasses import replace
from pathlib import Path

import pytest

from tests.smart_assistant.tools.test_dictionary_upload_authority import _install_dictionary, _two_entries
from tests.smart_assistant.tools.test_source_import_authority import load_source, project as project_fixture, write_json
from transbridge.smart_assistant.tools import _dictionary_application as dictionary_module
from transbridge.smart_assistant.tools._project_tool_mutations import ProjectToolTarget
from transbridge.smart_assistant.tools.entry_task_results import EntryResultStore
from transbridge.smart_assistant.tools.tool_migrator import _tool_apply_dictionary
from transbridge.smart_assistant.tools.tool_parser import _tool_import_json, _tool_parse_eet
from transbridge.smart_assistant.tools.tool_translator import _tool_get_task_result, _tool_merge_task_result
from transbridge.smart_assistant.tools.types import ExecutionContext

project = project_fixture
_LOCALES = {"source_locale": "en", "target_locale": "zh-CN"}


def test_dictionary_unrelated_edit_is_preserved(project, monkeypatch):
    first, second = _two_entries(project)
    _install_dictionary(project, monkeypatch, first)
    query = dictionary_module.TranslationMemoryQueryService.query
    edited = False

    def concurrent(service, request, cancellation=None):
        nonlocal edited
        selected = query(service, request, cancellation)
        if not edited:
            edited = True
            ProjectToolTarget.capture(ExecutionContext(app_context=project.ctx)).commit_records((
                replace(second, translation="Unrelated", stage=1),
            ))
        return selected

    monkeypatch.setattr(dictionary_module.TranslationMemoryQueryService, "query", concurrent)
    result = _tool_apply_dictionary(_LOCALES, ExecutionContext(app_context=project.ctx))
    assert result.success, result.message
    assert project.ctx.collection.get(first.identity).translation == "Dictionary value"
    assert project.ctx.collection.get(second.identity).translation == "Unrelated"


def test_dictionary_save_failure_can_retry_without_dictionary(project, monkeypatch):
    entry = load_source(project)
    _install_dictionary(project, monkeypatch, entry)
    create = EntryResultStore.create
    monkeypatch.setattr(EntryResultStore, "create", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    result = _tool_apply_dictionary(_LOCALES, ExecutionContext(app_context=project.ctx))
    assert result.partial and not result.success
    assert next(iter(project.ctx.collection)).translation == ""
    monkeypatch.setattr(EntryResultStore, "create", create)
    monkeypatch.setattr(
        dictionary_module.TranslationMemoryQueryService, "query", lambda *a: pytest.fail("must not query")
    )
    merged = _tool_merge_task_result({"result_id": result.data["result_id"]}, ExecutionContext(app_context=project.ctx))
    assert merged.success, merged.message
    assert next(iter(project.ctx.collection)).translation == "Dictionary value"


def test_source_save_failure_recovery_uses_captured_content(project, monkeypatch):
    create = EntryResultStore.create
    monkeypatch.setattr(EntryResultStore, "create", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    result = _tool_parse_eet({"path": str(project.source)}, ExecutionContext(app_context=project.ctx))
    assert result.partial and not result.success
    project.source.write_text("changed after parsing", encoding="utf-8")
    monkeypatch.setattr(EntryResultStore, "create", create)
    merged = _tool_merge_task_result({"result_id": result.data["result_id"]}, ExecutionContext(app_context=project.ctx))
    assert merged.success, merged.message
    assert len(project.ctx.collection) == 1
    assert project.ctx.active_slot.source_snapshot.content != b"changed after parsing"


def test_append_preserves_refs_and_rejects_locator_replacement(project):
    entry = load_source(project)
    row = entry.to_dict()
    row.update(translation="Imported", stage=1)
    path = write_json(project, "candidate.json", [row])
    result = _tool_import_json({"path": path, "action": "append"}, ExecutionContext(app_context=project.ctx))
    assert result.success, result.message
    assert project.ctx.collection.get(entry.identity).external_refs == entry.external_refs
    row["string_id"] = 42
    path = write_json(project, "bad.json", [row])
    result = _tool_import_json({"path": path, "action": "append"}, ExecutionContext(app_context=project.ctx))
    assert not result.success and result.data["unmapped_count"] == 1
    assert project.ctx.collection.get(entry.identity).string_id == entry.string_id


def test_source_receipt_failure_retry_recognizes_committed_source(project, monkeypatch):
    update = EntryResultStore.update_metadata

    def failed_receipt(store, task_id, **fields):
        if fields.get("registered"):
            raise OSError("receipt disk full")
        return update(store, task_id, **fields)

    monkeypatch.setattr(EntryResultStore, "update_metadata", failed_receipt)
    result = _tool_parse_eet({"path": str(project.source)}, ExecutionContext(app_context=project.ctx))
    assert result.success and result.warnings
    assert len(project.ctx.project_sources) == 1
    project.source.write_text("changed after registration", encoding="utf-8")
    monkeypatch.setattr(EntryResultStore, "update_metadata", update)
    recovered = _tool_merge_task_result(
        {"result_id": result.data["result_id"]}, ExecutionContext(app_context=project.ctx)
    )
    assert recovered.success, (recovered.message, project.ctx.project_sources)
    assert recovered.data["merge"]["registered"] and len(project.ctx.project_sources) == 1


def test_source_locator_change_is_pending_under_prefer_incoming():
    from transbridge.application.io.identity import EntryKey, SourceNamespace
    from transbridge.application.translation.result_merge import ResultEntryState, merge_result_entries

    key = EntryKey(SourceNamespace.legacy(), "key")
    base = ResultEntryState(key, "Original", "", 0, source_locator="old locator")
    current = replace(base, source_locator="new locator")
    incoming = replace(base, translation="Result", stage=1)
    plan = merge_result_entries({key: base}, {key: current}, {key: incoming}, policy="prefer_incoming")
    assert not plan.applied and plan.pending[0].reason == "source_changed_or_missing"


@pytest.mark.parametrize("save_failure", [False, True])
def test_partial_paratranz_source_preserves_candidates_diagnostics_and_bytes(project, monkeypatch, save_failure):
    from base64 import b64decode

    path = write_json(
        project,
        "partial.json",
        [
            {"id": 1, "key": "good", "original": "Good", "translation": "Value", "stage": 1},
            {"id": 2, "key": "bad", "original": "Bad", "translation": "Invalid", "stage": "unsupported"},
        ],
    )
    original = Path(path).read_bytes()
    create = EntryResultStore.create
    if save_failure:
        monkeypatch.setattr(EntryResultStore, "create", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    ctx = ExecutionContext(app_context=project.ctx)
    result = _tool_import_json({"path": path, "format": "paratranz"}, ctx)
    assert result.partial and bool(result.success) is not save_failure
    monkeypatch.setattr(EntryResultStore, "create", create)
    if save_failure:
        Path(path).unlink()
        recovered = _tool_merge_task_result({"result_id": result.data["result_id"]}, ctx)
        assert recovered.success and recovered.partial, recovered.message
    viewed = _tool_get_task_result({"result_id": result.data["result_id"]}, ctx)
    assert viewed.success and viewed.data["state"] == "partial"
    assert viewed.data["parse_outcome"] == "partial" and viewed.data["parse_diagnostics"]
    assert [entry["key"] for entry in viewed.data["entries"]] == ["good"]
    assert b64decode(viewed.data["input_snapshot"]["content"]) == original
    assert len(project.ctx.collection) == 1


def test_partial_eet_source_reports_missing_locators(project):
    xml = project.source.read_text(encoding="utf-8")
    block = xml[xml.index("<ESP>") : xml.index("</ESP>") + len("</ESP>")]
    second = block.replace("GreetingTopic", "SecondTopic").replace("00000001", "00000002")
    project.source.write_text(
        xml.replace("</DocumentElement>", block + second + "</DocumentElement>"), encoding="utf-8"
    )
    result = _tool_parse_eet({"path": str(project.source)}, ExecutionContext(app_context=project.ctx))
    assert result.success and result.partial, result.message
    assert result.data["entry_count"] == 1
    assert result.data["parse_diagnostics"][0]["code"] == "SOURCE_LOCATOR_CONFLICT"
    viewed = _tool_get_task_result({"result_id": result.data["result_id"]}, ExecutionContext(app_context=project.ctx))
    assert viewed.data["state"] == "partial" and viewed.data["parse_diagnostics"]


def test_internal_json_ambiguous_identity_keeps_all_raw_candidates_and_merges_other_entry(project):
    from base64 import b64decode
    from pathlib import Path

    first, second = _two_entries(project)
    rows = [{**first.to_dict(), "translation": text, "stage": 1} for text in ("One", "Two")]
    rows.append({**second.to_dict(), "translation": "Independent", "stage": 1})
    path = write_json(project, "duplicates.json", rows)
    content = Path(path).read_bytes()
    ctx = ExecutionContext(app_context=project.ctx)
    result = _tool_import_json({"path": path, "action": "append"}, ctx)
    assert result.partial and result.data["unmapped_count"] == 2
    assert project.ctx.collection.get(first.identity).translation == ""
    assert project.ctx.collection.get(second.identity).translation == "Independent"
    Path(path).unlink()
    viewed = _tool_get_task_result({"result_id": result.data["result_id"]}, ctx)
    assert viewed.success and viewed.data["raw_entries"] == rows
    assert {row["translation"] for row in viewed.data["unmapped"]} == {"One", "Two"}
    assert b64decode(viewed.data["input_snapshot"]["content"]) == content


@pytest.mark.parametrize("lose_memory_receipt", [False, True])
def test_source_receipt_failure_then_user_removal_cannot_resurrect_source(project, monkeypatch, lose_memory_receipt):
    from transbridge.smart_assistant.tools import _source_import as module

    update = EntryResultStore.update_metadata

    def fail_committed_receipt(store, task_id, **fields):
        if fields.get("registered"):
            raise OSError("receipt disk full")
        return update(store, task_id, **fields)

    monkeypatch.setattr(EntryResultStore, "update_metadata", fail_committed_receipt)
    result = _tool_parse_eet({"path": str(project.source)}, ExecutionContext(app_context=project.ctx))
    assert result.success and result.warnings
    assert project.ctx.project_commands.remove_source(str(project.source), project.request).is_success
    assert not project.ctx.project_sources
    if lose_memory_receipt:
        monkeypatch.setattr(module, "_SOURCE_RECEIPTS", {})
    monkeypatch.setattr(EntryResultStore, "update_metadata", update)
    recovered = _tool_merge_task_result(
        {"result_id": result.data["result_id"]}, ExecutionContext(app_context=project.ctx)
    )
    assert not recovered.success and not project.ctx.project_sources


def test_review_without_optional_locator_accepts_explicit_choice_but_detects_new_edit():
    from transbridge.application.io.identity import EntryKey, SourceNamespace
    from transbridge.application.translation.result_merge import ResultEntryState, merge_result_entries

    key = EntryKey(SourceNamespace.legacy(), "key")
    base = ResultEntryState(key, "Original", "Base", 1)
    current = replace(base, translation="Manual", source_locator="installed locator")
    reviewed = replace(current, source_locator=None)
    incoming = replace(base, translation="Candidate")
    plan = merge_result_entries(
        {key: base}, {key: current}, {key: incoming}, policy="prefer_incoming", decision_states={key: reviewed}
    )
    assert len(plan.applied) == 1 and not plan.pending
    current = replace(current, translation="Edited after review")
    stale = merge_result_entries(
        {key: base}, {key: current}, {key: incoming}, policy="prefer_incoming", decision_states={key: reviewed}
    )
    assert not stale.applied and stale.pending[0].reason == "decision_stale"
