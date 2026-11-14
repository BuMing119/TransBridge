from dataclasses import replace
import json
from types import SimpleNamespace

import httpx
import pytest

from tests.application.terminology.test_build import _entry
from tests.integration.bootstrap.test_terminology_build_review import _Harness
from transbridge.application.contracts import OperationOutcome
from transbridge.application.terminology.identity import canonical_digest
from transbridge.application.terminology.models import DecisionStatus, ExtractionMethod, LlmExtractionStatus
from transbridge.bootstrap.terminology_build import BuildRunner
from transbridge.bootstrap.terminology_extraction import ProductionTerminologyBuildInputs
from transbridge.bootstrap.terminology_storage import sha256_part
from transbridge.bootstrap.terminology_workloads import PublishRunner
from transbridge.config.llm import LLMConfig
from transbridge.infra import llm_client
from transbridge.infra.llm_client import LLMClient
from transbridge.persistence.terminology.repository import SqliteTerminologyRepository

pytestmark = pytest.mark.integration


class Model(LLMClient):
    def __init__(self):
        self.calls = []
        self.configs = []
        self.closed = 0
        self.fail = False

    def factory(self, config):
        self.configs.append(config.copy_for_execution())
        return self

    def chat(self, messages, max_tokens=0):
        self.calls.append(messages)
        if self.fail:
            raise TimeoutError("test provider stalled")
        content = messages[-1]["content"]
        inputs = json.loads(content[content.index("[") :])
        return json.dumps({
            "results": [
                {"evidence_id": item["evidence_id"], "term": "Whiterun", "translation": "白漫城"} for item in inputs
            ]
        })

    def close(self):
        self.closed += 1


@pytest.fixture
def workflow(tmp_path, monkeypatch):
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    harness = _Harness(repository, monkeypatch)
    model = Model()
    config = LLMConfig(model="test-terminology", api_key="test-secret", max_concurrent=2)
    text = _entry("source-a", "text", "We must return to Whiterun.", "我们必须返回白漫城。")
    text.context = "INFO:NAM1"
    # Use the existing real ParseResult fixture parser and preserve registered source identity.
    from tests.application.terminology.test_build import _Parser

    harness.parser = _Parser({"source-a": (text,)})
    monkeypatch.setattr(
        "transbridge.bootstrap.terminology_build.TranslationIoRegisteredSourceParser", lambda io: harness.parser
    )

    def capture(context, *, config):
        result = harness.capture_build_input(context, config=config)
        digest = sha256_part(canonical_digest(config, namespace="test"))
        return replace(result, value=replace(result.value, config_digest=digest))

    inputs = ProductionTerminologyBuildInputs(
        SimpleNamespace(capture_build_input=capture), config_loader=lambda: config, client_factory=model.factory
    )
    harness.commands._build_inputs = inputs
    harness.commands._publication._build_inputs = inputs
    try:
        yield harness, inputs, config, model
    finally:
        repository.close()


def build(harness, inputs, *, force=False):
    result = inputs.capture_build_input(harness.context, config={"llm_enabled": True, "force_llm": force})
    assert result.outcome is OperationOutcome.COMPLETED
    harness.commands.start_build(result.value, harness.context)
    execution = BuildRunner(harness.repositories, harness.state, None, harness.ids, harness.lifecycle)(
        harness.submit.request, harness.runtime
    )
    assert harness.commit.commit_if_current(harness.submit.request.expected, execution.mutation).current
    return harness.repository.active_draft("project-1", "main")


def test_production_model_candidates_without_differences_can_publish_without_individual_confirmation(workflow):
    harness, inputs, _, model = workflow
    draft = build(harness, inputs)
    assert len(model.calls) == 1
    assert model.closed == 1
    assert [(item.original, item.translation, item.status) for item in draft.decisions] == [
        ("Whiterun", "白漫城", DecisionStatus.ADOPTED)
    ]
    latest = harness.repository.latest_build("project-1", "main")
    assert latest.llm_status is LlmExtractionStatus.PERFORMED
    assert latest.candidates[0].extraction_method is ExtractionMethod.LLM_TEXT
    harness.commands.publish(harness.context, config={"llm_enabled": True})
    execution = PublishRunner(harness.repositories, harness.state)(harness.submit.request, harness.runtime)
    assert harness.commit.commit_if_current(harness.submit.request.expected, execution.mutation).current
    effective = harness.repository.effective_version("project-1", "main")
    assert effective.decisions[0].is_effective


def test_rebuild_reuses_success_force_refreshes_and_preserves_manual_confirmation(workflow):
    harness, inputs, _, model = workflow
    draft = build(harness, inputs)
    harness.commands.apply_decision(
        "change_translation", harness.context, term_id=draft.decisions[0].term_id, translation="白漫城"
    )
    assert build(harness, inputs).decisions[0].status is DecisionStatus.MANUAL_CONFIRMED
    assert len(model.calls) == 1
    assert build(harness, inputs, force=True).decisions[0].status is DecisionStatus.MANUAL_CONFIRMED
    assert len(model.calls) == 2


