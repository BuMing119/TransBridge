"""Saved original text completes assistant runs without optional reference files."""

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.application.projects.test_known_original_consumers import _saved
from tests.persistence.v2.test_saved_project_sources import CountingPreparer, _services
from transbridge.application.projects.entry_source_text import known_original_entries
from transbridge.application.translation.task_result_recovery import restore_recovery_results
from transbridge.config.llm import LLMConfig
from transbridge.config.task_results import TaskResultPreferences
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.smart_assistant.tools.translation_task_results import (
    AssistantTaskResults,
    load_task_result,
    result_view,
)
from transbridge.ui.tools.ai_translator.source_terms import source_terminology_path


@pytest.fixture
def saved_only(tmp_path):
    _source, project_path, context = _saved(tmp_path)
    document = json.loads(project_path.read_bytes())
    document["data"]["sources"] = []
    project_path.write_text(json.dumps(document), encoding="utf-8")
    services = _services(tmp_path / "store", CountingPreparer(forbidden=True))
    try:
        assert services.current_project_opener.open_path(project_path, context).is_success
        active = services.project_lifecycle.active
        collection = TranslationEntryCollection(known_original_entries(active.variant.snapshot().entries))
        key = "source:known"
        slot = SimpleNamespace(collection=collection, esp_path="", source_snapshot=None)
        app = SimpleNamespace(
            collection=collection,
            active_version_identity=(active.project_ref.identity.value, active.formal_variant_ref.identity.value),
            project_commands=services.gui_project_commands,
            runtime_context=context,
            slots={key: slot},
            active_slot=slot,
            active_key=key,
            esp_path="",
            variant_revision=active.variant.revision,
            task_result_preferences_provider=TaskResultPreferences,
        )
        yield app, services
    finally:
        services.close()


def _task(app, mode):
    return AssistantTaskResults(
        app,
        app.collection,
        tuple(app.collection),
        LLMConfig(api_key="fixture-only", term_priority=[]),
        SimpleNamespace(snapshot_ref=None),
        mode=mode,
    )


def _complete(task):
    task.ledger.entries = {
        key: replace(row, status="succeeded", text=f"AI:{row.before.original}", stage=1)
        for key, row in task.ledger.entries.items()
    }
    task.persist(state="completed")


@pytest.mark.parametrize("mode", ["translate", "polish", "postprocess"])
def test_saved_original_capture_wire_and_merge_preserve_unknown_context(saved_only, mode):
    app, services = saved_only
    task = _task(app, mode)
    assert all(not row["context_known"] for row in task.record["sources"][0]["inputs"])
    _complete(task)
    _store, record, payload = load_task_result(app, task.result_id)
    assert all(row["before"]["context"] == "" and row["before"]["context_known"] is False for row in payload["rows"])
    restored, _remaining, count = restore_recovery_results(
        task.ledger.freeze(), (task.task,), {**record, "results": payload}
    )
    assert count == 2 and all(row.before.context is None for row in restored.entries.values())
    summary, warning = task.merge()
    assert summary["applied_count"] == 2 and summary["pending_count"] == 0 and not warning
    assert len(result_view(app, task.result_id)["results"]) == 2
    assert {row.translation for row in services.project_lifecycle.active.variant.snapshot().entries} == {
        "AI:First",
        "AI:Second",
    }


@pytest.mark.parametrize("evidence", ["old_empty", "known_empty", "known_context", "locator"])
def test_saved_original_merge_keeps_known_context_and_legacy_locator_guards(saved_only, evidence):
    app, _services_value = saved_only
    task = _task(app, "translate")
    _complete(task)
    store, _record, payload = load_task_result(app, task.result_id)
    for row in payload["rows"]:
        before = row["before"]
        if evidence == "old_empty":
            before.pop("context_known")
        elif evidence in {"known_empty", "known_context"}:
            before["context_known"] = True
            before["context"] = "" if evidence == "known_empty" else "Source-owned context"
        else:
            before["source_locator"] = "required source locator"
    store.save_results(task.result_id, payload["rows"])
    summary, _warning = task.merge()
    assert summary["applied_count"] == 0 and summary["pending_count"] == 2
    viewed = result_view(app, task.result_id)
    assert all(row["merge_reason"] == "source_changed_or_missing" for row in viewed["results"])


