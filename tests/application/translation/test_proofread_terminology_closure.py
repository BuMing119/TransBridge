from __future__ import annotations

import json

import pytest

from transbridge.ai_translator.post_processor.llm_refiner import RefineResult
from transbridge.application.io import EntryKey, EntryRevision, SourceNamespace
from transbridge.application.translation.postprocess import PostProcessCandidate
from transbridge.application.translation.proofread_stage import ProofreadStage


def _candidate(
    local_key: str,
    *,
    namespace: str = "proofread-closure",
    original: str = "Dragon",
    text: str = "旧译",
    context: str = "Context",
    plugin_id: str | None = None,
) -> PostProcessCandidate:
    details = () if plugin_id is None else (("terminology_plugin_id", plugin_id),)
    return PostProcessCandidate(
        run_id="run",
        entry_key=EntryKey(SourceNamespace(namespace), local_key),
        before_revision=EntryRevision(),
        original=original,
        before_text=text,
        text=text,
        stage=2,
        context=context,
        report_details=details,
    )


class _ProofreadClient:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values
        self.messages: list[list[dict]] = []

    def chat_prepared(self, prepare, max_tokens=0):
        messages = prepare()
        self.messages.append(messages)
        entries = json.loads(messages[1]["content"])["entries"]
        return json.dumps(
            {
                "results": [
                    {
                        "entry_key": entry["entry_key"],
                        "final_translation": self.values[entry["entry_key"]["local_key"]],
                    }
                    for entry in entries
                ]
            },
            ensure_ascii=False,
        )

    def cancel(self) -> None:
        pass


class _Refiner:
    def __init__(self, values: dict[str, str] | None = None, *, valid: bool = True) -> None:
        self.values = values or {}
        self.valid = valid
        self.calls: list[tuple[list[object], dict, dict]] = []

    def refine_batch(self, entries, issues_map, *, terms_map=None):
        self.calls.append((entries, issues_map, terms_map))
        return {
            entry.id: RefineResult(
                entry_id=entry.id,
                original_translation=entry.translation,
                refined_translation=self.values.get(entry.key, entry.translation),
                confidence=1.0,
                valid=self.valid,
            )
            for entry in entries
        }


def _stage(client, terms, refiner, **kwargs) -> ProofreadStage:
    return ProofreadStage(
        client,
        term_resolver=lambda candidate: terms.get(candidate.entry_key, {}),
        refiner=refiner,
        model="unknown-model",
        max_tokens_per_batch=10_000,
        **kwargs,
    )


def test_first_prompt_is_broad_and_terms_are_constraints_not_a_detected_issue_list() -> None:
    candidate = _candidate("one", original="The Dragon is not hostile.")
    client = _ProofreadClient({"one": "巨龙并无敌意。"})
    terms = {candidate.entry_key: {"Dragon": "巨龙"}}

    outcome = _stage(client, terms, _Refiner())((candidate,))

    system = client.messages[0][0]["content"]
    user = json.loads(client.messages[0][1]["content"])
    assert outcome.candidates[0].text == "巨龙并无敌意。"
    assert all(
        concept in system
        for concept in (
            "semantic",
            "omissions",
            "mistranslations",
            "negation",
            "context",
            "terminology",
            "fluency",
            "style",
        )
    )
    assert "mandatory constraint" in system
    assert "not a complete list" in system
    assert "detected_issues" not in user["entries"][0]
    assert "only detected" not in system.casefold()


def test_no_refiner_call_when_proofread_already_satisfies_all_terms() -> None:
    candidate = _candidate("one")
    client = _ProofreadClient({"one": "巨龙"})
    refiner = _Refiner()

    outcome = _stage(client, {candidate.entry_key: {"Dragon": "巨龙"}}, refiner)((candidate,))

    assert refiner.calls == []
    assert outcome.candidates[0].accepted is True
    assert outcome.candidates[0].phases == ("proofread",)


