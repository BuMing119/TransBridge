"""Regression coverage for replacing mandatory term closure with AI review."""

from dataclasses import replace
import json

import pytest

from transbridge.application.io import EntryKey, EntryRevision, SourceNamespace
from transbridge.application.translation.postprocess import PostProcessCandidate
from transbridge.application.translation.proofread_stage import ProofreadStage
from transbridge.converter.translation_entry import STAGE_QUESTIONABLE


def candidate(original, text, *, details=()):
    return PostProcessCandidate(
        "review",
        EntryKey(SourceNamespace("plugin"), "name"),
        EntryRevision(),
        original,
        text,
        text,
        1,
        context="任务名称",
        report_details=details,
    )


class Client:
    def __init__(self, status="retained", reason="当前译文符合任务语境", text=None):
        self.requests = []
        self.status, self.reason, self.text = status, reason, text

    def chat(self, messages, _max_tokens):
        request = json.loads(messages[1]["content"])
        self.requests.append((messages[0]["content"], request))
        return json.dumps(
            {
                "results": [
                    {
                        "entry_key": row["entry_key"],
                        "final_translation": self.text or row["current_translation"],
                        "review_status": self.status,
                        "review_reason": self.reason,
                    }
                    for row in request["entries"]
                ]
            },
            ensure_ascii=False,
        )


@pytest.mark.parametrize(
    "source,text,terms",
    [
        ("Bruma guards fighting predators", "布鲁玛卫兵对抗掠食者", {"Guards": "瓜尔"}),
        (
            "Handler for first Rielle playthrough events",
            "瑞尔勒首次通关事件处理器",
            {"Rielle": "1.瑞尔勒 2.美丽；美人"},
        ),
    ],
)
def test_contextual_retention_is_accepted_once_despite_literal_term_difference(source, text, terms):
    item = candidate(source, text)
    client = Client("term_suspect", "参考词义不适用于当前任务名称，保留译文")

    class ForbiddenRefiner:
        def refine_batch(self, *_args, **_kwargs):
            pytest.fail("A justified review must not trigger mandatory terminology repair")

    outcome = ProofreadStage(client, term_resolver=lambda _: terms, refiner=ForbiddenRefiner())((item,))

    assert len(client.requests) == 1
    result = outcome.candidates[0]
    assert result.accepted and result.text == text and result.before_text == text
    assert result.phases == ("proofread",)
    assert not outcome.diagnostics
    details = dict(result.report_details)
    assert details["review_status"] == "term_suspect"
    assert details["review_contract_version"] == 1
    assert details["review_reference_terms"] == terms
    system, payload = client.requests[0]
    assert "fallible advisory" in system
    issue = payload["entries"][0]["detected_issues"][0]
    assert issue["issue_type"] == "term_mismatch"
    assert issue["standard_translation"] == next(iter(terms.values()))
    assert details["detected_issues"] == payload["entries"][0]["detected_issues"]


def test_all_existing_and_term_issues_are_sent_together_with_context():
    issue = {"issue_type": "low_quality", "message": "疑似遗漏否定关系", "severity": "warning"}
    item = candidate("Guards do not follow", "卫兵跟随", details=(("issues", [issue]),))
    client = Client("revised", "恢复否定关系；Guards 此处指卫兵", "卫兵不跟随")
    outcome = ProofreadStage(client, term_resolver=lambda _: {"Guards": "瓜尔"})((item,))
    sent = client.requests[0][1]["entries"][0]
    assert sent["original"] == item.original
    assert sent["context"] == "任务名称"
    assert {i["issue_type"] for i in sent["detected_issues"]} == {"low_quality", "term_mismatch"}
    result = outcome.candidates[0]
    assert result.text == "卫兵不跟随" and result.before_text == "卫兵跟随"
    assert dict(result.report_details)["review_status"] == "revised"


