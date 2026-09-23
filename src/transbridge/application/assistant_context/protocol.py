"""Normalize transcript evidence and enforce complete native tool protocol groups."""

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from transbridge.infra.llm_tool_calling import LlmToolProtocolError
from transbridge.smart_assistant.context_budget import context_json


def protocol_groups(history: list[dict]) -> list[list[int]]:
    """Keep each assistant call and its immediately following results atomic."""
    groups: list[list[int]] = []
    index = 0
    seen_calls: set[str] = set()
    while index < len(history):
        message = history[index]
        calls = message.get("tool_calls", [])
        if calls:
            ids = [str(call.get("id", "")) for call in calls]
            if message.get("role") != "assistant" or not all(ids) or len(set(ids)) != len(ids):
                raise LlmToolProtocolError("Invalid assistant tool call group in context history")
            if seen_calls.intersection(ids):
                raise LlmToolProtocolError("Duplicate historical tool call IDs")
            seen_calls.update(ids)
            group = [index]
            pending = set(ids)
            while pending and index + 1 < len(history):
                result = history[index + 1]
                call_id = str(result.get("tool_call_id", ""))
                if result.get("role") != "tool" or call_id not in pending:
                    break
                pending.remove(call_id)
                index += 1
                group.append(index)
            if pending:
                raise LlmToolProtocolError("Incomplete native tool call group; close pending calls before dispatch")
            groups.append(group)
        elif message.get("role") == "tool":
            raise LlmToolProtocolError("Orphan tool result in context history")
        else:
            groups.append([index])
        index += 1
    return groups


def normalize_records(history: Sequence[Mapping[str, Any]]) -> list[dict]:
    records = []
    seen: dict[str, dict] = {}
    for index, source in enumerate(history):
        message = deepcopy(dict(source))
        message_id = str(message.get("message_id") or f"legacy-message-{index}")
        message["message_id"] = message_id
        if message_id in seen:
            if message != seen[message_id]:
                raise ValueError(f"Conflicting content for message ID {message_id}")
            continue
        seen[message_id] = message
        records.append(message)
    # Late results retain their ledger position but must not masquerade as
    # active protocol when another input arrived between call and result.
    results = {str(m.get("tool_call_id")): i for i, m in enumerate(records) if m.get("role") == "tool"}
    call_ids = set()
    for index, record in enumerate(records):
        calls = record.get("tool_calls", ())
        if calls and record.get("role") != "assistant":
            raise LlmToolProtocolError("Historical tool calls require an assistant message")
        ids = [str(call.get("id", "")) for call in calls]
        if calls and (not all(ids) or len(set(ids)) != len(ids) or call_ids.intersection(ids)):
            raise LlmToolProtocolError("Invalid or duplicate historical tool call identity")
        call_ids.update(ids)
        positions = [results.get(str(call.get("id"))) for call in calls]
        if not calls or any(p is None or p <= index for p in positions):
            continue
        if set(positions) == set(range(index + 1, index + 1 + len(calls))):
            continue
        record["content"] = context_json({
            "material_only": True,
            "historical_tool_calls": calls,
            "text": record.get("content", ""),
        })
        record.pop("tool_calls", None)
        record.pop("provider_content", None)
        for position in positions:
            result = records[position]
            result["role"] = "assistant"
            result["_historical_result"] = True
            result.pop("tool_call_id", None)
            result.pop("name", None)
    return records
