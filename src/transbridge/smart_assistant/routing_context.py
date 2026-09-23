"""Bounded conversational background and request candidates for input classification."""

import re

from .request_protocol import COVERAGE_TOOL


def _terms(text):
    words = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", text.casefold())
    return {
        part
        for word in words
        for part in ([word] if word.isascii() else [word[i : i + 2] for i in range(len(word) - 1)])
    }


def request_candidates(batch, requests):
    scopes = {source.scope for source in batch.sources}
    available = [r for r in requests if r.session_id == batch.session_id and r.scope in scopes]
    active = [r for r in available if not r.terminal]
    terminal = [r for r in available if r.terminal]
    query = " ".join(s.text for s in batch.sources)
    terms = _terms(query)
    scored = sorted(
        ((len(terms & _terms(r.goal)), index, r) for index, r in enumerate(terminal)),
        key=lambda entry: (entry[0], entry[1]),
        reverse=True,
    )
    selected = {r.request_id for r in terminal[-3:]}
    selected.update(r.request_id for score, _, r in scored[:3] if score)
    selected.update(r.request_id for r in terminal if r.request_id in query)
    candidates = active + [r for r in terminal if r.request_id in selected]
    return candidates, len(available) - len(candidates)


def recent_conversation(batch, history, *, limit=12, character_budget=12000):
    """Historical excerpts are data, never replayed tool calls or new ingress."""
    current = {s.message_id for s in batch.sources}
    selected = []
    for message in reversed(history):
        text = message.get("content", "")
        if (
            message.get("message_id") in current
            or message.get("role") not in {"user", "assistant"}
            or any(call.get("name") != COVERAGE_TOOL for call in message.get("tool_calls", ()))
            or not isinstance(text, str)
            or not text.strip()
            or text.startswith(("[Tool result - ", "【工具执行结果", "[Plan execution completed]"))
        ):
            continue
        size = min(len(text), 2000, character_budget)
        selected.append({
            "message_id": message.get("message_id"),
            "role": message["role"],
            "text": text[:size],
            "truncated": size < len(text),
        })
        character_budget -= size
        if len(selected) >= limit or character_budget <= 0:
            break
    return list(reversed(selected))