@pytest.mark.parametrize("mode", ["translate", "polish", "postprocess"])
def test_assistant_dynamic_terms_use_the_gui_source_cache_identity(saved_only, tmp_path, monkeypatch, mode):
    from transbridge.ai_translator.term_database import DynamicTermDatabase

    app, _services_value = saved_only
    monkeypatch.setattr(LLMConfig, "get_ai_translator_dir", lambda name: str(tmp_path / "cache" / name))
    task = _task(app, mode)
    expected = source_terminology_path(None, app.active_key)
    assert task.esp_path == expected and task.task.esp_path == expected
    gui = DynamicTermDatabase(expected)
    Path(gui._path).parent.mkdir(parents=True)
    gui.add("Dragon", "巨龙", "manual")
    gui.save()
    assistant = DynamicTermDatabase(task.esp_path)
    assistant.load()
    assert [(entry.term, entry.translation) for entry in assistant.as_list()] == [("Dragon", "巨龙")]
    other = replace(task.task, key="another-source")
    assert source_terminology_path(None, other.key) != task.esp_path


def test_filtered_captured_collection_keeps_its_source_identity_after_active_source_changes(saved_only):
    from transbridge.smart_assistant.tools.types import ExecutionContext

    app, _services_value = saved_only
    original_slot = app.active_slot
    original_slot.source_snapshot = SimpleNamespace(source=SimpleNamespace(display_name="Captured.esp"))
    ctx = ExecutionContext(app_context=app)
    filtered = TranslationEntryCollection((next(iter(app.collection)),))
    app.active_key = "later-source"
    app.esp_path = "later.esp"
    app.active_slot = SimpleNamespace(collection=TranslationEntryCollection(), esp_path=app.esp_path)
    app.slots[app.active_key] = app.active_slot
    task = AssistantTaskResults(
        ctx,
        filtered,
        tuple(filtered),
        LLMConfig(api_key="fixture-only", term_priority=[]),
        SimpleNamespace(snapshot_ref=None),
        mode="translate",
    )
    assert task.task.key == "source:known"
    assert task.esp_path == source_terminology_path(None, "source:known")
    assert task.report_context.active_slot.source_snapshot.source.display_name == "Captured.esp"


