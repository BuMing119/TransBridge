"""Exercise task-owned terminology through the production translation and retry wiring."""

from dataclasses import replace
import json
import re
import threading
from types import SimpleNamespace

import pytest

from tests.conftest import make_entry
from transbridge.ai_translator.project_terminology_runtime import ProjectTerminologyBinding
from transbridge.ai_translator.term_database import DynamicTermDatabase
from transbridge.application.translation.ai_execution_profile import AiExecutionProfile
from transbridge.application.translation.ai_request_budget import AiRequestBudget
from transbridge.config.llm import LLMConfig
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.tools.ai_translator import source_execution
from transbridge.ui.tools.ai_translator.source_execution import SourceExecutor
from transbridge.ui.tools.ai_translator.task_consistency import TaskConsistency
from transbridge.ui.tools.ai_translator.task_scope import SourceTask


class _Provider:
    """Only the external model is replaced; prompt parsing and term extraction remain real."""

    retry_pending = False

    def chat(self, messages, max_tokens=0):
        content = messages[-1]["content"]
        translation = re.search(r"<translation_entries>\s*(.*?)\s*</translation_entries>", content, re.DOTALL)
        if translation:
            entries = json.loads(translation[1])
            return json.dumps({
                "results": [
                    {"entry_id": key, "translation": _TRANSLATIONS[item["source"]]}
                    for key, item in entries.items()
                    if key != "pending" or self.retry_pending
                ]
            })
        if content.startswith("{"):
            entries = json.loads(content)["entries"]
            return json.dumps({
                "results": [
                    {"entry_key": item["entry_key"], "final_translation": item["current_translation"]}
                    for item in entries
                ]
            })
        return json.dumps({"results": [{"term": "Whiterun", "translation": "雪漫城"}]})

    def chat_stream(self, messages, max_tokens, chunk_callback):
        response = self.chat(messages, max_tokens)
        chunk_callback(response)
        return response


_TRANSLATIONS = {
    "Whiterun": "雪漫城",
    "Travel to Whiterun.": "前往雪漫城。",
    "Continue the journey.": "继续旅程。",
}


@pytest.fixture
def term_write_run(tmp_path, monkeypatch):
    monkeypatch.setattr(LLMConfig, "get_ai_translator_dir", staticmethod(lambda _stem: str(tmp_path)))
    provider = _Provider()
    monkeypatch.setattr("transbridge.infra.llm_client.create_llm_client", lambda _: provider)
    monkeypatch.setattr("transbridge.infra.llm_reasoning.with_reasoning_intent", lambda client, *_: client)
    log_store = source_execution.WorkflowLogStore
    monkeypatch.setattr(
        source_execution,
        "WorkflowLogStore",
        lambda path, **kwargs: log_store(path, log_base=tmp_path / "logs", **kwargs),
    )

    def run(source, *, parallel_mixed=False, json_alias=None):
        term_priority = ["dynamic"] if json_alias is None else list(json_alias)
        config = LLMConfig(
            model="fake",
            max_concurrent=1,
            term_priority=term_priority,
            local_json_path="" if json_alias is None else str(tmp_path / "plugin_terms.json"),
            enable_semantic_match=False,
            mixed_execution_order="parallel" if parallel_mixed else "serial",
        )
        request = SimpleNamespace(
            run_id="term-write-run",
            config=config,
            terminology_binding=ProjectTerminologyBinding(),
            request_budget=AiRequestBudget(1),
            spec=SimpleNamespace(overwrite=False, execution_profile=AiExecutionProfile.from_config("mixed", config)),
        )
        guard = TaskConsistency(request)
        pause = threading.Event()
        pause.set()
        executor = SourceExecutor(
            request,
            stop_event=threading.Event(),
            pause_event=pause,
            shared_terms={},
            terms_lock=threading.Lock(),
            progress=lambda *_: None,
            log=lambda *_: None,
            consistency=guard,
        )
        seeded = source.startswith("existing_")
        is_name = source in {"existing_name", "auto_name"}
        original = "Whiterun" if is_name else "Travel to Whiterun."
        entry = make_entry(
            "written",
            original=original,
            translation=_TRANSLATIONS[original] if seeded else "",
            stage=1 if seeded else 0,
            context="NPC_:FULL" if is_name else "INFO:NAM1",
        )
        pending = make_entry("pending", original="Continue the journey.", context="BOOK:DESC")
        collection = TranslationEntryCollection([entry, pending])
        task = SourceTask(
            "plugin",
            "Plugin",
            "plugin.esp",
            collection,
            (pending,) if seeded else (entry, pending),
            (entry,) if parallel_mixed else (),
        )
        outcome = executor.execute(task)
        assert outcome.error == ""
        assert outcome.failed_keys == ("pending",)
        entry = collection.get(entry.identity)
        assert entry.translation == _TRANSLATIONS[original]
        database = DynamicTermDatabase(task.esp_path)
        database.load()
        assert [(term.term, term.translation, term.source) for term in database.as_list()] == [
            ("Whiterun", "雪漫城", source)
        ]
        return SimpleNamespace(
            executor=executor,
            request=request,
            guard=guard,
            task=task,
            entry=entry,
            pending=pending,
            provider=provider,
            outcome=outcome,
        )

    return run


