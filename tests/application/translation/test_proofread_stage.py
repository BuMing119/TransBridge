from __future__ import annotations

import json
import threading
import time

from transbridge.ai_translator.structured_schemas import PROOFREAD_OUTPUT_SCHEMA
from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io import EntryKey, EntryRevision, SourceNamespace, StagePolicy
from transbridge.application.io.publish import ImmediateCommitGuard
from transbridge.application.translation import (
    InMemoryTranslationCheckpointPort,
    PostProcessExecutionService,
    PostProcessWorkload,
    ProofreadStage,
    TranslationInput,
)
from transbridge.application.translation.ai_request_budget import AiRequestCancelledError
from transbridge.application.translation.postprocess import PostProcessCandidate
from transbridge.application.translation.protected_syntax import (
    extract_protected_syntax,
    protected_syntax_matches,
)
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.infra.llm_structured_outputs import extract_structured_output_directive


def _candidate(
    local_key: str,
    *,
    original: str = "Source",
    text: str = "Current",
    context: str = "Context",
) -> PostProcessCandidate:
    return PostProcessCandidate(
        run_id="run",
        entry_key=EntryKey(SourceNamespace.legacy(), local_key),
        before_revision=EntryRevision(),
        original=original,
        before_text=text,
        text=text,
        stage=2,
        context=context,
    )


def _result(candidate: PostProcessCandidate, value: str) -> dict[str, object]:
    return {"entry_key": candidate.entry_key.to_dict(), "final_translation": value}


def test_former_class_and_module_name_remain_importable_as_an_alias() -> None:
    from transbridge.application.translation.combined_proofread import CombinedProofreadStage

    assert CombinedProofreadStage is ProofreadStage


class _PreparedClient:
    def __init__(self, response_factory) -> None:
        self.response_factory = response_factory
        self.events: list[str] = []
        self.messages: list[list[dict[str, str]]] = []
        self.max_tokens: list[int] = []
        self.cancelled = False

    def chat_prepared(self, messages_factory, max_tokens: int = 0) -> str:
        self.events.append("admitted")
        messages = messages_factory()
        self.events.append("prepared")
        self.messages.append(messages)
        self.max_tokens.append(max_tokens)
        return self.response_factory(messages)

    def cancel(self) -> None:
        self.cancelled = True


def test_protected_syntax_is_a_multiset_and_does_not_validate_natural_language() -> None:
    source = r'Count 10 (old) "quote" %s %s {name} <Alias=Hero> [pagebreak] \n'
    translated = r"完全不同的 999 [内容] %s {name} <Alias=Hero> [pagebreak] \n %s"

    assert protected_syntax_matches(source, translated)
    assert not protected_syntax_matches(source, translated.replace(" %s", "", 1))
    assert extract_protected_syntax(source).count("%s") == 2


def test_terms_are_resolved_after_admission_and_one_pass_accepts_unchanged_values() -> None:
    candidate = _candidate("one", original="Dragon", text="龙")
    terms = {"Dragon": "巨龙"}

    def respond(messages):
        request = json.loads(messages[1]["content"])
        entry = request["entries"][0]
        assert entry["terms"] == {"Dragon": "巨龙"}
        assert "detected_issues" not in entry
        return json.dumps({"results": [_result(candidate, "巨龙")]}, ensure_ascii=False)

    client = _PreparedClient(respond)

    def resolve(_candidate):
        assert client.events == ["admitted"]
        return terms

    outcome = ProofreadStage(
        client,
        term_resolver=resolve,
        model="unknown-model",
        polish_level="light",
        max_tokens_per_batch=10_000,
        max_output_tokens=0,
    )((candidate,))

    assert client.events == ["admitted", "prepared"]
    assert client.max_tokens == [0]
    assert outcome.diagnostics == ()
    assert outcome.candidates[0].text == "巨龙"
    assert outcome.candidates[0].phases == ("proofread",)
    assert "confidence" not in client.messages[0][1]["content"]
    assert "needs_arbitration" not in client.messages[0][1]["content"]
    assert "only necessary corrections" in client.messages[0][0]["content"]
    system_prompt = client.messages[0][0]["content"]
    assert "semantic errors" in system_prompt
    assert "omissions" in system_prompt
    assert "negation relationships" in system_prompt
    assert "mandatory constraint" in system_prompt
    assert "not a complete list of possible problems" in system_prompt
    _clean_messages, output_schema = extract_structured_output_directive(client.messages[0])
    assert output_schema == PROOFREAD_OUTPUT_SCHEMA

    stage = ProofreadStage(client, max_tokens_per_batch=10_000)
    stage.cancel()
    assert client.cancelled is True


