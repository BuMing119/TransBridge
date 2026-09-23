"""Replay routing captures through production validation without business execution.

Synthetic controls test the harness and safety boundaries, never model accuracy.
Capture provenance is an operator assertion, not an authenticity attestation.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
import json
from typing import Any

from transbridge.application.assistant_requests.models import UserRequest, digest
from transbridge.application.assistant_requests.routing import (
    RoutingBatch,
    apply_proposal,
    parse_proposal,
)
from transbridge.infra.llm_tool_calling import LlmToolCall, LlmToolProtocolError, LlmTurn, require_complete_tool_call
from transbridge.smart_assistant.request_protocol import parse_control_turn, routing_definition
from transbridge.smart_assistant.request_router import routing_messages


def case_input(case: dict) -> tuple[RoutingBatch, tuple[UserRequest, ...], list[dict]]:
    """Use the same routing prompt/schema as the application, including snapshot races."""
    batch = RoutingBatch.from_dict(case["batch"])
    current = tuple(UserRequest.from_dict(raw) for raw in case["requests"])
    visible = tuple(UserRequest.from_dict(raw) for raw in case.get("visible_requests", case["requests"]))
    return batch, current, routing_messages(batch, visible, history=case.get("history", ()))


def input_digest(case: dict) -> str:
    batch, current, messages = case_input(case)
    return digest({
        "messages": messages,
        "tool": asdict(routing_definition()),
        "current_requests": [request.to_dict() for request in current],
        "batch": batch.to_dict(),
    })


def make_capture(case: dict, turn: LlmTurn, *, origin: str, model: str = "", provider: str = "") -> dict:
    if origin not in {"synthetic", "live"}:
        raise ValueError("capture origin must be synthetic or live")
    if origin == "live" and (not model or not provider):
        raise ValueError("live captures require provider and model labels")
    return {
        "case_id": case["case_id"],
        "input_digest": input_digest(case),
        "origin": origin,
        "model": model,
        "provider": provider,
        "captured_at": datetime.now(UTC).isoformat(),
        "turn": {
            "text": turn.text,
            "tool_calls": [call.to_dict() for call in turn.tool_calls],
            "stop_reason": turn.stop_reason,
        },
    }


def capture_live(case: dict, client: Any, *, model: str, provider: str, max_tokens: int = 4096) -> dict:
    """Explicit caller opt-in only; no credentials/configuration enter captures."""
    _, _, messages = case_input(case)
    turn = client.chat_stream_with_tools(messages, max_tokens, [routing_definition()], lambda _text: None)
    return make_capture(case, turn, origin="live", model=model, provider=provider)


def _turn(data: dict) -> LlmTurn:
    calls = tuple(LlmToolCall.from_dict(raw) for raw in data.get("tool_calls", ()))
    for call in calls:
        require_complete_tool_call(call_id=call.id, tool_name=call.name, stop_reason=data.get("stop_reason"))
    return LlmTurn(text=data.get("text", ""), tool_calls=calls, stop_reason=data.get("stop_reason"))


def _subset(actual: dict, expected: dict, label: str, errors: list[str]) -> None:
    actual = json.loads(json.dumps(actual))
    expected = json.loads(json.dumps(expected))
    for key, value in expected.items():
        if actual.get(key) != value:
            errors.append(f"{label}.{key}: expected {value!r}, received {actual.get(key)!r}")


def evaluate_capture(case: dict, capture: dict) -> dict:
    """Check actual effects of a proposal as well as its claimed intent."""
    result = {"case_id": case["case_id"], "origin": capture.get("origin", "unknown"), "status": "failed", "errors": []}
    errors = result["errors"]
    if capture.get("case_id") != case["case_id"] or capture.get("input_digest") != input_digest(case):
        errors.append("capture does not match current routing inputs/schema/state")
        return result
    if capture.get("origin") not in {"synthetic", "live"}:
        errors.append("capture provenance is missing or unknown")
        return result
    if capture["origin"] == "live" and any(not capture.get(k) for k in ("model", "provider", "captured_at")):
        errors.append("live capture provenance is incomplete")
        return result
    batch, requests, _ = case_input(case)
    expected = case["expected"]
    try:
        control = parse_control_turn(_turn(capture["turn"]), "routing")
        if control is None:
            raise LlmToolProtocolError("Routing response is missing request control")
        proposal = parse_proposal(control["arguments"])
        applied = apply_proposal(requests, batch, proposal)
    except (ValueError, TypeError, KeyError) as exc:
        code = getattr(exc, "code", type(exc).__name__)
        result["rejection"] = code
        if expected.get("rejection") != code:
            errors.append(f"unexpected production rejection: {code}")
        result["status"] = "failed" if errors else "passed"
        return result
    if "rejection" in expected:
        errors.append(f"expected production rejection: {expected['rejection']}")
    receipts = {receipt.local_id: receipt for receipt in applied.batch.receipts}
    observed = []
    for directive in proposal.directives:
        receipt = receipts[directive["local_id"]]
        observed.append({
            "action": directive["action"],
            "target_id": directive.get("target_id", ""),
            "status": receipt.status,
            "diagnostic": receipt.diagnostic.split(":", 1)[0] if receipt.diagnostic else "",
        })
    result["directives"] = observed
    wanted = expected.get("directives", [])
    if len(observed) != len(wanted):
        errors.append(f"expected {len(wanted)} directives, received {len(observed)}")
    for index, (actual, desired) in enumerate(zip(observed, wanted, strict=False)):
        _subset(actual, desired, f"directive[{index}]", errors)
    by_id = {request.request_id: request for request in applied.requests}
    for original in requests:
        desired = expected.get("existing", {}).get(original.request_id)
        if desired is None:
            if by_id[original.request_id] != original:
                errors.append(f"unintended change to request {original.request_id}")
        else:
            preserved = {"goal": original.goal, "scope": original.scope}
            _subset(
                by_id[original.request_id].to_dict(),
                {**preserved, **desired},
                f"request[{original.request_id}]",
                errors,
            )
    original_ids = {request.request_id for request in requests}
    created = [request for request in applied.requests if request.request_id not in original_ids]
    wanted_created = expected.get("created", [])
    if len(created) != len(wanted_created):
        errors.append(f"expected {len(wanted_created)} created requests, received {len(created)}")
    for index, (actual, desired) in enumerate(zip(created, wanted_created, strict=False)):
        projection = {
            "related_to": actual.related_to,
            "status": actual.status,
            "item_kinds": [item.kind for item in actual.items],
            "source_message_ids": list(actual.source_message_ids),
        }
        _subset(projection, {k: v for k, v in desired.items() if k != "goal_terms"}, f"created[{index}]", errors)
        if any(term not in actual.goal for term in desired.get("goal_terms", ())):
            errors.append(f"created[{index}].goal does not cover required scenario terms")
    result["status"] = "failed" if errors else "passed"
    return result


def evaluate_corpus(corpus: dict, captures: list[dict]) -> dict:
    cases = corpus["cases"]
    ids = [case["case_id"] for case in cases]
    if corpus.get("schema_version") != 1 or not cases or len(ids) != len(set(ids)):
        raise ValueError("corpus requires schema_version=1 and distinct nonempty cases")
    capture_ids = [capture["case_id"] for capture in captures]
    if len(capture_ids) != len(set(capture_ids)) or set(capture_ids) - set(ids):
        raise ValueError("captures contain duplicate or unknown case IDs")
    by_id = {capture["case_id"]: capture for capture in captures}
    results = [
        evaluate_capture(case, by_id[case["case_id"]])
        if case["case_id"] in by_id
        else {"case_id": case["case_id"], "status": "not_captured", "origin": "none", "errors": []}
        for case in cases
    ]
    live_ids = {case["case_id"] for case in cases if not case.get("replay_only", False)}
    live = [result for result in results if result["origin"] == "live" and result["case_id"] in live_ids]
    return {
        "schema_version": 1,
        "corpus_id": corpus["corpus_id"],
        "counts": dict(Counter(result["status"] for result in results)),
        "synthetic_passed": sum(r["status"] == "passed" and r["origin"] == "synthetic" for r in results),
        "live_passed": sum(r["status"] == "passed" for r in live),
        "live_required": len(live_ids),
        "live_acceptance": "passed"
        if live_ids and len(live) == len(live_ids) and all(r["status"] == "passed" for r in live)
        else "not_passed",
        "results": results,
    }