def test_one_entry_sends_all_remaining_term_issues_in_one_refiner_call() -> None:
    candidate = _candidate("one", original="Dragon Sword")
    client = _ProofreadClient({"one": "龙剑"})
    refiner = _Refiner({"one": "巨龙之剑"})
    terms = {candidate.entry_key: {"Dragon": "巨龙", "Sword": "剑"}}

    outcome = _stage(client, terms, refiner)((candidate,))

    assert len(refiner.calls) == 1
    entries, issues_map, terms_map = refiner.calls[0]
    assert len(entries) == 1
    assert {issue.term for issue in issues_map[entries[0].id]} == {"Dragon"}
    assert terms_map[entries[0].id] == {"Dragon": "巨龙", "Sword": "剑"}
    assert outcome.candidates[0].text == "巨龙之剑"
    assert outcome.candidates[0].phases == ("proofread", "refinement")


def test_only_the_entry_with_a_remaining_term_problem_is_refined() -> None:
    satisfied = _candidate("satisfied")
    missing = _candidate("missing")
    client = _ProofreadClient({"satisfied": "巨龙", "missing": "龙"})
    refiner = _Refiner({"missing": "巨龙"})
    terms = {candidate.entry_key: {"Dragon": "巨龙"} for candidate in (satisfied, missing)}

    outcome = _stage(client, terms, refiner)((satisfied, missing))

    assert [[entry.key for entry in call[0]] for call in refiner.calls] == [["missing"]]
    assert [candidate.text for candidate in outcome.candidates] == ["巨龙", "巨龙"]


def test_multiple_issues_for_one_entry_stay_together() -> None:
    candidate = _candidate("one", original="Dragon Sword")
    client = _ProofreadClient({"one": "龙刀"})
    refiner = _Refiner({"one": "巨龙剑"})
    terms = {candidate.entry_key: {"Dragon": "巨龙", "Sword": "剑"}}

    outcome = _stage(client, terms, refiner)((candidate,))

    entries, issues_map, _terms_map = refiner.calls[0]
    assert [(issue.term, issue.standard_translation) for issue in issues_map[entries[0].id]] == [
        ("Dragon", "巨龙"),
        ("Sword", "剑"),
    ]
    assert outcome.candidates[0].accepted is True


def test_batch_keeps_entry_keys_issues_and_plugin_terms_isolated() -> None:
    project = _candidate("same", namespace="project", plugin_id=None)
    plugin = _candidate("same", namespace="plugin", plugin_id="Patch.esp")
    client = _ProofreadClient({"same": "错误"})
    refiner = _Refiner({"same": "项目龙插件龙"})
    terms = {
        project.entry_key: {"Dragon": "项目龙"},
        plugin.entry_key: {"Dragon": "插件龙"},
    }

    _stage(client, terms, refiner)((project, plugin))

    entries, issues_map, terms_map = refiner.calls[0]
    assert len(entries) == 2
    assert entries[0].id != entries[1].id
    assert terms_map[entries[0].id] == {"Dragon": "项目龙"}
    assert terms_map[entries[1].id] == {"Dragon": "插件龙"}
    assert issues_map[entries[0].id][0].standard_translation == "项目龙"
    assert issues_map[entries[1].id][0].standard_translation == "插件龙"


def test_refiner_result_still_missing_term_is_rejected_and_run_start_text_is_restored() -> None:
    candidate = _candidate("one", text="运行前译文")
    client = _ProofreadClient({"one": "首轮候选"})
    refiner = _Refiner({"one": "仍未采用"})

    outcome = _stage(client, {candidate.entry_key: {"Dragon": "巨龙"}}, refiner)((candidate,))

    assert outcome.candidates[0].text == "运行前译文"
    assert outcome.candidates[0].accepted is False
    assert outcome.candidates[0].phases == ()
    diagnostic = outcome.diagnostics[-1]
    assert diagnostic.code == "PROOFREAD_TERMINOLOGY_REFINEMENT_FAILED"
    assert dict(diagnostic.details)["reason"] == "terminology_still_inconsistent"


