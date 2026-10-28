"""Exercise advisory shared-reference review through real source and consistency wiring."""

from __future__ import annotations

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
from transbridge.ui.tools.ai_translator.source_reporting import build_source_snapshot
from transbridge.ui.tools.ai_translator.task_consistency import TaskConsistency


class _Provider:
    def __init__(self):
        self.calls = []
        self.review_calls = []
        self.repair_failure = ""

    def chat(self, messages, max_tokens=0):
        content = messages[-1]["content"]
        payload = re.search(r"<translation_entries>\s*(.*?)\s*</translation_entries>", content, re.DOTALL)
        if payload is None:
            entries = json.loads(content)["entries"]
            self.review_calls.append(entries)
            if self.repair_failure == "exception":
                raise RuntimeError("review provider unavailable")
            return json.dumps(
                {
                    "results": [
                        {
                            "entry_key": item["entry_key"],
                            "final_translation": item["current_translation"],
                            "review_status": "retained",
                            "review_reason": "结合当前语境，现有译名合适，无需按参考词面替换。",
                        }
                        for item in entries
                    ]
                },
                ensure_ascii=False,
            )
        entries = json.loads(payload[1])
        current_terms = "雪漫城" in content
        self.calls.append((tuple(entries), tuple(item["source"] for item in entries.values()), current_terms))
        if current_terms and self.repair_failure == "exception":
            raise RuntimeError("repair provider unavailable")
        results = []
        for key, item in entries.items():
            if item["source"] == "Whiterun":
                translation = "雪漫城"
            elif item["source"] == "Travel to Whiterun.":
                translation = (
                    "前往雪漫城。" if current_terms and self.repair_failure != "noncompliant" else "前往白漫。"
                )
            else:
                assert item["source"] == "Continue the journey."
                translation = "继续旅程。"
            results.append({"entry_id": key, "translation": translation})
        return json.dumps({"results": results}, ensure_ascii=False)

    def chat_stream(self, messages, max_tokens, chunk_callback):
        response = self.chat(messages, max_tokens)
        chunk_callback(response)
        return response

    def cancel(self):
        pass