def test_postprocess_dispatch_keeps_saved_report_name_and_worker_directory(saved_only, tmp_path, monkeypatch):
    from transbridge.smart_assistant.tools._postprocess_tool_runtime import PostprocessToolRequest
    from transbridge.smart_assistant.tools.postprocess_task_run import launch_postprocess_task
    from transbridge.smart_assistant.tools.tool_proofreader import ProofreaderController, _resolve_report_directory

    app, _services_value = saved_only
    source = SimpleNamespace(display_name="SavedPlugin.esp", uri="old/SavedPlugin.esp")
    app.active_slot.source_snapshot = SimpleNamespace(source=source)
    lookup = SimpleNamespace(esp_path="", active_slot=SimpleNamespace(source_snapshot=SimpleNamespace(source=source)))
    monkeypatch.setattr(LLMConfig, "get_ai_translator_dir", lambda name: str(tmp_path / "reports" / name))
    request = PostprocessToolRequest(
        tuple(app.collection),
        LLMConfig(api_key="fixture", term_priority=[]),
        "polish",
        None,
        "polish",
        "proofread",
        ("proofread",),
        "all",
        "medium",
        {},
        SimpleNamespace(snapshot_ref=None),
    )
    monkeypatch.setattr(
        "transbridge.smart_assistant.tools.postprocess_task_run.resolve_postprocess_request", lambda *_args: request
    )
    workers = []
    manager = SimpleNamespace(
        register=lambda **_options: "job",
        get_handle=lambda _key: None,
        start_thread=lambda _key, callback: workers.append(callback),
        get_status=lambda _key: {"run_id": "job"},
    )
    directories = []

    def report_directory(context):
        directory = _resolve_report_directory(context)
        directories.append(directory)
        return directory

    def execute(task, _request, directory, *_args):
        target = directory()
        target.mkdir(parents=True)
        (target / "postprocess-report-fixture.xlsx").write_bytes(b"report fixture")
        task.persist(state="completed")

    monkeypatch.setattr("transbridge.smart_assistant.tools.postprocess_task_run.execute_task", execute)
    launched = launch_postprocess_task(
        {}, app, report_directory=report_directory, report_callback=lambda *_args: None, manager=manager
    )
    assert launched.success and directories == []
    app.active_slot.source_snapshot = SimpleNamespace(source=SimpleNamespace(display_name="Other.esp"))
    app.esp_path = "later.esp"
    workers[0]()
    assert directories == [_resolve_report_directory(lookup)]
    listed = ProofreaderController().list_quality_reports({}, lookup)
    assert [item["name"] for item in listed.data["files"]] == ["postprocess-report-fixture.xlsx"]


def test_real_saved_hydration_with_empty_context_keeps_the_known_context_guard(tmp_path, monkeypatch):
    from transbridge.application.contracts import RequestContext
    from transbridge.application.io import FormatId
    from transbridge.application.projects import ProjectProvisioningRequest, ProjectSourceRequest
    from transbridge.converter.translation_entry import TranslationEntry

    path = tmp_path / "no-context.json"
    path.write_text(json.dumps([{"key": "one", "original": "One", "translation": ""}]), encoding="utf-8")
    services = _services(tmp_path / "store")
    context = RequestContext("empty-source-context", run_id="capture")
    try:
        created = services.gui_project_commands.create_project(
            ProjectProvisioningRequest("No context", source=ProjectSourceRequest(str(path), FormatId.JSON_PARATRANZ)),
            context,
        )
        assert created.is_success, created.diagnostics
        active = services.project_lifecycle.active
        opened = services.current_project_opener.prepare_path(services.projects.path_for(active.project_ref), context)
        hydration = opened.value.hydrations[0]
        assert hydration.entries[0].context is None
        collection = TranslationEntryCollection(
            TranslationEntry(
                row.legacy_id,
                row.entry_key.local_key,
                row.original,
                row.translation,
                row.stage,
                row.context,
                entry_key=row.entry_key,
                metadata=row.metadata,
            )
            for row in hydration.entries
        )
        slot = SimpleNamespace(collection=collection, esp_path="", source_snapshot=hydration.source_snapshot)
        app = SimpleNamespace(
            collection=collection,
            active_version_identity=(active.project_ref.identity.value, active.formal_variant_ref.identity.value),
            project_commands=services.gui_project_commands,
            runtime_context=context,
            slots={"ready": slot},
            active_slot=slot,
            active_key="ready",
            esp_path="",
            variant_revision=active.variant.revision,
            task_result_preferences_provider=TaskResultPreferences,
        )
        task = _task(app, "translate")
        _complete(task)
        _store, _record, payload = load_task_result(app, task.result_id)
        assert payload["rows"][0]["before"]["context_known"] is True
        reader = services.gui_project_commands._categorized_uploads.result_source_texts

        def changed_context(*args):
            return {
                key: (text, "Now source-owned context", locator)
                for key, (text, _context, locator) in reader(*args).items()
            }

        monkeypatch.setattr(services.gui_project_commands._categorized_uploads, "result_source_texts", changed_context)
        summary, _warning = task.merge()
        assert summary["applied_count"] == 0 and summary["pending_count"] == 1
    finally:
        services.close()