def test_response_mapping_rejects_only_duplicate_missing_empty_and_unknown_results() -> None:
    duplicate = _candidate("duplicate")
    empty = _candidate("empty")
    missing = _candidate("missing")
    valid = _candidate("valid")
    unknown = _candidate("unknown")
    response = {
        "results": [
            _result(duplicate, "first"),
            _result(duplicate, "second"),
            _result(empty, "   "),
            _result(valid, "updated"),
            _result(unknown, "not requested"),
        ]
    }
    client = _PreparedClient(lambda _messages: json.dumps(response))

    outcome = ProofreadStage(client, max_tokens_per_batch=10_000)((duplicate, empty, missing, valid))

    assert [candidate.text for candidate in outcome.candidates] == ["Current", "Current", "Current", "updated"]
    assert [candidate.phases for candidate in outcome.candidates] == [(), (), (), ("proofread",)]
    assert [candidate.accepted for candidate in outcome.candidates] == [False, False, False, True]
    assert {diagnostic.code for diagnostic in outcome.diagnostics} == {
        "PROOFREAD_RESPONSE_DUPLICATE_KEY",
        "PROOFREAD_RESPONSE_EMPTY_TRANSLATION",
        "PROOFREAD_RESPONSE_MISSING_KEY",
        "PROOFREAD_RESPONSE_UNKNOWN_KEY",
    }


def test_only_placeholder_or_program_tag_damage_rejects_a_translation() -> None:
    protected = _candidate("protected", original="Hello %s <Alias=Hero>", text="你好 %s <Alias=Hero>")
    natural = _candidate("natural", original='Count 10 (old) "quoted"', text="计数 10（旧）")
    response = {
        "results": [
            _result(protected, "你好 <Alias=Hero>"),
            _result(natural, "这段译文可以任意改变数字 999、引号和长度"),
        ]
    }
    client = _PreparedClient(lambda _messages: json.dumps(response, ensure_ascii=False))

    outcome = ProofreadStage(client, max_tokens_per_batch=10_000)((protected, natural))

    assert outcome.candidates[0].text == protected.text
    assert outcome.candidates[0].phases == ()
    assert outcome.candidates[0].accepted is False
    assert outcome.candidates[1].accepted is True
    assert outcome.candidates[1].text == "这段译文可以任意改变数字 999、引号和长度"
    assert [diagnostic.code for diagnostic in outcome.diagnostics] == ["PROOFREAD_PROTECTED_SYNTAX_MISMATCH"]


def test_malformed_response_and_call_failures_retain_the_original_candidates() -> None:
    candidate = _candidate("one")
    malformed = _PreparedClient(lambda _messages: "```json\n{}\n```")

    malformed_outcome = ProofreadStage(malformed, max_tokens_per_batch=10_000)((candidate,))

    assert malformed_outcome.candidates[0].text == candidate.text
    assert malformed_outcome.candidates[0].accepted is False
    assert malformed_outcome.failed is False
    assert [diagnostic.code for diagnostic in malformed_outcome.diagnostics] == ["PROOFREAD_RESPONSE_MALFORMED"]

    class FailingClient:
        @staticmethod
        def chat_prepared(messages_factory, max_tokens=0):
            messages_factory()
            raise TimeoutError("provider details stay in the trusted LLM log")

    failed_outcome = ProofreadStage(FailingClient(), max_tokens_per_batch=10_000)((candidate,))

    assert failed_outcome.candidates[0].text == candidate.text
    assert failed_outcome.candidates[0].accepted is False
    assert failed_outcome.failed is True
    assert failed_outcome.diagnostics[0].code == "PROOFREAD_LLM_CALL_FAILED"
    assert dict(failed_outcome.diagnostics[0].details) == {"error_type": "TimeoutError"}


