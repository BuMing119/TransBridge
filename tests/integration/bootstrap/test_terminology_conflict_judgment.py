import json
from types import SimpleNamespace

import pytest

from tests.application.terminology.test_build import _entry
from tests.integration.bootstrap.test_terminology_llm_build import build, workflow as _workflow
from transbridge.application.terminology.build import FullBuildCancelled
from transbridge.application.terminology.conflict_queries import ConflictFilter
from transbridge.application.terminology.models import ConflictStatus
from transbridge.application.terminology.ports import PageRequest
from transbridge.bootstrap.terminology_workloads import PublishRunner

pytestmark = pytest.mark.integration

workflow = _workflow


def configure(workflow):
    harness, inputs, config, model = workflow
    config.max_tokens_per_batch = 16000
    name = _entry("source-a", "name", "Cyrodilic", "西罗帝尔")
    language = _entry("source-a", "language", "Cyrodilic", "西罗帝尔语")
    harness.parser.entries["source-a"] = (name, language, *harness.parser.entries["source-a"])
    original = model.chat
    model.judgments = 0
    model.judgment_failure = False

    def chat(messages, max_tokens=0):
        if "Judge whether bilingual terminology" not in messages[0]["content"]:
            return original(messages, max_tokens)
        model.judgments += 1
        if model.judgment_failure:
            raise TimeoutError("judgment unavailable")
        payload = json.loads(messages[-1]["content"])
        return json.dumps({
            "conflict_group_id": payload["conflict_group_id"],
            "classification": "equivalent",
            "rationale": "原文均为 Cyrodilic，译文的语是正常语言后缀，核心术语一致。",
            "canonical_translation": "西罗帝尔",
        })

    model.chat = chat
    return harness, inputs, model


def test_extraction_cache_hit_still_judges_and_equivalent_can_publish(workflow):
    harness, inputs, model = configure(workflow)
    first = build(harness, inputs)
    assert len(model.calls) == 1
    assert model.judgments == 1
    assert len([item for item in first.decisions if item.normalized_original == "cyrodilic"]) == 1
    second = build(harness, inputs)
    assert len(model.calls) == 1  # successful text extraction reused
    assert model.judgments == 2  # independent fresh conflict judgment
    result = harness.repository.latest_build("project-1", "main")
    assert result.summary.conflict_count == 0
    assert result.conflicts[0].status is ConflictStatus.EQUIVALENT
    assert "核心术语一致" in result.conflicts[0].notes
    assert (
        harness.repository.list_conflicts(
            result.ref, PageRequest(), filters=ConflictFilter(include_equivalent=False)
        ).total
        == 0
    )
    assert harness.repository.list_conflicts(result.ref, PageRequest()).total == 1
    assert all(item.is_effective for item in second.decisions)
    harness.commands.publish(harness.context, config={"llm_enabled": True})
    execution = PublishRunner(harness.repositories, harness.state)(harness.submit.request, harness.runtime)
    assert harness.commit.commit_if_current(harness.submit.request.expected, execution.mutation).current
    version = harness.repository.effective_version("project-1", "main")
    assert version.conflicts[0].status is ConflictStatus.EQUIVALENT


def test_judgment_failure_keeps_conflict_and_retry_does_not_reextract(workflow):
    harness, inputs, model = configure(workflow)
    model.judgment_failure = True
    build(harness, inputs)
    result = harness.repository.latest_build("project-1", "main")
    assert result.summary.conflict_count == 1
    assert result.conflicts[0].status is ConflictStatus.UNRESOLVED
    assert "TimeoutError" in result.conflicts[0].notes
    model.judgment_failure = False
    build(harness, inputs)
    assert len(model.calls) == 1
    assert model.judgments == 2
    assert harness.repository.latest_build("project-1", "main").summary.conflict_count == 0


def test_cancelling_judgment_preserves_existing_build_and_draft(workflow):
    harness, inputs, model = configure(workflow)
    existing = build(harness, inputs)
    previous = harness.repository.latest_build("project-1", "main")
    token = SimpleNamespace(is_cancelled=False)
    harness.runtime.cancellation = token
    original = model.chat

    def cancel_after_response(messages, max_tokens=0):
        response = original(messages, max_tokens)
        token.is_cancelled = True
        return response

    model.chat = cancel_after_response
    with pytest.raises(FullBuildCancelled):
        build(harness, inputs)
    assert harness.repository.latest_build("project-1", "main") == previous
    assert harness.repository.active_draft("project-1", "main") == existing
