"""Registered Assistant translation tools run the desktop executor with offline model responses."""

from __future__ import annotations

from dataclasses import replace
from functools import wraps
import json
from time import monotonic
from types import SimpleNamespace

from PyQt6.QtTest import QTest
import pytest

from tests.conftest import make_entry
from transbridge.ai_translator.translation_entry_outcomes import TranslationEntryOutcome
from transbridge.ai_translator.translator import AutoTranslator, TranslationResult
from transbridge.application.security.diagnostic_logging import credential_diagnostics
from transbridge.application.translation.ai_execution_profile import apply_profile_settings
from transbridge.application.translation.custom_workflow_profile import CustomWorkflowProfile
from transbridge.application.translation.entry_alias import ai_entry_key
from transbridge.application.translation.task_entry_results import TaskEntryResults
from transbridge.application.translation.task_scope import SourceTask, task_entry_groups
from transbridge.config.ai_workflow_profiles import AiWorkflowProfileRepository
from transbridge.config.llm import LLMConfig
from transbridge.config.paratranz import ParatranzConfig
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.paratranz.config_manager import ActionRule
from transbridge.persistence.task_store import TaskStore
from transbridge.smart_assistant.tool_registry import ToolRegistry
from transbridge.smart_assistant.tools import register_all
from transbridge.smart_assistant.tools.task_manager import TaskManager
from transbridge.smart_assistant.tools.translation_task_results import result_view
from transbridge.smart_assistant.tools.types import ExecutionContext
from transbridge.ui.tools.ai_translator.run_request_preparation import prepare_run_request
from transbridge.ui.tools.ai_translator.source_execution import SourceExecutor
from transbridge.ui.tools.ai_translator.task_worker import AiTaskWorker


class _Provider:
    def __init__(self, respond):
        self.respond = respond
        self.calls = []

    def chat(self, messages, max_tokens=0):
        del max_tokens
        entries = json.loads(messages[1]["content"])["entries"]
        self.calls.append(entries)
        return json.dumps({"results": [dict(entry_key=e["entry_key"], **self.respond(e)) for e in entries]})


@pytest.fixture
def run_task(current_project_factory, tmp_path, monkeypatch):
    register_all()
    TaskManager.reset()
    manager = TaskManager()
    config = LLMConfig(api_key="fixture", model="fixture", retrieval_enabled=False, max_tokens_per_batch=10_000)
    monkeypatch.setattr(LLMConfig, "load_from_file", lambda: config)
    monkeypatch.setattr(ParatranzConfig, "get_data_dir", lambda: str(tmp_path))

    def start(name, entries, *, respond=None, args=None, config_override=None):
        if config_override is not None:
            monkeypatch.setattr(LLMConfig, "load_from_file", lambda: config_override)
        project = current_project_factory(entries)
        app = project.context
        app.config = ParatranzConfig(token=None, environment={})
        app.task_store = TaskStore(tmp_path / "tasks")
        # CurrentEditingProject owns an open, unsaved variant with no persisted project catalog.
        monkeypatch.setattr(project.commands, "project_exists", lambda key: key == app.active_project_id)
        monkeypatch.setattr(project.commands, "result_receipt", lambda *_args: None)
        monkeypatch.setattr(
            project.commands,
            "_categorized_uploads",
            SimpleNamespace(
                result_source_texts=lambda _project, _variant, keys, _context: {
                    e.identity: (e.original, e.context or "") for e in entries if e.identity in keys
                }
            ),
        )
        request = replace(app.runtime_context, session_id="assistant-test")
        ctx = ExecutionContext(app_context=app, request_context=request)
        provider = _Provider(respond)
        monkeypatch.setattr("transbridge.infra.llm_client.create_llm_client", lambda *_args, **_kwargs: provider)
        result = ToolRegistry.get(name).execute(args or {}, ctx)
        assert result.success, result.message
        handle = manager.get_handle(result.data["task_id"])
        deadline = monotonic() + 8
        while handle._thread.is_alive() and monotonic() < deadline:
            QTest.qWait(5)
        assert not handle._thread.is_alive()
        return project, result_view(ctx, result.data["result_id"]), provider.calls

    yield start
    TaskManager.reset()


def _trace_stages(monkeypatch):
    stages = []
    for name in ("prepare_names", "execute", "close_terms"):
        method = getattr(SourceExecutor, name)

        @wraps(method)
        def traced(self, *args, _name=name, _method=method, **kwargs):
            stages.append((_name, self.request.run_id, self.request.request_budget))
            return _method(self, *args, **kwargs)

        monkeypatch.setattr(SourceExecutor, name, traced)
    return stages


