"""Immediate source registration and import use desktop atomicity and conflict rules."""

from dataclasses import replace

from tests.smart_assistant.tools.test_source_import_authority import load_source, project as project_fixture, write_json
from transbridge.smart_assistant.tools.tool_parser import _tool_import_json, _tool_parse_eet
from transbridge.smart_assistant.tools.types import ExecutionContext

project = project_fixture


def test_source_locator_change_is_pending_under_prefer_incoming():
    from transbridge.application.io.identity import EntryKey, SourceNamespace
    from transbridge.application.translation.result_merge import ResultEntryState, merge_result_entries

    key = EntryKey(SourceNamespace.legacy(), "key")
    base = ResultEntryState(key, "Original", "", 0, source_locator="old locator")
    current = replace(base, source_locator="new locator")
    incoming = replace(base, translation="Result", stage=1)
    plan = merge_result_entries({key: base}, {key: current}, {key: incoming}, policy="prefer_incoming")
    assert not plan.applied and plan.pending[0].reason == "source_changed_or_missing"


def test_partial_paratranz_source_rejects_registration(project):
    path = write_json(
        project,
        "partial.json",
        [
            {"id": 1, "key": "good", "original": "Good", "translation": "Value", "stage": 1},
            {"id": 2, "key": "bad", "original": "Bad", "translation": "Invalid", "stage": "unsupported"},
        ],
    )
    result = _tool_import_json({"path": path, "format": "paratranz"}, ExecutionContext(app_context=project.ctx))
    assert not result.success and "PARATRANZ" in result.message
    assert not project.ctx.project_sources


def test_partial_eet_source_reports_missing_locators(project):
    xml = project.source.read_text(encoding="utf-8")
    block = xml[xml.index("<ESP>") : xml.index("</ESP>") + len("</ESP>")]
    second = block.replace("GreetingTopic", "SecondTopic").replace("00000001", "00000002")
    project.source.write_text(
        xml.replace("</DocumentElement>", block + second + "</DocumentElement>"), encoding="utf-8"
    )
    result = _tool_parse_eet({"path": str(project.source)}, ExecutionContext(app_context=project.ctx))
    assert not result.success and "SOURCE_LOCATOR_CONFLICT" in result.message
    assert not project.ctx.project_sources


def test_internal_json_ambiguous_identity_rejects_entire_import(project):
    xml = project.source.read_text(encoding="utf-8")
    block = xml[xml.index("<ESP>") : xml.index("</ESP>") + len("</ESP>")]
    second_block = block.replace("GreetingTopic", "SecondTopic").replace("00000001", "00000002")
    project.source.write_text(xml.replace("</DocumentElement>", second_block + "</DocumentElement>"), encoding="utf-8")
    load_source(project)
    first, second = project.ctx.collection
    rows = [{**first.to_dict(), "translation": text, "stage": 1} for text in ("One", "Two")]
    rows.append({**second.to_dict(), "translation": "Independent", "stage": 1})
    path = write_json(project, "duplicates.json", rows)
    ctx = ExecutionContext(app_context=project.ctx)
    result = _tool_import_json({"path": path, "action": "append"}, ctx)
    assert not result.success and "MIGRATION_SOURCE_KEY_DUPLICATE" in result.message
    assert all(not entry.translation for entry in project.ctx.collection)


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