def test_wrapped_json_is_recovered_locally_without_an_extra_model_call() -> None:
    candidate = _candidate("wrapped")
    payload = json.dumps({"results": [_result(candidate, "updated")]})
    responses = iter((f"<think>private reasoning</think>\n```json\n{payload}\n```",))
    client = _PreparedClient(lambda _messages: next(responses))

    outcome = ProofreadStage(client, max_tokens_per_batch=10_000)((candidate,))

    assert len(client.messages) == 1
    assert outcome.candidates[0].text == "updated"
    assert outcome.diagnostics == ()


def test_failed_entries_are_retried_as_a_subset_and_only_final_failures_remain() -> None:
    first = _candidate("first")
    recovered = _candidate("recovered")
    calls: list[list[str]] = []

    def respond(messages):
        entries = json.loads(messages[1]["content"])["entries"]
        keys = [entry["entry_key"]["local_key"] for entry in entries]
        calls.append(keys)
        if len(calls) == 1:
            return json.dumps({"results": [_result(first, "first-updated")]})
        return json.dumps({"results": [_result(recovered, "recovered-updated")]})

    outcome = ProofreadStage(_PreparedClient(respond), max_tokens_per_batch=10_000)((first, recovered))

    assert calls == [["first", "recovered"], ["recovered"]]
    assert [candidate.text for candidate in outcome.candidates] == ["first-updated", "recovered-updated"]
    assert all(candidate.accepted for candidate in outcome.candidates)
    assert [diagnostic.code for diagnostic in outcome.diagnostics] == ["PROOFREAD_RECOVERY_SUCCEEDED"]
    assert dict(outcome.diagnostics[0].details) == {"recovered_count": 1, "final_failed_count": 0}


def test_cancelled_model_call_is_not_retried() -> None:
    candidate = _candidate("cancelled")

    class CancelledClient:
        calls = 0

        def chat_prepared(self, messages_factory, max_tokens=0):
            self.calls += 1
            messages_factory()
            raise AiRequestCancelledError("cancelled")

    client = CancelledClient()
    outcome = ProofreadStage(client, max_tokens_per_batch=10_000)((candidate,))

    assert client.calls == 1
    assert outcome.candidates[0].accepted is False
    assert [diagnostic.code for diagnostic in outcome.diagnostics] == ["PROOFREAD_LLM_CALL_CANCELLED"]


def test_malformed_recovery_splits_once_and_counts_only_persistent_failures() -> None:
    candidates = tuple(_candidate(str(index)) for index in range(4))
    calls: list[list[str]] = []

    def respond(messages):
        entries = json.loads(messages[1]["content"])["entries"]
        keys = [entry["entry_key"]["local_key"] for entry in entries]
        calls.append(keys)
        if len(calls) <= 2 or keys == ["2", "3"]:
            return "{}"
        return json.dumps({
            "results": [
                {"entry_key": entry["entry_key"], "final_translation": f"updated-{entry['entry_key']['local_key']}"}
                for entry in entries
            ]
        })

    outcome = ProofreadStage(_PreparedClient(respond), max_tokens_per_batch=10_000)(candidates)

    assert calls == [["0", "1", "2", "3"], ["0", "1", "2", "3"], ["0", "1"], ["2", "3"]]
    assert [candidate.accepted for candidate in outcome.candidates] == [True, True, False, False]
    assert sum(1 for candidate in outcome.candidates if not candidate.accepted) == 2
    assert {diagnostic.code for diagnostic in outcome.diagnostics} == {
        "PROOFREAD_RECOVERY_SUCCEEDED",
        "PROOFREAD_RESPONSE_MALFORMED",
    }


