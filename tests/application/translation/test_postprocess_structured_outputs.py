from __future__ import annotations

from dataclasses import replace
import json

import pytest

from transbridge.ai_translator.structured_schemas import POSTPROCESS_VALUES_OUTPUT_SCHEMA
from transbridge.application.io import EntryKey, EntryRevision, SourceNamespace
from transbridge.application.translation.postprocess import PostProcessCandidate
from transbridge.application.translation.postprocess_stages import (
    LlmClientPostProcessPort,
    OpenAiPostProcessHttpPort,
    PostProcessLlmError,
    PostProcessLlmPhase,
    PostProcessLlmRequest,
)
from transbridge.infra.llm_structured_outputs import extract_structured_output_directive, openai_responses_text_config


def _request() -> PostProcessLlmRequest:
    candidate = PostProcessCandidate(
        run_id="run-1",
        entry_key=EntryKey(SourceNamespace.legacy(), "entry-1"),
        before_revision=EntryRevision(),
        original="Open the door.",
        before_text="打开门。",
        text="打开门。",
        stage=1,
    )
    return PostProcessLlmRequest(PostProcessLlmPhase.REFINE, "run-1", (candidate,))


def _result_payload(request: PostProcessLlmRequest) -> dict:
    return {
        "results": [
            {
                "entry_key": request.candidates[0].entry_key.to_dict(),
                "value": "把门打开。",
            }
        ]
    }


def test_llm_client_postprocess_port_attaches_native_schema() -> None:
    request = _request()

    class CapturingClient:
        def chat(self, messages, max_tokens=0):
            _clean_messages, output_schema = extract_structured_output_directive(messages)
            assert output_schema == POSTPROCESS_VALUES_OUTPUT_SCHEMA
            return json.dumps(_result_payload(request), ensure_ascii=False)

    response = LlmClientPostProcessPort(CapturingClient()).apply(PostProcessLlmPhase.REFINE, request)

    assert response.by_key()[request.candidates[0].entry_key] == "把门打开。"


def test_raw_openai_postprocess_port_sends_responses_schema(monkeypatch) -> None:
    postprocess_request = _request()
    captured_payload = {}
    provider_response = {
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(_result_payload(postprocess_request), ensure_ascii=False),
                    }
                ],
            }
        ],
    }

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit):
            return json.dumps(provider_response, ensure_ascii=False).encode()

    def fake_urlopen(http_request, timeout):
        del timeout
        assert http_request.full_url.endswith("/responses")
        captured_payload.update(json.loads(http_request.data.decode()))
        return Response()

    monkeypatch.setattr("transbridge.application.translation.postprocess_stages.request.urlopen", fake_urlopen)

    response = OpenAiPostProcessHttpPort().apply(PostProcessLlmPhase.REFINE, postprocess_request)

    assert captured_payload["text"] == openai_responses_text_config(POSTPROCESS_VALUES_OUTPUT_SCHEMA)
    assert captured_payload["store"] is False
    assert response.by_key()[postprocess_request.candidates[0].entry_key] == "把门打开。"


def test_raw_deepseek_postprocess_port_uses_responses_schema(monkeypatch) -> None:
    postprocess_request = replace(_request(), base_url="https://api.deepseek.com/v1")
    captured_payload = {}
    fenced_result = f"```json\n{json.dumps(_result_payload(postprocess_request), ensure_ascii=False)}\n```"
    provider_response = {
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": fenced_result,
                    }
                ],
            }
        ],
    }

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit):
            return json.dumps(provider_response, ensure_ascii=False).encode()

    def fake_urlopen(http_request, timeout):
        del timeout
        assert http_request.full_url == "https://api.deepseek.com/v1/responses"
        captured_payload.update(json.loads(http_request.data.decode()))
        return Response()

    monkeypatch.setattr("transbridge.application.translation.postprocess_stages.request.urlopen", fake_urlopen)

    response = OpenAiPostProcessHttpPort().apply(PostProcessLlmPhase.REFINE, postprocess_request)

    assert captured_payload["text"] == openai_responses_text_config(POSTPROCESS_VALUES_OUTPUT_SCHEMA)
    assert response.by_key()[postprocess_request.candidates[0].entry_key] == "把门打开。"

    provider_response["output"][0]["content"][0]["text"] = fenced_result + " extra explanation"
    with pytest.raises(PostProcessLlmError) as caught:
        OpenAiPostProcessHttpPort().apply(PostProcessLlmPhase.REFINE, postprocess_request)
    assert caught.value.code == "POSTPROCESS_RESPONSE_MALFORMED"