def _check_stages(stages):
    assert [name for name, _run, _budget in stages] == ["prepare_names", "execute", "close_terms"]
    assert len({run for _name, run, _budget in stages}) == 1
    assert all(budget is stages[0][2] for _name, _run, budget in stages)
    return stages[0][2]


def test_registered_translation_uses_desktop_preparation_source_and_closure(run_task, monkeypatch):
    stages = _trace_stages(monkeypatch)
    entries = [make_entry("keep", original="One"), make_entry("outside", original="Two")]

    def translate(self, *, collection, target_entry_ids, strict_target_scope, **_):
        assert strict_target_scope and target_entry_ids == ["keep"]
        (key,) = target_entry_ids
        entry = collection.get(key)
        return TranslationResult(
            entry_outcomes={entry.identity: TranslationEntryOutcome(entry.identity, "succeeded", "一", 1)}
        )

    monkeypatch.setattr(AutoTranslator, "translate", translate)
    project, saved, _ = run_task("start_translation", entries, args={"entry_ids": ["keep"]})
    _check_stages(stages)
    assert saved["state"] == "completed" and [row["text"] for row in saved["results"]] == ["一"]
    assert [project.context.collection.get(entry.identity).translation for entry in entries] == ["一", ""]


def test_registered_polish_batches_original_qualified_rows_and_logs_one_budgeted_call(run_task, tmp_path, monkeypatch):
    stages = _trace_stages(monkeypatch)
    entries = [
        TranslationEntry("same", "same", original, f"旧译{original}", 1, "QUST:CNAM", requires_original_match=True)
        for original in ("甲", "乙")
    ]
    project, saved, calls = run_task(
        "start_polish", entries, respond=lambda entry: {"final_translation": "校对" + entry["current_translation"]}
    )
    budget = _check_stages(stages)
    assert budget.snapshot().peak_in_flight == 1
    assert len(calls) == 1 and len(calls[0]) == 2
    assert sorted((row["entry_key"]["original"], row["text"]) for row in saved["results"]) == [
        ("乙", "校对旧译乙"),
        ("甲", "校对旧译甲"),
    ]
    assert all(project.context.collection.get(e.identity).translation == "校对" + e.translation for e in entries)
    (log,) = tuple(tmp_path.rglob("proofread_call_*.log"))
    content = log.read_text(encoding="utf-8")
    assert all(marker in content for marker in ("[REQUEST TO LLM]", "[RESPONSE FROM LLM]", "[REQUEST BUDGET]"))


def test_registered_polish_retries_provider_error_and_preserves_originals(run_task, tmp_path):
    entries = [make_entry("first", "First", "旧译一", 1), make_entry("second", "Second", "旧译二", 1)]

    def fail(_entry):
        raise TimeoutError("provider timeout")

    project, saved, calls = run_task("start_polish", entries, respond=fail)
    assert len(calls) == 2
    assert saved["state"] == "failed" and not saved["results"]
    assert [project.context.collection.get(e.identity).translation for e in entries] == ["旧译一", "旧译二"]
    logs = list(tmp_path.rglob("proofread_call_*.log"))
    assert len(logs) == 2 and all("provider timeout" in log.read_text(encoding="utf-8") for log in logs)


def test_failed_source_keeps_provider_credential_out_of_saved_task_error(run_task, monkeypatch):
    class OfflineProvider:
        def __init__(self):
            self._api_key = "fixture-secret-for-this-request"

        @credential_diagnostics
        def chat(self):
            raise RuntimeError(f"offline provider failed with {self._api_key}")

    provider = OfflineProvider()
    monkeypatch.setattr(SourceExecutor, "_polish", lambda *_: provider.chat())
    _, saved, _ = run_task("start_polish", [make_entry("key", "Source", "译文", 1)])
    assert saved["state"] == "failed" and "offline provider failed" in saved["error"]
    assert provider._api_key not in saved["error"]


