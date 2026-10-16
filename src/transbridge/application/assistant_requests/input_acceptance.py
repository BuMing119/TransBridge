"""Admit user text and its immutable material snapshot in one Session transaction."""

from copy import deepcopy
from uuid import uuid4

from transbridge.persistence.v2.ids import SessionId, SessionRef

from .attachments import encode_user_content
from .journal import EventCause
from .models import RequestError, digest
from .skill_context import snapshot_skill_context


def accept_input(service, context, text, *, selection, command_id=None, attachments=None, skill_context=None):
    command_id = command_id or uuid4().hex
    skill = snapshot_skill_context(skill_context)
    content = encode_user_content(text, attachments)
    identity = {"text": text, "context": context.to_dict(), "selection": selection}
    if attachments is not None:
        identity["attachment_content"] = content
    if skill is not None:
        identity["skill_context"] = skill
    content_hash = digest(identity)
    accepted = {}

    def apply(state):
        if state.get("session_tombstone"):
            raise RequestError("SESSION_DELETING", "会话正在删除，不能接纳新输入。")
        ingress = state.setdefault("ingress", [])
        old = next((value for value in ingress if value["message_id"] == command_id), None)
        if old:
            if old["digest"] != content_hash:
                raise RequestError("COMMAND_PAYLOAD_CONFLICT", "input identity reused")
            accepted.update(old)
            return
        item = {
            "message_id": command_id,
            "text": text,
            "sequence": len(ingress) + 1,
            "context": context.to_dict(),
            "selection": deepcopy(selection),
            "digest": content_hash,
            "status": "pending",
            "batch_id": "",
            **({"skill_context": deepcopy(skill)} if skill is not None else {}),
        }
        ingress.append(item)
        accepted.update(item)

    service.transact(
        context,
        apply,
        append_messages=(
            {
                "role": "user",
                "content": content,
                "message_id": command_id,
                **({"skill_context": deepcopy(skill)} if skill is not None else {}),
            },
        ),
        cause=EventCause("input.accepted", "user", {"message_ids": [command_id]}),
    )
    # Full materials live once in the transcript, never duplicated into routing state.
    accepted["content"] = content
    return accepted


def input_content(service, context, message_id):
    snapshot = service.lifecycle.read_session(SessionRef(SessionId(context.session_id)), context)
    record = next(
        (m for m in snapshot.backend_messages() if m.get("message_id") == message_id and m.get("role") == "user"),
        None,
    )
    if record is None:
        raise RequestError("INPUT_NOT_FOUND", "输入原文不存在，请重新打开所属会话。")
    return record["content"]
