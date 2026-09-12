from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest

from transbridge.infra.llm_tool_calling import LlmToolCall, LlmTurn
from transbridge.smart_assistant.request_evaluation import (
    capture_live,
    evaluate_capture,
    evaluate_corpus,
    input_digest,
    make_capture,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures/assistant_request_routing"
CORPUS = json.loads((FIXTURES / "corpus.json").read_text(encoding="utf-8"))
CAPTURES = json.loads((FIXTURES / "synthetic_captures.json").read_text(encoding="utf-8"))


def case_pair(case_id):
    return (
        deepcopy(next(c for c in CORPUS["cases"] if c["case_id"] == case_id)),
        deepcopy(next(c for c in CAPTURES if c["case_id"] == case_id)),
    )


def load_cli():
    spec = importlib.util.spec_from_file_location(
        "evaluate_requests_cli", ROOT / "scripts/evaluate_assistant_requests.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("case_id", [case["case_id"] for case in CORPUS["cases"]])
def test_synthetic_controls_exercise_production_routing_and_state(case_id):
    case, capture = case_pair(case_id)
    result = evaluate_capture(case, capture)
    assert result["status"] == "passed", result


def test_synthetic_success_does_not_claim_live_model_acceptance():
    result = evaluate_corpus(CORPUS, CAPTURES)
    assert result["synthetic_passed"] == 12
    assert result["live_passed"] == 0
    assert result["live_required"] == 9
    assert result["live_acceptance"] == "not_passed"
    empty = evaluate_corpus(CORPUS, [])
    assert empty["counts"] == {"not_captured": 12}
    assert empty["live_acceptance"] == "not_passed"


def test_invalid_revision_returns_real_clarification_and_retains_original_request():
    case, capture = case_pair("stale_revision")
    # The exact same user command would cancel if incorrectly checked against the old snapshot.
    result = evaluate_capture(case, capture)
    assert result["directives"][0]["diagnostic"] == "REQUEST_REVISION_CONFLICT"
    capture["turn"]["tool_calls"][0]["arguments"]["directives"][0]["expected_revision"] = 2
    mutated = evaluate_capture(case, capture)
    assert mutated["status"] == "failed"
    assert any("unintended change" in error for error in mutated["errors"])


def test_quoted_command_misrouting_fails_due_to_cancelled_original_state():
    case, capture = case_pair("quoted_cancel")
    capture["turn"]["tool_calls"][0]["arguments"]["directives"] = [
        {
            "local_id": "bad-cancel",
            "message_id": "input",
            "span": [0, 10],
            "action": "CANCEL",
            "target_id": "translate",
            "expected_revision": 1,
        }
    ]
    result = evaluate_capture(case, capture)
    assert result["status"] == "failed"
    assert any("unintended change" in error for error in result["errors"])


def test_correct_label_but_invalid_source_span_is_not_accepted():
    case, capture = case_pair("new_question")
    capture["turn"]["tool_calls"][0]["arguments"]["directives"][0]["span"] = [0, 999]
    result = evaluate_capture(case, capture)
    assert result["status"] == "failed"
    assert result["rejection"] == "REQUEST_PROTOCOL_INVALID"


def test_amend_cannot_silently_replace_existing_goal():
    case, capture = case_pair("amend")
    capture["turn"]["tool_calls"][0]["arguments"]["directives"][0]["goal"] = "删除所有文件"
    result = evaluate_capture(case, capture)
    assert result["status"] == "failed"
    assert any(".goal" in error for error in result["errors"])


def test_new_request_goal_must_cover_the_question_subject():
    case, capture = case_pair("new_question")
    case["expected"]["created"][0]["goal_terms"] = ["术语"]
    capture["turn"]["tool_calls"][0]["arguments"]["directives"][0]["goal"] = "解释天气"
    result = evaluate_capture(case, capture)
    assert result["status"] == "failed"
    assert any(".goal" in error for error in result["errors"])


def test_missing_independent_directive_is_a_failure():
    case, capture = case_pair("multiple_directives")
    capture["turn"]["tool_calls"][0]["arguments"]["directives"].pop()
    result = evaluate_capture(case, capture)
    assert result["status"] == "failed"
    assert "expected 2 directives, received 1" in result["errors"]


@pytest.mark.parametrize("mutation", ["user_text", "state", "visible_state"])
def test_capture_cannot_be_reused_against_different_inputs(mutation):
    case, capture = case_pair("stale_revision")
    if mutation == "user_text":
        case["batch"]["sources"][0]["text"] = "继续翻译"
    elif mutation == "state":
        case["requests"][0]["revision"] = 3
    else:
        case["visible_requests"][0]["revision"] = 2
    assert input_digest(case) != capture["input_digest"]
    assert evaluate_capture(case, capture)["status"] == "failed"


def test_wrong_target_even_with_same_kind_of_request_fails():
    case, capture = case_pair("progress")
    other = deepcopy(case["requests"][0])
    other["request_id"] = "other-translation"
    case["requests"].append(other)
    capture["input_digest"] = input_digest(case)
    capture["turn"]["tool_calls"][0]["arguments"]["directives"][0]["target_id"] = other["request_id"]
    result = evaluate_capture(case, capture)
    assert result["status"] == "failed"
    assert any("related_to" in error for error in result["errors"])


def test_capture_live_uses_production_prompt_and_only_routing_tool_without_business_dispatch():
    case, saved = case_pair("progress")

    class DeterministicClient:
        def chat_stream_with_tools(self, messages, max_tokens, tools, chunk_callback):
            assert max_tokens == 4096
            assert [tool.name for tool in tools] == ["submit_request_routing"]
            data = json.loads(messages[1]["content"])
            assert data["inputs"][0]["text"] == "现在翻译到哪了？"
            assert data["requests"][0]["request_id"] == "translate"
            chunk_callback("streamed commentary is not a control")
            return LlmTurn(
                tool_calls=tuple(LlmToolCall.from_dict(c) for c in saved["turn"]["tool_calls"]),
                stop_reason="tool_calls",
                provider_content=({"provider_private": "must not persist"},),
            )

    # A fake client tests capture plumbing only; this result is not saved as real model evidence.
    captured = capture_live(case, DeterministicClient(), model="fake-model", provider="fake-provider")
    assert "provider_content" not in captured["turn"]
    assert evaluate_capture(case, captured)["status"] == "passed"


def test_missing_live_provenance_fails_and_duplicate_capture_ids_are_rejected():
    case, capture = case_pair("progress")
    capture["origin"] = "live"
    assert evaluate_capture(case, capture)["status"] == "failed"
    with pytest.raises(ValueError, match="duplicate"):
        evaluate_corpus(CORPUS, [CAPTURES[0], CAPTURES[0]])
    with pytest.raises(ValueError, match="provider and model"):
        make_capture(case, LlmTurn(), origin="live")


def test_cli_replay_never_opens_model_configuration(monkeypatch, capsys):
    module = load_cli()
    import transbridge.infra.llm_client as llm_client

    def forbidden(*args, **kwargs):
        raise AssertionError("offline replay must not create a model client")

    monkeypatch.setattr(llm_client, "create_llm_client", forbidden)
    args = ["--replay", str(FIXTURES / "synthetic_captures.json")]
    assert module.main(args) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["live_acceptance"] == "not_passed"
    assert module.main([*args, "--require-live"]) == 1
    capsys.readouterr()
    assert module.main([]) == 2
    assert json.loads(capsys.readouterr().out)["counts"] == {"not_captured": 12}


@pytest.mark.parametrize(
    "provider,override,expected_url",
    [
        ("openai_compatible", None, "https://api.openai.com/v1"),
        ("openai_compatible", "https://example.invalid/v1", "https://example.invalid/v1"),
        ("anthropic", None, ""),
    ],
)
def test_live_cli_selects_provider_configuration_without_network(
    provider, override, expected_url, monkeypatch, capsys, tmp_path
):
    import transbridge.infra.llm_client as llm_client

    sentinel = "synthetic-test-key-never-print"
    monkeypatch.setenv("TRANSBRIDGE_EVAL_API_KEY", sentinel)
    calls = []

    class LocalClient:
        def chat_stream_with_tools(self, messages, max_tokens, tools, chunk_callback):
            case_id = json.loads(messages[1]["content"])["batch_id"]
            _, capture = case_pair(case_id)
            data = capture["turn"]
            return LlmTurn(
                text=data["text"],
                tool_calls=tuple(LlmToolCall.from_dict(c) for c in data["tool_calls"]),
                stop_reason=data["stop_reason"],
            )

    def factory(config):
        calls.append((config.provider, config.base_url))
        assert config.api_key == sentinel
        return LocalClient()

    monkeypatch.setattr(llm_client, "create_llm_client", factory)
    output = tmp_path / "local-test-capture.json"
    args = ["--live", "--provider", provider, "--model", "local-test-double", "--output", str(output)]
    if override:
        args.extend(["--base-url", override])
    assert load_cli().main([*args, "--require-live"]) == 0
    assert calls == [(provider, expected_url)]
    stdout = capsys.readouterr().out
    saved = output.read_text(encoding="utf-8")
    assert sentinel not in stdout + saved
    assert len(json.loads(saved)) == 9


def test_anthropic_custom_endpoint_is_rejected_before_client_or_file_access(monkeypatch, tmp_path):
    import transbridge.infra.llm_client as llm_client

    def forbidden(config):
        raise AssertionError("unsupported endpoint must fail before constructing a client")

    monkeypatch.setattr(llm_client, "create_llm_client", forbidden)
    output = tmp_path / "not-created.json"
    with pytest.raises(SystemExit) as error:
        load_cli().main([
            "--live",
            "--provider",
            "anthropic",
            "--base-url",
            "https://example.invalid",
            "--model",
            "test-model",
            "--output",
            str(output),
        ])
    assert error.value.code == 2
    assert not output.exists()


def test_plain_text_model_response_fails_one_case_without_aborting_the_corpus():
    captures = deepcopy(CAPTURES)
    captures[0]["turn"] = {"text": "好的", "tool_calls": [], "stop_reason": "stop"}
    report = evaluate_corpus(CORPUS, captures)
    assert report["counts"] == {"failed": 1, "passed": 11}
    assert report["results"][0]["rejection"] == "LlmToolProtocolError"


def test_absent_parsed_control_is_reported_as_protocol_failure(monkeypatch):
    import transbridge.smart_assistant.request_evaluation as evaluation

    monkeypatch.setattr(evaluation, "parse_control_turn", lambda turn, stage: None)
    case, capture = case_pair("new_question")
    result = evaluate_capture(case, capture)
    assert result["status"] == "failed"
    assert result["rejection"] == "LlmToolProtocolError"
