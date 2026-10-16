"""Explicit recovery of one unresolved routing directive, retaining accepted siblings."""

from copy import deepcopy
from dataclasses import replace
import json
from uuid import uuid4

from .attachments import attachment_snapshot, encode_user_content
from .journal import EventCause
from .models import RequestError, digest
from .routing import RoutingBatch
from .skill_context import snapshot_skill_context


def resolution_kind(batch, receipt):
    """Read old all-purpose clarification receipts without rewriting their audit."""
    if receipt.status not in {"needs_clarification", "needs_replan"}:
        return ""
    directive = (
        next((d for d in batch.proposal.directives if d["local_id"] == receipt.local_id), None)
        if batch.proposal
        else None
    )
    diagnostic = receipt.diagnostic
    try:
        audit = json.loads(diagnostic)
        if isinstance(audit, dict):
            diagnostic = audit.get("diagnostic", "") or audit.get("previous_diagnostic", "")
    except (ValueError, TypeError):
        pass  # Original receipts contain plain diagnostic text, not JSON.
    if (
        receipt.status == "needs_clarification"
        and directive is not None
        and directive["action"] != "CREATE"
        and str(diagnostic).startswith("REQUEST_TARGET_AMBIGUOUS:")
    ):
        return "target"
    return "replan"


def unresolved_directives(state):
    for saved in state.get("batches", ()):
        if saved.get("status") == "discarded":
            continue
        batch = RoutingBatch.from_dict(saved["batch"])
        for receipt in batch.receipts:
            kind = resolution_kind(batch, receipt)
            if kind:
                yield batch, receipt, kind


def directive_source(batch, local_id):
    directive = (
        next((d for d in batch.proposal.directives if d["local_id"] == local_id), None) if batch.proposal else None
    )
    if directive is None:
        raise RequestError("REQUEST_CLARIFICATION_INVALID", "未找到原始要求，请刷新任务列表。")
    source = next((s for s in batch.sources if s.message_id == directive["message_id"]), None)
    if source is None:
        raise RequestError("REQUEST_CLARIFICATION_INVALID", "原始输入缺失，不能重新规划。")
    start, end = directive["span"]
    return source, source.text[start:end]


def resolve_routing_input(service, context, batch_id, local_id, *, action, command_id=None):
    """Commit an explicit new user input or discard; never replay a saved proposal.

    Replanning copies only the unresolved source span into new ingress. Original
    scope, selection and material stay frozen. Existing receipts remain immutable
    except the selected unresolved receipt; duplicate commands return their result.
    """
    if action not in {"replan", "discard"}:
        raise RequestError("REQUEST_PROTOCOL_INVALID", "未知的输入恢复操作。")
    command_id = command_id or uuid4().hex
    command_digest = digest({"action": action, "batch_id": batch_id, "local_id": local_id})
    messages = []

    def apply(state):
        messages.clear()
        if state.get("session_tombstone"):
            raise RequestError("SESSION_DELETING", "会话正在删除，不能处理输入。")
        commands = state.setdefault("management_commands", {})
        previous = commands.get(command_id)
        if previous:
            if previous["digest"] != command_digest:
                raise RequestError("COMMAND_PAYLOAD_CONFLICT", "恢复命令标识已用于其他操作。")
            return
        if any(i["message_id"] == command_id for i in state.get("ingress", ())):
            raise RequestError("COMMAND_PAYLOAD_CONFLICT", "恢复命令不能复用旧输入标识。")
        saved = next((b for b in state.get("batches", ()) if b["batch"]["batch_id"] == batch_id), None)
        if saved is None or saved.get("status") == "discarded":
            raise RequestError("REQUEST_CLARIFICATION_INVALID", "待处理输入已变化，请刷新。")
        batch = RoutingBatch.from_dict(saved["batch"])
        receipt = next((r for r in batch.receipts if r.local_id == local_id), None)
        if receipt is None or not resolution_kind(batch, receipt):
            raise RequestError("REQUEST_CLARIFICATION_INVALID", "该要求已处理，请刷新；不会重复执行。")
        source, excerpt = directive_source(batch, local_id)
        scope = tuple(
            sorted(
                (k, str(v))
                for k, v in context.to_dict().items()
                if k in {"owner_id", "session_id", "project_id", "variant_id"} and v is not None
            )
        )
        if batch.session_id != context.session_id or source.scope != scope:
            raise RequestError("REQUEST_SCOPE_MISMATCH", "请回到该输入原来的项目和版本再处理。")
        original = next((i for i in state.get("ingress", ()) if i["message_id"] == source.message_id), None)
        if original is None:
            raise RequestError("INPUT_NOT_FOUND", "原始输入与范围快照缺失，请核对后重新发送。")
        text = ("重新规划这条尚未接纳的要求：\n" if action == "replan" else "放弃这条尚未接纳的要求：\n") + excerpt
        content = text
        if action == "replan":
            material = attachment_snapshot(service.input_content(context, source.message_id))
            if material is not None:
                content = encode_user_content(text, material["attachments"])
        skill = snapshot_skill_context(original.get("skill_context")) if action == "replan" else None
        skills = (
            [snapshot_skill_context(value) for value in original.get("skill_contexts", ())]
            if action == "replan"
            else []
        )
        metadata = {**({"skill_context": skill} if skill else {}), **({"skill_contexts": skills} if skills else {})}
        messages.append({
            "message_id": command_id,
            "role": "user",
            "content": content,
            **metadata,
        })
        ingress = state.setdefault("ingress", [])
        ingress.append({
            "message_id": command_id,
            "text": text,
            "sequence": len(ingress) + 1,
            "context": deepcopy(original["context"]),
            "selection": deepcopy(original.get("selection", {})),
            "digest": command_digest,
            "status": "pending" if action == "replan" else "applied",
            "batch_id": "",
            "management_kind": action,
            "original_message_id": source.message_id,
            **metadata,
        })
        replacement = replace(
            receipt,
            status="replanned" if action == "replan" else "discarded",
            diagnostic=json.dumps(
                {"previous_diagnostic": receipt.diagnostic, "resolution_message_id": command_id}, ensure_ascii=False
            ),
        )
        batch = replace(batch, receipts=tuple(replacement if r.local_id == local_id else r for r in batch.receipts))
        saved.update(
            batch=batch.to_dict(),
            status="needs_clarification" if any(resolution_kind(batch, r) for r in batch.receipts) else "applied",
        )
        commands[command_id] = {"kind": action, "digest": command_digest, "request_ids": []}

    with service.serialized(context):
        state = service.transact(
            context,
            apply,
            append_messages=messages,
            cause=EventCause("routing." + action, "user", {"batch_id": batch_id, "message_ids": [command_id]}),
        )
    service.notify(context.session_id)
    return state