def test_uncertain_review_is_a_successful_candidate_marked_questionable():
    item = candidate("Whispers", "低语")
    client = Client("needs_review", "缺少上下文，无法确认是组织名还是普通名词")
    outcome = ProofreadStage(client, term_resolver=lambda _: {"Whispers": "低语学院"})((item,))
    result = outcome.candidates[0]
    assert result.accepted and result.stage == STAGE_QUESTIONABLE
    assert result.text == "低语"
    assert not outcome.diagnostics
    assert len(client.requests) == 1


def test_legacy_text_only_response_is_pending_review_without_false_evidence():
    item = candidate("Guards", "卫兵")

    class LegacyClient:
        def chat(self, *_args):
            return {"results": [{"entry_key": item.entry_key.to_dict(), "final_translation": "卫兵"}]}

    result = ProofreadStage(LegacyClient(), term_resolver=lambda _: {"Guards": "瓜尔"})((item,)).candidates[0]
    assert result.accepted and result.stage == STAGE_QUESTIONABLE
    details = dict(result.report_details)
    assert details["review_status"] == "needs_review"
    assert details["review_contract_version"] == 0
    assert "未返回" in details["review_reason"]


def test_term_matching_does_not_invent_differences_from_fragments():
    item = candidate("Guards", "卫兵")
    client = Client()
    ProofreadStage(client, term_resolver=lambda _: {"Guar": "瓜尔"})((item,))
    assert client.requests[0][1]["entries"][0]["detected_issues"] == []


def test_main_stage_collects_format_and_cross_entry_findings_before_one_joint_review():
    first = candidate("Guards %s", "卫兵（")
    second = replace(candidate("Guards %s", "守卫 %s"), entry_key=EntryKey(SourceNamespace("plugin"), "other"))
    client = Client("revised", "修复占位符并结合语境保留卫兵译法", "卫兵 %s")
    outcome = ProofreadStage(client, term_resolver=lambda _: {"Guards": "瓜尔"})((first, second))
    assert len(client.requests) == 1
    sent = client.requests[0][1]["entries"][0]
    types = {issue["issue_type"] for issue in sent["detected_issues"]}
    assert {"placeholder_mismatch", "cross_entry_inconsistency", "term_mismatch"} <= types
    assert outcome.candidates[0].accepted
    assert outcome.candidates[0].text == "卫兵 %s"


def test_frequent_original_has_bounded_wording_examples():
    from transbridge.application.translation.proofread_advisory import collect_review_findings

    items = tuple(
        replace(candidate("Guards", f"卫兵 {i}"), entry_key=EntryKey(SourceNamespace("plugin"), str(i)))
        for i in range(100)
    )
    results = collect_review_findings(items)
    issue = dict(results[0].report_details)["detected_issues"][0]
    assert issue["variant_count"] == 100
    assert len(issue["examples"]) == 8


def test_followup_review_recovers_response_failure_even_if_previous_phase_was_proofread():
    item = replace(candidate("Guards", "卫兵"), phases=("proofread",))

    class RecoveringClient(Client):
        def chat(self, messages, tokens):
            response = super().chat(messages, tokens)
            return {"results": []} if len(self.requests) == 1 else response

    client = RecoveringClient()
    outcome = ProofreadStage(client, term_resolver=lambda _: {"Guards": "瓜尔"})((item,))
    assert len(client.requests) == 2
    assert outcome.candidates[0].accepted
    assert dict(outcome.candidates[0].report_details)["review_contract_version"] == 1


@pytest.mark.parametrize(
    "reported,text,expected",
    [
        ("revised", "卫兵", "retained"),
        ("retained", "守卫", "revised"),
        ("term_suspect", "守卫", "term_suspect"),
        ("needs_review", "守卫", "needs_review"),
    ],
)
def test_review_labels_reflect_actual_text_changes_without_losing_suspect_or_pending_status(reported, text, expected):
    item = candidate("Guards", "卫兵")
    outcome = ProofreadStage(Client(reported, "结合语境判断", text))((item,))
    assert dict(outcome.candidates[0].report_details)["review_status"] == expected
    assert dict(outcome.candidates[0].report_details)["review_contract_version"] == 1