@pytest.mark.parametrize("source", ["existing_name", "existing_text", "auto_name", "auto_dialogue"])
def test_internal_term_writes_survive_real_polish_retry_and_apply_guards(term_write_run, source):
    run = term_write_run(source)
    _assert_polish_retry_and_apply_succeed(run)


def _assert_polish_retry_and_apply_succeed(run):
    # The second stage creates and loads a fresh real TermDatabaseManager.
    polish = run.executor.execute(replace(run.task, translate_entries=(), polish_entries=(run.entry,)))
    assert polish.successful, polish.error
    assert polish.polish[run.entry.id].accepted
    run.guard.require_current(run.request)

    # Retry an actually failed entry with the same source and consistency guard.
    run.provider.retry_pending = True
    run.executor.attempt_id = "retry-attempt"
    retried = run.executor.execute(replace(run.task, translate_entries=(run.pending,)))
    assert retried.successful, retried.error
    assert retried.translation.success_count == 1
    assert run.task.collection.get(run.pending.identity).translation == "继续旅程。"
    run.guard.require_current(run.request)


@pytest.mark.parametrize(
    "priority", [("dynamic", "json"), ("json", "dynamic"), ("json",)], ids=["dynamic_first", "json_first", "json_only"]
)
def test_dynamic_file_used_as_reference_does_not_block_apply_after_external_edit(term_write_run, priority):
    run = term_write_run("existing_name", json_alias=priority)
    _assert_polish_retry_and_apply_succeed(run)

    external = DynamicTermDatabase(run.task.esp_path)
    external.load()
    external.add_many_and_save([("Whiterun", "外部修改", "manual", "NPC_:FULL")])
    run.guard.require_current(run.request)
    assert external.as_list()[0].translation == "外部修改"


@pytest.mark.parametrize("source", ["existing_name", "existing_text"])
def test_parallel_mixed_stages_accept_terms_seeded_inside_the_same_task(term_write_run, source):
    run = term_write_run(source, parallel_mixed=True)
    assert run.outcome.polish[run.entry.id].accepted
    run.guard.require_current(run.request)


@pytest.mark.parametrize("check", ["apply", "polish", "retry"])
def test_external_reference_edit_preserves_successes_and_allows_polish_retry_and_apply(term_write_run, check):
    run = term_write_run("auto_name")
    external = DynamicTermDatabase(run.task.esp_path)
    external.load()
    external.add_many_and_save([("Whiterun", "外部修改", "manual", "NPC_:FULL")])
    before = run.task.collection.get(run.entry.identity)
    original_text, original_revision = before.translation, before.revision
    if check == "apply":
        run.guard.require_current(run.request)
    else:
        run.provider.retry_pending = True
        run.executor.attempt_id = "retry-after-reference-edit"
        task = (
            replace(run.task, translate_entries=(), polish_entries=(run.entry,))
            if check == "polish"
            else replace(run.task, translate_entries=(run.pending,))
        )
        result = run.executor.execute(task)
        assert result.successful, result.error
        run.guard.require_current(run.request)
    external.load()
    assert external.as_list()[0].translation == "外部修改"
    if check != "polish":
        current = run.task.collection.get(run.entry.identity)
        assert (current.translation, current.revision) == (original_text, original_revision)