def test_malformed_batch_does_not_fail_the_stage_or_discard_other_batch_results() -> None:
    malformed = _candidate("malformed")
    valid = _candidate("valid")

    def respond(messages):
        entry = json.loads(messages[1]["content"])["entries"][0]
        if entry["entry_key"]["local_key"] == "malformed":
            return "{}"
        return json.dumps({"results": [_result(valid, "updated")]})

    outcome = ProofreadStage(
        _PreparedClient(respond),
        max_tokens_per_batch=10_000,
        max_items=1,
    )((malformed, valid))

    assert outcome.failed is False
    assert [candidate.accepted for candidate in outcome.candidates] == [False, True]
    assert [candidate.text for candidate in outcome.candidates] == ["Current", "updated"]
    assert [diagnostic.code for diagnostic in outcome.diagnostics] == ["PROOFREAD_RESPONSE_MALFORMED"]


def test_token_and_item_boundaries_keep_oversized_candidates_without_calling_them() -> None:
    small = _candidate("small", original="a", text="b", context="")
    other = _candidate("other", original="c", text="d", context="")
    oversized = _candidate("oversized", original="x" * 100, text="y", context="")

    def respond(messages):
        entries = json.loads(messages[1]["content"])["entries"]
        return json.dumps({
            "results": [{"entry_key": entry["entry_key"], "final_translation": "updated"} for entry in entries]
        })

    client = _PreparedClient(respond)
    outcome = ProofreadStage(
        client,
        model="unknown-model",
        max_tokens_per_batch=20,
        max_items=1,
    )((small, other, oversized))

    assert len(client.messages) == 2
    assert [candidate.text for candidate in outcome.candidates] == ["updated", "updated", "y"]
    assert [candidate.accepted for candidate in outcome.candidates] == [True, True, False]
    assert [diagnostic.code for diagnostic in outcome.diagnostics] == ["PROOFREAD_CONTENT_TOKEN_LIMIT"]


class _ConcurrentClient:
    def __init__(self, synchronized_calls: int) -> None:
        self._lock = threading.Lock()
        self._barrier = threading.Barrier(synchronized_calls)
        self._synchronized_calls = synchronized_calls
        self._started = 0
        self.active = 0
        self.peak = 0

    def chat_prepared(self, messages_factory, max_tokens: int = 0) -> str:
        messages = messages_factory()
        with self._lock:
            self._started += 1
            synchronize = self._started <= self._synchronized_calls
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            if synchronize:
                self._barrier.wait(timeout=2)
            request = json.loads(messages[1]["content"])
            local_key = request["entries"][0]["entry_key"]["local_key"]
            time.sleep((5 - int(local_key)) * 0.005)
            return json.dumps({
                "results": [
                    {
                        "entry_key": entry["entry_key"],
                        "final_translation": f"updated-{entry['entry_key']['local_key']}",
                    }
                    for entry in request["entries"]
                ]
            })
        finally:
            with self._lock:
                self.active -= 1


def test_batches_run_concurrently_with_constructor_default_and_explicit_worker_limit() -> None:
    candidates = tuple(_candidate(str(index)) for index in range(5))
    client = _ConcurrentClient(3)
    stage = ProofreadStage(
        client,
        max_tokens_per_batch=10_000,
        max_items=1,
        max_workers=3,
    )

    outcome = stage(candidates)

    assert client.peak == 3
    assert [candidate.text for candidate in outcome.candidates] == [f"updated-{index}" for index in range(5)]

    limited_client = _ConcurrentClient(2)
    progress: list[tuple[int, int, str]] = []
    limited_stage = ProofreadStage(
        limited_client,
        max_tokens_per_batch=10_000,
        max_items=1,
        max_workers=5,
    )

    limited_outcome = limited_stage.run(
        candidates, max_workers=2, progress_callback=lambda *args: progress.append(args)
    )

    assert limited_client.peak == 2
    assert limited_outcome.diagnostics == ()
    assert [item[:2] for item in progress] == [(1, 5), (2, 5), (3, 5), (4, 5), (5, 5)]
    assert progress[-1][2] == "校对已完成 5/5 条"


class _AuditedCollection(TranslationEntryCollection):
    def __init__(self, entries) -> None:
        super().__init__(entries)
        self.apply_count = 0

    def apply(self, change_set, context):
        self.apply_count += 1
        return super().apply(change_set, context)


