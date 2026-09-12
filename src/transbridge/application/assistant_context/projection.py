"""Append new authorized sources once; never reselect a rolling history window."""

from copy import deepcopy
from dataclasses import replace
import json
from uuid import uuid4

from transbridge.application.assistant_requests.models import digest
from transbridge.smart_assistant.request_context_assembler import RequestContextAssembler, _groups

from .models import ContextEpoch, FrozenContextItem, PreparationWait, encode, state_item

_KEYS = ("role", "content", "tool_calls", "tool_call_id", "name", "provider_content")
_STATUS_KEYS = (
    "success",
    "status",
    "error",
    "errors",
    "code",
    "message",
    "partial",
    "total",
    "count",
    "task_id",
    "job_id",
    "request_id",
    "path",
    "file",
    "result_id",
)


def authorized_records(history, request, owners=None):
    explicit = owners or {}
    sources = set(request.source_message_ids) | {r.source_message_id for r in request.revisions}
    result = []
    for source in history:
        identity = source.get("message_id", "")
        owner = explicit.get(identity)
        allowed = (
            owner == request.request_id
            if owner is not None
            else request.request_id in source.get("request_ids", [source.get("request_id")]) or identity in sources
        )
        if source.get("role") == "system" or allowed:
            result.append(source)
    return RequestContextAssembler._records(result)


def source_digest(record):
    return digest({key: record[key] for key in _KEYS if key in record})


def related_history(history, request, requests, owners, previous=None):
    """Freeze explicit follow-up background without copying a parent's execution authority."""
    if not request.related_to:
        return list(history)
    parent = next((r for r in requests if r.request_id == request.related_to), None)
    if parent is None or parent.session_id != request.session_id or parent.scope != request.scope:
        raise PreparationWait("CONTEXT_SCOPE_CHANGED", "关联请求的历史范围无法核验。")
    prefix = "related:" + parent.request_id + ":"
    known = {key for key, _ in previous.source_digests if key.startswith(prefix)} if previous else set()
    permitted = [
        r
        for r in authorized_records(history, parent, owners)
        if r["role"] in {"user", "assistant"} and r.get("content")
    ]
    if previous:
        permitted = [r for r in permitted if prefix + r["message_id"] in known]
        if {prefix + r["message_id"] for r in permitted} != known:
            raise PreparationWait("CONTEXT_SCOPE_CHANGED", "关联请求的已引用历史已失去访问资格。")
    else:
        # Include original intent and the latest public explanations. All omissions remain explicit references.
        selected = set(parent.source_message_ids) | {r["message_id"] for r in permitted[-3:]}
        permitted = [r for r in permitted if r["message_id"] in selected]
    background = [
        {
            "role": "assistant",
            "message_id": prefix + r["message_id"],
            "request_ids": [request.request_id],
            "content": encode({
                "material_only": True,
                "kind": "related_request_history",
                "source_request_id": parent.request_id,
                "source_message_id": r["message_id"],
                "authority": "Historical context only; no permissions or task state are inherited.",
                "content": r.get("content", ""),
            }),
        }
        for r in permitted
    ]
    return background + list(history)


def project_result(record, *, threshold=6000):
    from transbridge.smart_assistant.request_protocol import CONTROL_TOOLS

    message = {key: deepcopy(record[key]) for key in _KEYS if key in record}
    content = message.get("content", "")
    if message.get("role") != "tool" or len(str(content)) <= threshold:
        return message
    # Control receipts are protocol, never blindly truncate them.
    if message.get("name") in CONTROL_TOOLS:
        return message
    text = content if isinstance(content, str) else encode(content)
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    facts = {key: data[key] for key in _STATUS_KEYS if key in data} if isinstance(data, dict) else {}
    message["content"] = encode({
        "material_only": True,
        "kind": "tool_result_reference",
        "facts": facts,
        "format": "json" if data is not None else "text",
        "total_characters": len(text),
        "reference": {
            "message_id": record["message_id"],
            "digest": digest(text),
            "tool": "read_request_result",
            "offset": 0,
            "limit": 2000,
        },
    })
    return message


def append_context(
    history,
    request,
    required_state,
    *,
    config_digest,
    previous=None,
    owners=None,
    summaries=(),
    verified_immutable=False,
):
    records = authorized_records(history, request, owners)
    # ConversationManager replaces its sole system message. Old revisions remain in the
    # factual transcript, but only the newest rule snapshot belongs to the active prompt.
    systems = [project_result(r) for r in records if r["role"] == "system"][-1:]
    body = [r for r in records if r["role"] != "system"]
    groups = _groups(body)
    previous_sources = dict(previous.source_digests) if previous else {}
    # Only the canonical artifact reader may reuse hashes: it has already verified the complete
    # immutable transcript bytes. Detached callers must still detect same-ID content changes.
    all_sources = {
        r["message_id"]: (
            previous_sources[r["message_id"]]
            if verified_immutable and r["message_id"] in previous_sources
            else source_digest(r)
        )
        for r in body
    }
    if any(all_sources.get(key) != value for key, value in previous_sources.items()):
        raise PreparationWait("CONTEXT_SOURCE_CHANGED", "原历史的内容或归属已变化，请检查上下文范围。")
    if previous is None:
        previous = ContextEpoch(
            request.session_id,
            request.request_id,
            request.scope,
            uuid4().hex,
            config_digest,
            encode(systems),
            summaries=tuple(summaries),
        )
    elif previous.config_digest != config_digest or previous.systems_json != encode(systems):
        # A new provider/config version keeps every summary, rebuilding only raw protocol projections.
        covered = {source for segment in previous.summaries for source in segment.covered_sources}
        previous = replace(
            previous,
            epoch_id=uuid4().hex,
            config_digest=config_digest,
            systems_json=encode(systems),
            items=(),
            state_digest="",
            reason="configuration_changed",
        )
        previous_sources = {key: value for key, value in previous_sources.items() if key in covered}
    additions = []
    new_state = digest(required_state)
    if new_state != previous.state_digest:
        additions.append(state_item(required_state))
    for group in groups:
        fresh = [body[index] for index in group if body[index]["message_id"] not in previous_sources]
        if fresh and len(fresh) != len(group):
            raise PreparationWait("CONTEXT_PROTOCOL_CHANGED", "工具调用组不能部分追加。")
        for record in fresh:
            additions.append(
                FrozenContextItem(
                    record["message_id"],
                    all_sources[record["message_id"]],
                    encode(project_result(record)),
                    body[group[0]]["message_id"],
                )
            )
    candidate = replace(
        previous,
        items=previous.items + tuple(additions),
        source_digests=tuple(all_sources.items()),
        state_digest=new_state,
    )
    candidate.validate()
    return candidate