def test_mixed_tool_and_desktop_worker_publish_identical_entry_candidates(run_task, monkeypatch, tmp_path):
    entries = [make_entry("new", "New"), make_entry("draft", "Draft", "初稿", 1)]
    config = LLMConfig(
        api_key="fixture",
        model="fixture",
        retrieval_enabled=False,
        action_rules=[
            ActionRule(status_filter={0}, action="translate"),
            ActionRule(status_filter={1}, action="polish"),
        ],
    )

    def translate(_self, *, collection, target_entry_ids, **_kwargs):
        (entry,) = (e for e in collection if ai_entry_key(e) in target_entry_ids)
        return TranslationResult(
            entry_outcomes={entry.identity: TranslationEntryOutcome(entry.identity, "succeeded", "新译", 1)}
        )

    monkeypatch.setattr(AutoTranslator, "translate", translate)
    _, saved, calls = run_task(
        "start_translation",
        entries,
        args={"mode": "mixed", "entry_ids": ["new", "draft"]},
        respond=lambda _: {"final_translation": "校对稿"},
        config_override=config,
    )
    assert saved["state"] == "completed" and len(calls) == 1
    desktop_config = config.copy_for_execution()
    apply_profile_settings(desktop_config, "mixed")
    groups = task_entry_groups(entries, mode="mixed", config=desktop_config)
    source = SourceTask("fixture.esp", "Fixture", None, TranslationEntryCollection(entries), *groups)
    prepared = prepare_run_request("desktop", "mixed", desktop_config, source.entries)
    request = replace(prepared.execution_request(), recovery_directory=tmp_path / "desktop")
    worker = AiTaskWorker(request, (source,))
    outcomes = []
    worker.completed.connect(outcomes.extend)
    worker.run()
    ledger = TaskEntryResults((source,), request.run_id)
    ledger.ingest(outcomes, set(ledger.entries))
    assert [row.status for row in ledger.entries.values()] == ["succeeded", "succeeded"]
    assert sorted((row["text"], row["stage"]) for row in saved["results"]) == sorted(
        (row.text, row.stage) for row in ledger.entries.values()
    )


@pytest.mark.parametrize("initial_stage", [1, 2])
@pytest.mark.parametrize("damaged", [False, True])
@pytest.mark.parametrize("explicit_review", [False, True])
def test_registered_polish_preserves_text_and_applies_checked_stage(run_task, initial_stage, damaged, explicit_review):
    entry = make_entry("first", "Hello {name}", "你好 {name}", initial_stage)

    def respond(row):
        return {
            "final_translation": "你好" if damaged else row["current_translation"],
            **({"review_status": "retained", "review_reason": "Source preserved."} if explicit_review else {}),
        }

    project, saved, _ = run_task("start_polish", [entry], respond=respond)
    expected_stage = 2 if damaged or not explicit_review else initial_stage
    (row,) = saved["results"]
    assert (row["text"], row["stage"]) == (entry.translation, expected_stage)
    actual = project.context.collection.get(entry.identity)
    assert actual.stage == expected_stage and actual.translation == entry.translation
    if damaged:
        assert "PROOFREAD_SYNTAX_REVIEW_REQUIRED" in saved["execution"][0]["reason"]


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [({}, ("strict", "light")), ({"strategy": "proofread", "intensity": "heavy"}, ("proofread", "aggressive"))],
)
def test_registered_polish_uses_saved_profile_unless_explicitly_overridden(run_task, monkeypatch, overrides, expected):
    entry = make_entry("first", "First", "旧译", 1)
    profile = {"pp_strategy": "strict", "pp_polish_level": "light"}
    config = LLMConfig(
        api_key="fixture", model="fixture", retrieval_enabled=False, workflow_profiles={"polish": profile}
    )
    captured = []

    def no_pipeline(*args, profile, **kwargs):
        captured.append((profile.postprocess_strategy, profile.polish_level))
        raise RuntimeError("fixture pipeline unavailable")

    monkeypatch.setattr(
        "transbridge.ui.tools.ai_translator.proofread_composition.build_proofread_pipeline", no_pipeline
    )
    _, saved, _ = run_task("start_polish", [entry], args=overrides, config_override=config)
    assert captured == [expected]
    assert config.workflow_profiles == {"polish": {"pp_strategy": "strict", "pp_polish_level": "light"}}
    assert saved["state"] == "failed" and saved["results"] == []


@pytest.mark.parametrize("reference", ["name", "id"])
def test_registered_polish_runs_named_desktop_workflow(run_task, tmp_path, monkeypatch, reference):
    repository = AiWorkflowProfileRepository(tmp_path / "profiles.json")
    selected = CustomWorkflowProfile.from_config("Translate", "translate", LLMConfig())
    repository.upsert(selected, select=True)
    named = CustomWorkflowProfile.from_config("Review dialogue", "polish", LLMConfig(pp_strategy="proofread"))
    repository.upsert(named)
    monkeypatch.setattr(
        "transbridge.smart_assistant.tools.translation_options.AiWorkflowProfileRepository", lambda: repository
    )
    base = LLMConfig(api_key="fixture", model="fixture", workflow_profiles={"polish": {"enable_post_process": False}})
    entry = make_entry("line", "Dialogue", "待校对", 1)
    _, saved, calls = run_task(
        "start_polish",
        [entry],
        args={"profile": named.id if reference == "id" else named.name},
        respond=lambda _: {"final_translation": "已校对"},
        config_override=base,
    )
    assert len(calls) == 1 and saved["state"] == "completed"
    assert [row["text"] for row in saved["results"]] == ["已校对"]
    assert base.workflow_profiles["polish"]["enable_post_process"] is False
    assert repository.selected().id == selected.id