def test_model_configuration_and_prompt_are_frozen_without_serializing_credentials(workflow):
    harness, inputs, config, model = workflow
    captured = inputs.capture_build_input(harness.context, config={"llm_enabled": True}).value
    forced = inputs.capture_build_input(harness.context, config={"llm_enabled": True, "force_llm": True}).value
    assert captured.config_digest == forced.config_digest
    assert "test-secret" not in repr(captured)
    assert "test-secret" not in json.dumps(captured.extraction.identity)
    assert "test-secret" not in json.dumps(captured.canonical_payload())
    config.model = "changed-after-capture"
    changed = inputs.capture_build_input(harness.context, config={"llm_enabled": True}).value
    assert changed.config_digest != captured.config_digest
    harness.commands.start_build(captured, harness.context)
    BuildRunner(harness.repositories, harness.state, None, harness.ids, harness.lifecycle)(
        harness.submit.request, harness.runtime
    )
    assert model.configs[-1].model == "test-terminology"


def test_missing_configuration_is_explicit_and_rule_mode_still_works(workflow):
    harness, inputs, config, model = workflow
    config.api_key = ""
    failed = inputs.capture_build_input(harness.context, config={"llm_enabled": True})
    assert failed.outcome is OperationOutcome.FAILED
    assert "设置" in failed.diagnostics[0].message
    rules = inputs.capture_build_input(harness.context, config={"llm_enabled": False})
    assert rules.outcome is OperationOutcome.COMPLETED
    assert rules.value.extraction is None
    assert not model.calls


def test_provider_failure_is_partial_and_retry_builds_reviewable_terms(workflow):
    harness, inputs, _, model = workflow
    model.fail = True
    draft = build(harness, inputs)
    assert not draft.decisions
    latest = harness.repository.latest_build("project-1", "main")
    assert latest.llm_status is LlmExtractionStatus.PARTIAL
    assert latest.completeness.value == "partial"
    with pytest.raises(ValueError):
        latest.require_publishable()
    model.fail = False
    assert build(harness, inputs).decisions[0].original == "Whiterun"
    assert len(model.calls) == 2


def test_explicit_rules_mode_preserves_legacy_configuration_identity(workflow):
    harness, inputs, _, _ = workflow
    legacy = inputs.capture_build_input(harness.context, config={}).value
    selected = inputs.capture_build_input(harness.context, config={"llm_enabled": False, "force_llm": False}).value
    assert selected.config_digest == legacy.config_digest
    assert selected.extraction is None


@pytest.mark.parametrize("provider", ["openai_compatible", "anthropic"])
def test_production_factory_sends_real_sdk_requests_and_closes_run_resources(workflow, monkeypatch, provider):
    harness, inputs, config, _ = workflow
    config.provider = provider
    config.base_url = "https://offline.invalid/v1"
    config.llm_max_retries = 0
    requests = []
    clients = []
    monkeypatch.setattr("transbridge.infra.llm_reasoning.with_reasoning_intent", lambda client, *_: client)

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        content = payload["messages" if provider == "anthropic" else "input"][-1]["content"]
        if isinstance(content, list):
            content = content[0]["text"]
        evidence = json.loads(content[content.index("[") :])
        response = json.dumps({
            "results": [{"evidence_id": evidence[0]["evidence_id"], "term": "Whiterun", "translation": "白漫城"}]
        })
        if provider == "anthropic":
            return httpx.Response(
                200,
                json={
                    "id": "msg_offline",
                    "type": "message",
                    "role": "assistant",
                    "model": payload["model"],
                    "content": [{"type": "text", "text": response}],
                    "stop_reason": "end_turn",
                    "stop_sequence": None,
                    "usage": {"input_tokens": 30, "output_tokens": 15},
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "offline",
                "object": "response",
                "created_at": 0,
                "model": payload["model"],
                "status": "completed",
                "output": [
                    {
                        "id": "message",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": response, "annotations": []}],
                    }
                ],
                "usage": {"input_tokens": 30, "output_tokens": 15, "total_tokens": 45},
            },
        )

    def client(**kwargs):
        value = httpx.Client(transport=httpx.MockTransport(respond), **kwargs)
        clients.append(value)
        return value

    monkeypatch.setattr(llm_client, "httpx", SimpleNamespace(**{**vars(httpx), "Client": client}))
    inputs._client_factory = None
    try:
        draft = build(harness, inputs)
        assert draft.decisions[0].original == "Whiterun"
        assert draft.decisions[0].status is DecisionStatus.ADOPTED
        assert len(requests) == 1
        assert requests[0]["model"] == config.model
        schema = (
            requests[0]["output_config"]["format"]["schema"]
            if provider == "anthropic"
            else requests[0]["text"]["format"]["schema"]
        )
        assert "evidence_id" in schema["properties"]["results"]["items"]["required"]
        assert clients and all(client.is_closed for client in clients)
    finally:
        for client in clients:
            client.close()
