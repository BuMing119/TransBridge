"""Project-wide name preparation with the real translator, draft commits and pool."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
import json
from pathlib import Path
import re
import shutil
import threading
from types import SimpleNamespace

import pytest

from tests.ui.tools.ai_runtime_support import frozen_ai_request
from transbridge.ai_translator.project_terminology_runtime import ProjectTerminologyBinding
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.translation.ai_execution_profile import AiExecutionProfile
from transbridge.application.translation.ai_request_budget import AiRequestBudget
from transbridge.application.translation.task_scope import SourceTask
from transbridge.config.llm import LLMConfig
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.tools.ai_translator import source_execution, workflow_log_store
from transbridge.ui.tools.ai_translator.source_execution import SourceExecutor
from transbridge.ui.tools.ai_translator.task_consistency import TaskConsistency


class _Provider:
    def __init__(self):
        self.calls = []
        self.fail = set()
        self.translations = {"Whiterun": "雪漫城", "Iron Sword": "铁剑", "Dawn": "黎明"}

    def chat(self, messages, max_tokens=0):
        content = messages[-1]["content"]
        payload = re.search(r"<translation_entries>\s*(.*?)\s*</translation_entries>", content, re.DOTALL)
        assert payload is not None, "unexpected non-translation model request"
        entries = json.loads(payload[1])
        self.calls.append((tuple(item["source"] for item in entries.values()), content))
        results = []
        for key, item in entries.items():
            original = item["source"]
            if original in self.fail:
                continue
            if original == "Travel to Whiterun with an Iron Sword.":
                assert "雪漫城" in content and "铁剑" in content
                text = "带着铁剑前往雪漫城。"
            else:
                text = self.translations.get(original, "继续旅程。")
            results.append({"entry_id": key, "translation": text})
        return json.dumps({"results": results}, ensure_ascii=False)

    def chat_stream(self, messages, max_tokens, chunk_callback):
        response = self.chat(messages, max_tokens)
        chunk_callback(response)
        return response

    def cancel(self):
        pass

    def counts(self):
        return Counter(text for entries, _ in self.calls for text in entries)


@pytest.fixture
def name_run(tmp_path, monkeypatch, request):
    from transbridge.config.paths import get_data_resource_dir

    shutil.copytree(get_data_resource_dir("prompts"), tmp_path / "data" / "prompts")
    monkeypatch.setattr("transbridge.config.paths.get_data_dir", lambda: str(tmp_path / "data"))

    def directory(stem):
        path = tmp_path / "translator" / stem
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    monkeypatch.setattr(LLMConfig, "get_ai_translator_dir", staticmethod(directory))
    provider = _Provider()
    monkeypatch.setattr("transbridge.infra.llm_client.create_llm_client", lambda _: provider)
    monkeypatch.setattr("transbridge.infra.llm_reasoning.with_reasoning_intent", lambda client, *_: client)
    store_class = workflow_log_store.WorkflowLogStore

    def store(path, **kwargs):
        return store_class(path, log_base=tmp_path / "logs", **kwargs)

    monkeypatch.setattr(source_execution, "WorkflowLogStore", store)
    monkeypatch.setattr(workflow_log_store, "WorkflowLogStore", store)
    config = LLMConfig(
        model="fake",
        max_concurrent=2,
        term_priority=["dynamic"],
        enable_semantic_match=False,
        enable_post_process=bool(getattr(request, "param", False)),
        pp_strategy="strict",
        pp_enable_consistency_check=False,
        pp_enable_quality_gate=False,
        pp_enable_refinement=False,
        pp_enable_arbitration=False,
    )
    request = SimpleNamespace(
        run_id="name-preparation",
        config=config,
        terminology_binding=ProjectTerminologyBinding(),
        request_budget=AiRequestBudget(2),
        spec=SimpleNamespace(
            overwrite=False,
            owner=SimpleNamespace(project_id="names-project", variant_id="variant"),
            execution_profile=AiExecutionProfile.from_config("translate", config),
        ),
    )
    pause = threading.Event()
    pause.set()
    events = []
    request = frozen_ai_request(request)
    guard = TaskConsistency(request)
    executor = SourceExecutor(
        request,
        stop_event=threading.Event(),
        pause_event=pause,
        shared_terms={},
        terms_lock=threading.Lock(),
        progress=lambda *args: events.append(args),
        log=lambda *_: None,
        consistency=guard,
    )

    def task(source, rows):
        entries = tuple(
            TranslationEntry(key, key, original, "", 0, context, entry_key=EntryKey(SourceNamespace(source), key))
            for key, original, context in rows
        )
        return SourceTask(
            source,
            source,
            str(tmp_path / "sources" / f"{source}.esp"),
            TranslationEntryCollection(entries),
            entries,
            (),
        )

    return SimpleNamespace(
        executor=executor, provider=provider, task=task, guard=guard, request=request, events=events, root=tmp_path
    )


@pytest.mark.parametrize("name_run", [False, True], indirect=True)
def test_names_are_translated_once_before_body_and_reused_with_per_source_commit(name_run):
    run = name_run
    rows = [("place", "Whiterun", "LCTN:FULL"), ("item", "Iron Sword", "WEAP:FULL")]
    first = run.task("A", rows + [("body", "Travel to Whiterun with an Iron Sword.", "BOOK:DESC")])
    second = run.task("B", rows)
    before = [entry.snapshot() for task in (first, second) for entry in task.collection]

    run.executor.prepare_names((first, second))

    assert run.provider.counts() == {"Whiterun": 1, "Iron Sword": 1}
    assert [entry.snapshot() for task in (first, second) for entry in task.collection] == before
    assert {term.term: term.translation for term in run.executor.shared_term_pool.snapshot().entries} == {
        "Whiterun": "雪漫城",
        "Iron Sword": "铁剑",
    }
    assert run.events and all(event[0] in {"A", "B"} and event[1] == "project_names" for event in run.events)
    outcomes = tuple(run.executor.execute(task) for task in (first, second))
    outcomes = run.executor.close_terms(outcomes)
    assert all(outcome.successful for outcome in outcomes), [outcome.error for outcome in outcomes]
    assert sum(outcome.translation.new_dynamic_terms for outcome in outcomes) == 2
    assert run.provider.counts() == {"Whiterun": 1, "Iron Sword": 1, "Travel to Whiterun with an Iron Sword.": 1}
    for outcome in outcomes:
        for entry in outcome.task.entries:
            current = outcome.task.collection.get(entry.identity)
            evidence = outcome.translation.entry_outcomes[entry.identity]
            assert evidence.status == "succeeded"
            assert evidence.text == current.translation
            assert current.revision.value > entry.revision.value
    run.guard.require_current(run.request)
    assert run.request.request_budget.snapshot().in_flight == 0
    if run.request.config.enable_post_process:
        assert any(event[1] == "format" for event in run.events)


def test_failed_unique_name_propagates_without_second_model_attempt(name_run):
    run = name_run
    run.provider.fail.add("Whiterun")
    first = run.task("A", [("place", "Whiterun", "LCTN:FULL"), ("body", "Continue.", "BOOK:DESC")])
    second = run.task("B", [("place", "Whiterun", "LCTN:FULL")])
    run.executor.prepare_names((first, second))
    initial_attempts = run.provider.counts()["Whiterun"]
    outcomes = [run.executor.execute(task) for task in (first, second)]
    assert run.provider.counts()["Whiterun"] == initial_attempts
    assert initial_attempts > 0
    for outcome in outcomes:
        entry = outcome.task.entries[0]
        assert outcome.translation.entry_outcomes[entry.identity].status == "failed"
        assert outcome.failed_keys == ("place",)
        assert outcome.task.collection.get(entry.identity).translation == ""
    assert outcomes[0].translation.entry_outcomes[first.entries[1].identity].status == "succeeded"


def test_same_spelling_in_different_categories_is_not_a_global_name(name_run):
    run = name_run
    first = run.task("A", [("name", "Dawn", "LCTN:FULL")])
    second = run.task("B", [("name", "Dawn", "WEAP:FULL")])
    run.executor.prepare_names((first, second))
    assert run.provider.counts()["Dawn"] == 2
    assert run.executor.shared_term_pool.snapshot().entries == ()
    outcomes = [run.executor.execute(task) for task in (first, second)]
    assert all(outcome.successful for outcome in outcomes)
    assert run.provider.counts()["Dawn"] == 2
    assert run.executor.shared_term_pool.snapshot().entries == ()


def test_plugin_scoped_existing_name_survives_project_first_batch(name_run):
    run = name_run
    run.executor.shared_term_pool.publish([("Whiterun", "插件雪漫", "test", "")], plugin_id="B.esp")
    first = run.task("A", [("place", "Whiterun", "LCTN:FULL")])
    second = run.task("B", [("place", "Whiterun", "LCTN:FULL")])
    run.executor.prepare_names((first, second))
    outcomes = [run.executor.execute(task) for task in (first, second)]
    assert all(outcome.successful for outcome in outcomes)
    assert run.provider.counts()["Whiterun"] == 1
    assert first.collection.get(first.entries[0].identity).translation == "雪漫城"
    assert second.collection.get(second.entries[0].identity).translation == "插件雪漫"
    run.guard.require_current(run.request)


def test_first_batch_only_contains_selected_names_not_descriptions(name_run):
    run = name_run
    task = run.task("A", [("item", "Iron Sword", "WEAP:FULL"), ("desc", "Continue.", "WEAP:DESC")])
    excluded = run.task("B", [("place", "Whiterun", "LCTN:FULL")])
    excluded = replace(excluded, translate_entries=())
    run.executor.prepare_names((task, excluded))
    assert run.provider.counts() == {"Iron Sword": 1}
    assert excluded.key not in run.executor.prepared_names or not run.executor.prepared_names[excluded.key]


def test_pool_write_failure_keeps_every_original_draft_untouched(name_run, monkeypatch):
    run = name_run
    task = run.task("A", [("item", "Iron Sword", "WEAP:FULL")])

    def fail(*args, **kwargs):
        raise OSError("name pool disk unavailable")

    monkeypatch.setattr(run.executor.shared_term_pool, "publish", fail)
    with pytest.raises(OSError, match="name pool disk unavailable"):
        run.executor.prepare_names((task,))
    assert task.collection.get(task.entries[0].identity).translation == ""
    assert run.executor.shared_term_pool.snapshot().entries == ()
    assert Path(run.executor.shared_term_pool._path).is_relative_to(run.root)


def test_existing_local_name_is_reused_before_new_translation(name_run):
    run = name_run
    task = run.task("A", [("old", "Whiterun", "LCTN:FULL"), ("new", "Whiterun", "LCTN:FULL")])
    existing = replace(task.entries[0], translation="原有雪漫", stage=3)
    selected = task.entries[1]
    task = replace(task, collection=TranslationEntryCollection((existing, selected)), translate_entries=(selected,))
    run.executor.prepare_names((task,))
    assert run.provider.calls == []
    assert run.executor.prepared_names[task.key][selected.identity].text == "原有雪漫"
    filled = task.collection.get(selected.identity).snapshot()
    assert filled.translation == "原有雪漫"
    assert filled.stage == 2 and filled.revision == selected.revision.next()
    outcome = run.executor.execute(task)
    assert outcome.successful, outcome.error
    assert task.collection.get(selected.identity).translation == "原有雪漫"
    assert task.collection.get(selected.identity).snapshot() == filled
    assert task.collection.get(existing.identity).snapshot() == existing.snapshot()
    assert outcome.translation.entry_outcomes[selected.identity].status == "succeeded"
    assert run.provider.calls == []


def test_published_plugin_override_remains_distinct_without_model_calls(name_run):
    from transbridge.ai_translator.project_terminology_adapter import (
        EnabledEffectiveTerminologyGate,
        ProjectTerminologyAdapter,
    )
    from transbridge.application.terminology.effective import (
        EffectiveSnapshotStatus,
        EffectiveTerminologySnapshot,
        SnapshotEffectiveTerminologyPort,
        TerminologyLookupContext,
    )
    from transbridge.application.terminology.identity import normalize_original, term_id
    from transbridge.application.terminology.models import DecisionStatus, TermDecision, TermScope

    run = name_run
    decisions = []
    for scope, text in ((TermScope.project(), "权威雪漫"), (TermScope.plugin("B.esp"), "插件雪漫")):
        decisions.append(
            TermDecision(
                term_id(project_id="names-project", variant_id="variant", scope=scope, original="Whiterun"),
                "names-project",
                "variant",
                "Whiterun",
                normalize_original("Whiterun"),
                text,
                scope=scope,
                status=DecisionStatus.ADOPTED,
            )
        )
    snapshot = EffectiveTerminologySnapshot(
        "names-project", "variant", EffectiveSnapshotStatus.READY, "v1", "digest", tuple(decisions)
    )
    adapter = ProjectTerminologyAdapter(
        SnapshotEffectiveTerminologyPort(SimpleNamespace(snapshot=lambda *_: snapshot)),
        EnabledEffectiveTerminologyGate(),
    )
    run.request = replace(
        run.request,
        terminology_binding=ProjectTerminologyBinding(
            adapter=adapter, context=TerminologyLookupContext("names-project", "variant")
        ),
    )
    run.executor.request = run.request
    run.request = frozen_ai_request(run.request)
    run.executor.consistency = TaskConsistency(run.request)
    first = run.task("A", [("place", "Whiterun", "LCTN:FULL")])
    second = run.task("B", [("place", "Whiterun", "LCTN:FULL")])
    run.executor.prepare_names((first, second))
    outcomes = [run.executor.execute(task) for task in (first, second)]
    assert all(outcome.successful for outcome in outcomes), [outcome.error for outcome in outcomes]
    assert run.provider.calls == []
    assert first.collection.get(first.entries[0].identity).translation == "权威雪漫"
    assert second.collection.get(second.entries[0].identity).translation == "插件雪漫"
    assert run.executor.shared_term_pool.snapshot().entries == ()
    run.executor.consistency.require_current(run.request)


def test_unique_name_reference_difference_does_not_reject_translation(name_run):
    run = name_run
    pool = run.executor.shared_term_pool
    pool.publish([("Sword", "剑", "test", "")])
    run.provider.translations["Iron Sword"] = "铁刀"
    task = run.task("A", [("item", "Iron Sword", "WEAP:FULL")])
    entry = task.entries[0]
    before = entry.snapshot()

    run.executor.prepare_names((task,))

    prepared = run.executor.prepared_names[task.key][entry.identity]
    assert run.provider.counts() == {"Iron Sword": 1}
    assert prepared.text == "铁刀"
    assert prepared.reason == ""
    assert {term.term: term.translation for term in pool.snapshot().entries} == {"Sword": "剑", "Iron Sword": "铁刀"}
    assert task.collection.get(entry.identity).snapshot() == before

    # Reference wording alone must neither reject nor repeatedly translate a name.
    outcome = run.executor.execute(task)
    (outcome,) = run.executor.close_terms((outcome,))
    assert run.provider.counts() == {"Iron Sword": 1}
    assert outcome.translation.entry_outcomes[entry.identity].status == "succeeded"
    assert outcome.failed_keys == ()
    assert task.collection.get(entry.identity).translation == "铁刀"
    assert {term.term: term.translation for term in pool.snapshot().entries} == {"Sword": "剑", "Iron Sword": "铁刀"}
    run.guard.require_current(run.request)


@pytest.mark.parametrize(
    ("term", "reference", "original", "translated"),
    [
        ("Guards", "瓜尔", "Bruma guards fighting predators", "布鲁玛卫兵对抗掠食者"),
        ("Rielle", "1.瑞尔勒 2.美丽；美人", "Handler for first Rielle playthrough events", "瑞尔勒首次通关事件处理器"),
        ("Marc Dufonte", "马克·杜方特", "Marc Dufonte gathering ingredients", "马克·杜丰特采集材料"),
    ],
)
def test_log_examples_remain_successful_name_candidates(name_run, term, reference, original, translated):
    run = name_run
    run.executor.shared_term_pool.publish([(term, reference, "auto_dialogue", "")])
    run.provider.translations[original] = translated
    task = run.task("A", [("quest", original, "QUST:FULL")])
    run.executor.prepare_names((task,))
    prepared = run.executor.prepared_names[task.key][task.entries[0].identity]
    assert prepared.text == translated
    assert prepared.reason == ""
    outcome = run.executor.execute(task)
    assert outcome.successful
    assert outcome.translation.failed_count == 0
    assert task.collection.get(task.entries[0].identity).translation == translated
    assert run.provider.counts()[original] == 1


def test_duplicate_names_with_plugin_specific_subterms_are_translated_separately(name_run, monkeypatch):
    run = name_run
    pool = run.executor.shared_term_pool
    pool.publish([("Sword", "剑", "test", "")])
    pool.publish([("Sword", "刀", "test", "")], plugin_id="B.esp")
    first = run.task("A", [("item", "Iron Sword", "WEAP:FULL")])
    second = run.task("B", [("item", "Iron Sword", "WEAP:FULL")])
    received_terms = []

    def chat(messages, max_tokens=0):
        content = messages[-1]["content"]
        payload = re.search(r"<translation_entries>\s*(.*?)\s*</translation_entries>", content, re.DOTALL)
        assert payload is not None
        entries = json.loads(payload[1])
        run.provider.calls.append((tuple(item["source"] for item in entries.values()), content))
        results = []
        for key, item in entries.items():
            assert item["source"] == "Iron Sword"
            required = item["terms"]["Sword"]
            received_terms.append(required)
            results.append({"entry_id": key, "translation": f"铁{required}"})
        return json.dumps({"results": results}, ensure_ascii=False)

    monkeypatch.setattr(run.provider, "chat", chat)

    run.executor.prepare_names((first, second))

    assert Counter(received_terms) == {"剑": 1, "刀": 1}
    assert run.provider.counts() == {"Iron Sword": 2}
    for task, expected in ((first, "铁剑"), (second, "铁刀")):
        entry = task.entries[0]
        prepared = run.executor.prepared_names[task.key][entry.identity]
        assert prepared.text == expected and not prepared.reason
        assert task.collection.get(entry.identity).translation == ""
    assert all(term.term != "Iron Sword" for term in pool.as_list())

    outcomes = tuple(run.executor.execute(task) for task in (first, second))
    outcomes = run.executor.close_terms(outcomes)
    assert all(outcome.successful for outcome in outcomes), [outcome.error for outcome in outcomes]
    assert first.collection.get(first.entries[0].identity).translation == "铁剑"
    assert second.collection.get(second.entries[0].identity).translation == "铁刀"
    assert run.provider.counts() == {"Iron Sword": 2}
    assert all(term.term != "Iron Sword" for term in pool.as_list())
    run.guard.require_current(run.request)


def test_external_reference_write_during_name_translation_preserves_names_and_external_file(name_run, monkeypatch):
    from transbridge.ai_translator.shared_term_pool import SharedTermPool

    run = name_run
    task = run.task("A", [("item", "Iron Sword", "WEAP:FULL")])
    pool = run.executor.shared_term_pool
    chat = run.provider.chat
    external_bytes = []

    def translate_then_update_reference(messages, max_tokens=0):
        response = chat(messages, max_tokens)
        external = SharedTermPool(*pool.identity, path=pool._path)
        external.publish([("Shield", "盾", "external_task", "")])
        external_bytes.append(Path(pool._path).read_bytes())
        return response

    monkeypatch.setattr(run.provider, "chat", translate_then_update_reference)
    run.executor.prepare_names((task,))

    entry = task.entries[0]
    prepared = run.executor.prepared_names[task.key][entry.identity]
    assert prepared.text == "铁剑"
    assert prepared.reason == ""
    assert len(external_bytes) == 1
    assert Path(pool._path).read_bytes() == external_bytes[0]
    assert pool.snapshot().entries == ()
    assert pool.conflicts_since(0)[0].reason == "external-write-conflict"

    outcome = run.executor.execute(task)
    assert outcome.successful
    assert outcome.translation.failed_count == 0
    assert task.collection.get(entry.identity).translation == "铁剑"
    assert run.provider.counts() == {"Iron Sword": 1}
    assert Path(pool._path).read_bytes() == external_bytes[0]
    run.guard.require_current(run.request)