def _workload_input(local_key: str, original: str) -> TranslationInput:
    return TranslationInput(
        EntryKey(SourceNamespace("proofread-fixture"), local_key),
        EntryRevision(),
        original,
        f"current-{local_key}" + (" %s" if "%s" in original else ""),
        1,
        "",
    )


def test_workload_rejects_only_invalid_candidates_and_execution_commits_the_valid_result() -> None:
    valid = _workload_input("valid", "Valid source")
    missing = _workload_input("missing", "Missing source")
    empty = _workload_input("empty", "Empty source")
    protected = _workload_input("protected", "Protected %s")
    entries = (valid, missing, empty, protected)

    def respond(messages):
        request_entries = json.loads(messages[1]["content"])["entries"]
        by_key = {item["entry_key"]["local_key"]: item["entry_key"] for item in request_entries}
        results = []
        if "valid" in by_key:
            results.append({"entry_key": by_key["valid"], "final_translation": "valid-updated"})
        if "empty" in by_key:
            results.append({"entry_key": by_key["empty"], "final_translation": ""})
        if "protected" in by_key:
            results.append({"entry_key": by_key["protected"], "final_translation": "protected-but-broken"})
        return json.dumps({"results": results})

    collection = _AuditedCollection(
        tuple(
            TranslationEntry(
                id=entry.entry_key.local_key,
                key=entry.entry_key.local_key,
                original=entry.original,
                translation=entry.translation,
                stage=entry.stage,
                context=entry.context,
                entry_key=entry.entry_key,
                revision=entry.revision,
            )
            for entry in entries
        )
    )
    stage = ProofreadStage(_PreparedClient(respond), max_tokens_per_batch=10_000)
    execution = PostProcessExecutionService(
        PostProcessWorkload((stage,), stage_policy=StagePolicy(), stage_names=("proofread",))
    ).execute(
        run_id="proofread-run",
        entries=entries,
        collection=collection,
        context=RequestContext(
            "owner",
            run_id="proofread-run",
            permissions=frozenset({"entry.translation.write", "entry.stage.write"}),
        ),
        commit_guard=ImmediateCommitGuard("proofread-run"),
        commit_checkpoint=InMemoryTranslationCheckpointPort(),
    )

    assert execution.report_result.outcome is OperationOutcome.COMPLETED
    assert execution.report_result.counts.succeeded == 1
    assert execution.report_snapshot is not None
    assert execution.report_snapshot.accepted_count == 1
    candidates = {candidate.entry_key.local_key: candidate for candidate in execution.report_snapshot.candidates}
    assert candidates["valid"].accepted is True
    assert candidates["valid"].text == "valid-updated"
    assert all(candidates[key].accepted is False for key in ("missing", "empty", "protected"))
    assert {diagnostic.code for diagnostic in execution.report_result.diagnostics} == {
        "PROOFREAD_RESPONSE_MISSING_KEY",
        "PROOFREAD_RESPONSE_EMPTY_TRANSLATION",
        "PROOFREAD_PROTECTED_SYNTAX_MISMATCH",
    }
    assert execution.commit_result is not None
    assert execution.commit_result.outcome is OperationOutcome.COMPLETED
    assert collection.apply_count == 1
    assert collection.get("valid").translation == "valid-updated"
    assert collection.get("missing").translation == missing.translation
    assert collection.get("empty").translation == empty.translation
    assert collection.get("protected").translation == protected.translation


def test_llm_call_failure_remains_a_workload_stage_failure() -> None:
    entry = _workload_input("failed", "Source")

    class FailingClient:
        @staticmethod
        def chat_prepared(messages_factory, max_tokens=0):
            messages_factory()
            raise TimeoutError("provider unavailable")

    result = PostProcessWorkload(
        (ProofreadStage(FailingClient(), max_tokens_per_batch=10_000),),
        stage_policy=StagePolicy(),
        stage_names=("proofread",),
    ).run("proofread-call-failed", (entry,))

    assert result.outcome is OperationOutcome.FAILED
    assert result.counts.failed == 1
    assert result.value is None
    assert [diagnostic.code for diagnostic in result.diagnostics] == ["PROOFREAD_LLM_CALL_FAILED"]