def _task(tmp_path, source, rows):
    entries = tuple(
        TranslationEntry(
            key,
            key,
            original,
            "",
            0,
            context,
            entry_key=EntryKey(SourceNamespace(source), key),
        )
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


@pytest.fixture
def closure_run(tmp_path, monkeypatch):
    # Keep the actual default project-pool composition, while containing every
    # persistent artifact in this test's directory.
    from transbridge.config.paths import get_data_resource_dir

    shutil.copytree(get_data_resource_dir("prompts"), tmp_path / "data" / "prompts")
    monkeypatch.setattr("transbridge.config.paths.get_data_dir", lambda: str(tmp_path / "data"))

    def translator_directory(stem):
        path = tmp_path / "translator" / stem
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    monkeypatch.setattr(LLMConfig, "get_ai_translator_dir", staticmethod(translator_directory))
    provider = _Provider()
    monkeypatch.setattr("transbridge.infra.llm_client.create_llm_client", lambda _: provider)
    monkeypatch.setattr(
        "transbridge.ui.tools.ai_translator.source_reference_review.create_llm_client", lambda _: provider
    )
    monkeypatch.setattr("transbridge.infra.llm_reasoning.with_reasoning_intent", lambda client, *_: client)
    store_class = workflow_log_store.WorkflowLogStore

    def make_store(path, **kwargs):
        return store_class(path, log_base=tmp_path / "logs", **kwargs)

    monkeypatch.setattr(source_execution, "WorkflowLogStore", make_store)
    monkeypatch.setattr(workflow_log_store, "WorkflowLogStore", make_store)
    monkeypatch.setattr("transbridge.ui.tools.ai_translator.source_reference_review.WorkflowLogStore", make_store)

    def make_executor(run_id="shared-term-closure", *, enable_post_process=False):
        config = LLMConfig(
            model="fake",
            max_concurrent=2,
            term_priority=["dynamic"],
            enable_semantic_match=False,
            enable_post_process=enable_post_process,
        )
        request = SimpleNamespace(
            run_id=run_id,
            config=config,
            terminology_binding=ProjectTerminologyBinding(),
            request_budget=AiRequestBudget(2),
            spec=SimpleNamespace(
                overwrite=False,
                owner=SimpleNamespace(project_id="local-project", variant_id="local-variant"),
                execution_profile=AiExecutionProfile.from_config("translate", config),
            ),
        )
        request = frozen_ai_request(request)
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
        assert executor.shared_term_pool is not None
        assert Path(executor.shared_term_pool._path).is_relative_to(tmp_path)
        return SimpleNamespace(executor=executor, request=request, guard=guard)

    first = _task(
        tmp_path,
        "book-source",
        [("book", "Travel to Whiterun.", "BOOK:DESC"), ("unrelated", "Continue the journey.", "BOOK:DESC")],
    )
    second = _task(tmp_path, "name-source", [("name", "Whiterun", "NPC_:FULL")])
    run = make_executor()
    first_outcome = run.executor.execute(first)
    assert first_outcome.successful, first_outcome.error
    assert first.collection.get(first.entries[0].identity).translation == "前往白漫。"
    assert run.executor.shared_term_pool.snapshot().entries == ()
    run.guard.require_current(run.request)

    second_outcome = run.executor.execute(second)
    assert second_outcome.successful, second_outcome.error
    assert second.collection.get(second.entries[0].identity).translation == "雪漫城"
    assert [(term.term, term.translation) for term in run.executor.shared_term_pool.snapshot().entries] == [
        ("Whiterun", "雪漫城")
    ]
    run.guard.require_current(run.request)
    return SimpleNamespace(
        **vars(run),
        first=first,
        second=second,
        outcomes=(first_outcome, second_outcome),
        provider=provider,
        make_executor=make_executor,
        root=tmp_path,
    )


def test_disabled_postprocessing_marks_late_reference_for_review_without_extra_model_calls(closure_run):
    run = closure_run
    before_calls = len(run.provider.calls)
    second_evidence = dict(run.outcomes[1].translation.entry_outcomes)
    unrelated = run.first.entries[1]
    unrelated_before = run.first.collection.get(unrelated.identity)
    unrelated_revision = unrelated_before.revision

    first, second = run.executor.close_terms(run.outcomes)

    assert first.successful and second.successful
    assert len(run.provider.calls) == before_calls
    assert not run.provider.review_calls
    entry = run.first.entries[0]
    assert run.first.collection.get(entry.identity).translation == "前往白漫。"
    assert first.translation.entry_outcomes[entry.identity].text == "前往白漫。"
    assert dict(first.translation.entry_outcomes[entry.identity].review_details)["review_status"] == "needs_review"
    assert first.translation.entry_outcomes[entry.identity].status == "succeeded"
    assert first.translation.success_count == 2
    assert first.translation.failed_count == 0
    assert second.translation.entry_outcomes == second_evidence
    assert run.second.collection.get(run.second.entries[0].identity).translation == "雪漫城"
    assert run.first.collection.get(unrelated.identity).translation == "继续旅程。"
    assert run.first.collection.get(unrelated.identity).revision == unrelated_revision
    run.guard.require_current(run.request)
    assert run.request.request_budget.snapshot().in_flight == 0


@pytest.mark.parametrize("failure", ["exception", "noncompliant"])
def test_enabled_review_can_retain_mismatch_or_leave_pending_without_failing_translation(closure_run, failure):
    run = closure_run
    run.provider.repair_failure = failure
    book = run.first.entries[0]
    before = run.first.collection.get(book.identity)
    before_text, before_stage = before.translation, before.stage

    reviewer = run.make_executor("review-enabled", enable_post_process=True)
    first, second = reviewer.executor.close_terms(run.outcomes)

    assert first.successful
    assert first.failed_keys == ()
    assert second.successful
    assert first.translation.entry_outcomes[book.identity].status == "succeeded"
    assert first.translation.success_count == 2
    assert first.translation.failed_count == 0
    current = run.first.collection.get(book.identity)
    assert (current.translation, current.stage) == (before_text, before_stage)
    snapshot = build_source_snapshot(first, run.request)
    candidate = next(candidate for candidate in snapshot.candidates if candidate.entry_key == book.identity)
    assert candidate.accepted
    assert dict(candidate.report_details)["review_status"] == ("needs_review" if failure == "exception" else "retained")
    assert all(len(batch) == 1 and batch[0]["original"] == "Travel to Whiterun." for batch in run.provider.review_calls)
    if failure == "noncompliant":
        assert len(run.provider.review_calls) == 1
        sent = run.provider.review_calls[0][0]
        assert sent["terms"] == {"Whiterun": "雪漫城"}
        assert any(issue["issue_type"] == "term_mismatch" for issue in sent["detected_issues"])
        again = reviewer.executor.close_terms((first, second))
        assert len(run.provider.review_calls) == 1
        assert again[0].translation.entry_outcomes[book.identity].text == before_text
    reviewer.guard.require_current(reviewer.request)
    assert reviewer.request.request_budget.snapshot().in_flight == 0


def test_new_executor_for_same_project_restores_shared_terms_before_translation(closure_run):
    run = closure_run
    next_run = run.make_executor("next-task")
    assert next_run.executor.shared_term_pool is not run.executor.shared_term_pool
    assert next_run.executor.shared_term_pool.identity == run.executor.shared_term_pool.identity
    assert next_run.executor.shared_term_pool._path == run.executor.shared_term_pool._path
    assert [(term.term, term.translation) for term in next_run.executor.shared_term_pool.snapshot().entries] == [
        ("Whiterun", "雪漫城")
    ]
    task = _task(run.root, "new-source", [("new-book", "Travel to Whiterun.", "BOOK:DESC")])
    before_calls = len(run.provider.calls)

    outcome = next_run.executor.execute(task)
    (closed,) = next_run.executor.close_terms((outcome,))

    assert closed.successful, closed.error
    assert task.collection.get(task.entries[0].identity).translation == "前往雪漫城。"
    assert closed.translation.entry_outcomes[task.entries[0].identity].text == "前往雪漫城。"
    assert len(run.provider.calls) == before_calls + 1
    assert run.provider.calls[-1] == (("new-book",), ("Travel to Whiterun.",), True)
    next_run.guard.require_current(next_run.request)