def test_refiner_placeholder_or_tag_damage_is_rejected() -> None:
    candidate = _candidate("one", original="Dragon %s <Alias=Hero>", text="旧译 %s <Alias=Hero>")
    client = _ProofreadClient({"one": "龙 %s <Alias=Hero>"})
    refiner = _Refiner({"one": "巨龙"})

    outcome = _stage(client, {candidate.entry_key: {"Dragon": "巨龙"}}, refiner)((candidate,))

    assert outcome.candidates[0].text == candidate.before_text
    assert outcome.candidates[0].accepted is False
    assert dict(outcome.diagnostics[-1].details)["reason"] == "protected_syntax_mismatch"


@pytest.mark.parametrize("value, valid", [("", True), ("巨龙", False)])
def test_empty_or_contract_invalid_refiner_result_fails_safely(value: str, valid: bool) -> None:
    candidate = _candidate("one")
    client = _ProofreadClient({"one": "首轮候选"})
    refiner = _Refiner({"one": value}, valid=valid)

    outcome = _stage(client, {candidate.entry_key: {"Dragon": "巨龙"}}, refiner)((candidate,))

    assert outcome.candidates[0].text == candidate.before_text
    assert outcome.candidates[0].accepted is False


@pytest.mark.parametrize(
    "failure_code, diagnostic_code, category",
    [
        ("call_failed", "PROOFREAD_TERMINOLOGY_REFINEMENT_FAILED", "external"),
        ("cancelled", "PROOFREAD_REFINEMENT_CANCELLED", "cancelled"),
        ("invalid_response", "PROOFREAD_TERMINOLOGY_REFINEMENT_FAILED", "input"),
    ],
)
def test_structured_refiner_failure_code_controls_actionable_diagnostic(
    failure_code: str,
    diagnostic_code: str,
    category: str,
) -> None:
    candidate = _candidate("one")
    client = _ProofreadClient({"one": "首轮候选"})

    class FailureRefiner:
        @staticmethod
        def refine_batch(entries, issues_map, *, terms_map=None):
            del issues_map, terms_map
            return {
                entry.id: RefineResult(
                    entry_id=entry.id,
                    original_translation=entry.translation,
                    refined_translation=entry.translation,
                    valid=False,
                    failure_code=failure_code,
                )
                for entry in entries
            }

    outcome = _stage(client, {candidate.entry_key: {"Dragon": "巨龙"}}, FailureRefiner())((candidate,))

    assert outcome.candidates[0].accepted is False
    assert outcome.diagnostics[-1].code == diagnostic_code
    assert outcome.diagnostics[-1].category.value == category


def test_no_terms_or_no_term_resolver_does_not_create_an_extra_call() -> None:
    candidate = _candidate("one")
    client = _ProofreadClient({"one": "开放校对后的译文"})
    refiner = _Refiner()

    outcome = _stage(client, {}, refiner)((candidate,))
    no_manager_client = _ProofreadClient({"one": "另一条开放校对译文"})
    no_manager = ProofreadStage(no_manager_client, refiner=refiner, max_tokens_per_batch=10_000)((candidate,))

    assert refiner.calls == []
    assert outcome.candidates[0].text == "开放校对后的译文"
    assert no_manager.candidates[0].text == "另一条开放校对译文"


def test_refinement_batch_size_is_bounded_without_splitting_entries() -> None:
    candidates = tuple(_candidate(str(index)) for index in range(6))
    client = _ProofreadClient({str(index): "错误" for index in range(6)})
    refiner = _Refiner({str(index): "巨龙" for index in range(6)})
    terms = {candidate.entry_key: {"Dragon": "巨龙"} for candidate in candidates}

    outcome = _stage(client, terms, refiner, refinement_batch_size=5)(candidates)

    assert [len(call[0]) for call in refiner.calls] == [5, 1]
    assert all(candidate.accepted for candidate in outcome.candidates)


def test_non_terminology_semantic_fix_remains_the_first_open_proofread_result() -> None:
    candidate = _candidate("one", original="The gate is not open.", text="门开着。")
    client = _ProofreadClient({"one": "门没有开。"})
    refiner = _Refiner()

    outcome = _stage(client, {}, refiner)((candidate,))

    assert refiner.calls == []
    assert outcome.candidates[0].text == "门没有开。"
    assert outcome.candidates[0].phases == ("proofread",)
