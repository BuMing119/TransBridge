"""Durable evidence and explicit inverse commands for one accepted user input.

Project working-copy commits and Session persistence are separate boundaries.
An interrupted intent remains unresolved; it is never guessed successful or replayed.
"""

from copy import deepcopy
from dataclasses import replace
import json
from uuid import uuid4

from transbridge.application.contracts import DomainError
from transbridge.application.projects.assistant_undo import (
    VariantUndoReceipt,
    apply_variant_undo,
    capture_variant_undo,
    combine_variant_undo,
)

from .journal import EventCause
from .models import UNSETTLED_EFFECTS, RequestError, UserRequest
from .transcript import AttachmentRef
from .undo_capabilities import undo_limitation


def _execution_owner(state, context, execution):
    request = next(
        (UserRequest.from_dict(r) for r in state.get("requests", ()) if r["request_id"] == execution.request_id), None
    )
    if (
        request is None
        or execution.session_id != context.session_id
        or request.session_id != context.session_id
        or request.revision != execution.request_revision
        or request.work_round_id != execution.work_round_id
        or execution.work_round_id not in request.source_message_ids
        or not set(execution.item_ids).issubset({item.item_id for item in request.items})
    ):
        raise RequestError("UNDO_SCOPE_MISMATCH", "撤销效果与请求、会话或真实用户轮次不匹配")
    if any(dict(request.scope).get(key) != getattr(context, key) for key in ("project_id", "variant_id")):
        raise RequestError("UNDO_SCOPE_MISMATCH", "撤销请求的工程版本范围不匹配")
    if not any(
        i["message_id"] == execution.work_round_id and i.get("status") == "applied" for i in state.get("ingress", ())
    ):
        raise RequestError("UNDO_ROUND_SOURCE_MISSING", "本轮必须关联已接纳的真实用户输入")
    return request


def register_in_state(state, context, execution, effect_id, tool_name):
    """Register inside the same Session transaction that prepares the effect."""
    if not execution.work_round_id:
        raise RequestError("UNDO_ROUND_SOURCE_MISSING", "本轮必须关联已接纳的真实用户输入")
    request = _execution_owner(state, context, execution)
    if not effect_id or not tool_name:
        raise RequestError("UNDO_SCOPE_MISMATCH", "撤销效果身份不能为空")
    effect = next((e for e in request.effects if e.effect_id == effect_id), None)
    if effect is not None and effect.execution != execution:
        raise RequestError("UNDO_SCOPE_MISMATCH", "撤销效果不属于此执行")
    rounds = state.setdefault("undo_rounds", {})
    record = rounds.setdefault(execution.work_round_id, {"status": "recording", "effects": {}, "commits": []})
    if record["status"] != "recording":
        raise RequestError("UNDO_ROUND_CLOSED", "已撤销或正在撤销的工作轮次不能继续写入")
    value = {"request_id": execution.request_id, "tool": tool_name}
    old = record["effects"].get(effect_id)
    if old is not None and old != value:
        raise RequestError("COMMAND_PAYLOAD_CONFLICT", "撤销效果身份冲突")
    record["effects"][effect_id] = value


class RoundUndoService:
    def __init__(self, requests, projects, *, config_repository=None):
        self.requests = requests
        self.projects = projects
        self.config_repository = config_repository

    def configuration(self):
        if self.config_repository is None:
            from transbridge.config.repository import default_config_repository

            self.config_repository = default_config_repository()
        return self.config_repository

    def begin_receipt(self, context, execution, effect_id, kind, before):
        reference = self._store(context, before)
        identity = uuid4().hex

        def begin(state):
            request = _execution_owner(state, context, execution)
            if not any(e.effect_id == effect_id and e.execution == execution for e in request.effects):
                raise RequestError("UNDO_SCOPE_MISMATCH", "撤销捕获缺少对应业务效果")
            record = state.get("undo_rounds", {}).get(execution.work_round_id)
            if record is None or effect_id not in record["effects"] or record["status"] != "recording":
                raise RequestError("UNDO_ROUND_CLOSED", "本轮修改没有有效撤销归属")
            record["commits"].append({
                "id": identity,
                "effect_id": effect_id,
                "kind": kind,
                "status": "pending",
                "before": reference.to_dict(),
            })

        self.requests.transact(context, begin, artifact_refs=(reference,))
        return identity

    def _store(self, context, payload):
        if self.requests.transcript_store is None:
            raise RequestError("UNDO_STORAGE_UNAVAILABLE", "撤销凭证存储不可用")
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return self.requests.transcript_store.write_artifact(context.session_id, raw)

    def _read(self, context, reference):
        return json.loads(
            self.requests.transcript_store.read_artifact(context.session_id, AttachmentRef.from_dict(reference))
        )

    def register(self, context, execution, effect_id, tool_name):
        self.requests.transact(
            context, lambda state: register_in_state(state, context, execution, effect_id, tool_name)
        )

    def capture_variant_commit(self, context, execution, effect_id, mutation):
        """Capture a CAS-protected Variant command; persist intent before mutation."""
        if not execution.work_round_id:
            raise RequestError("UNDO_ROUND_SOURCE_MISSING", "本轮必须关联已接纳的真实用户输入")
        active = self.projects.active_variant_snapshot()
        if active is None:
            return mutation()  # The ordinary command reports its missing target.
        before, project_revision = active
        if context.project_id != before.ref.project_id.value or context.variant_id != before.ref.identity.value:
            raise RequestError("UNDO_SCOPE_MISMATCH", "撤销凭证目标与执行范围不一致")
        identity = uuid4().hex
        reference = self._store(context, {"before": before.to_dto().envelope.to_dict()})

        def begin(state):
            request = _execution_owner(state, context, execution)
            if not any(e.effect_id == effect_id and e.execution == execution for e in request.effects):
                raise RequestError("UNDO_SCOPE_MISMATCH", "撤销捕获缺少对应业务效果")
            record = state.get("undo_rounds", {}).get(execution.work_round_id)
            if record is None or effect_id not in record["effects"] or record["status"] != "recording":
                raise RequestError("UNDO_ROUND_CLOSED", "本轮修改没有有效撤销归属")
            record["commits"].append({
                "id": identity,
                "effect_id": effect_id,
                "kind": "variant",
                "status": "pending",
                "before": reference.to_dict(),
            })

        self.requests.transact(context, begin, artifact_refs=(reference,))
        # No retry: mutation may already have committed when a later step fails.
        result = mutation()
        latest = self.projects.active_variant_snapshot()
        if not result.is_success:
            status = "rejected" if latest == active else "unresolved"
            self._finish(context, execution.work_round_id, identity, status)
            return result
        if (
            latest is None
            or latest[0].ref != before.ref
            or latest[1] != project_revision
            or latest[0].revision != result.value.get("revision")
            or latest[0].revision not in (before.revision, before.revision + 1)
            or (latest[0].revision == before.revision and latest[0] != before)
        ):
            self._finish(context, execution.work_round_id, identity, "unresolved")
            return result
        after = latest[0]
        try:
            receipt = capture_variant_undo(before, after)
        except DomainError as exc:
            if exc.code != "ASSISTANT_UNDO_UNSUPPORTED":
                raise
            self._finish(context, execution.work_round_id, identity, "unsupported", detail=str(exc))
            return result
        receipt_ref = self._store(context, receipt.to_dict())
        self._finish(context, execution.work_round_id, identity, "recorded", receipt_ref=receipt_ref)
        return result

    def _finish(self, context, round_id, identity, status, *, detail="", receipt_ref=None):
        def finish(state):
            record = state["undo_rounds"][round_id]
            item = next(c for c in record["commits"] if c["id"] == identity)
            item.update(status=status, detail=detail)
            if receipt_ref is not None:
                item["receipt"] = receipt_ref.to_dict()

        self.requests.transact(context, finish, artifact_refs=() if receipt_ref is None else (receipt_ref,))

    def preview(self, context, round_id):
        state = self.requests.state(context)
        record = state.get("undo_rounds", {}).get(round_id)
        if record is None:
            return {"round_id": round_id, "available": False, "reason": "本轮没有可核验的撤销凭证"}
        result = {"round_id": round_id, "status": record["status"], "available": False, "limitations": []}
        if record["status"] != "recording":
            return {**result, "reason": "本轮撤销已执行或尚待核对，不能重复执行"}
        requests = self.requests.requests(state)
        owners = {e["request_id"] for e in record["effects"].values()}
        if not owners.issubset({r.request_id for r in requests}):
            return {**result, "reason": "本轮所属请求已缺失，不能自动恢复"}
        if any(r.unsettled or (not r.terminal and not r.pause_reasons) for r in requests if r.request_id in owners):
            return {**result, "reason": "请先停止本轮，并等待后台操作完成收尾或核对未知结果"}
        effects = {e.effect_id: e for r in requests for e in r.effects}
        if any(
            eid not in effects
            or effects[eid].status in UNSETTLED_EFFECTS
            or effects[eid].execution.request_id != value["request_id"]
            or effects[eid].execution.work_round_id != round_id
            or effects[eid].execution.session_id != context.session_id
            for eid, value in record["effects"].items()
        ):
            return {**result, "reason": "本轮包含未核对的操作结果"}
        if any(c["status"] in ("pending", "unresolved") for c in record["commits"]):
            return {**result, "reason": "业务提交与撤销凭证尚未完成核对，不能自动恢复"}
        supported = {"variant", "config", "files", "binding"}
        recorded = [c for c in record["commits"] if c["status"] == "recorded" and c.get("kind", "variant") in supported]
        covered = {c["effect_id"] for c in recorded}
        uncovered_commits = {
            c["effect_id"]
            for c in record["commits"]
            if c["status"] == "unsupported" or c.get("kind", "variant") not in supported
        }
        result["limitations"] = [
            {"tool": value["tool"], "reason": undo_limitation(value["tool"]) or "该操作尚无可安全自动执行的逆操作凭证"}
            for eid, value in record["effects"].items()
            if eid not in covered or eid in uncovered_commits or undo_limitation(value["tool"])
        ]
        return {
            **result,
            "available": bool(recorded),
            "reason": "" if recorded else "没有可安全撤销的已记录修改",
            "commit_count": len(recorded),
        }

    def undo(self, context, round_id, *, allow_partial=False):
        with self.requests.serialized(context):
            existing = self.requests.state(context).get("undo_rounds", {}).get(round_id, {})
            if existing.get("status") in {"undone", "partially_undone"}:
                return {
                    "round_id": round_id,
                    "partial": existing["status"] == "partially_undone",
                    "limitations": deepcopy(existing.get("limitations", [])),
                }
            preview = self.preview(context, round_id)
            if not preview["available"]:
                raise RequestError("UNDO_NOT_AVAILABLE", preview["reason"])
            if preview["limitations"] and not allow_partial:
                raise RequestError("UNDO_PARTIAL_CONFIRMATION_REQUIRED", "本轮部分操作不能自动撤销，请先确认恢复范围")
            state = self.requests.state(context)
            record = state["undo_rounds"][round_id]
            receipts = [
                VariantUndoReceipt.from_dict(self._read(context, c["receipt"]))
                for c in record["commits"]
                if c["status"] == "recorded" and c.get("kind", "variant") == "variant"
            ]
            receipt = combine_variant_undo(receipts) if receipts else None
            from .config_undo import apply_config_undo, combine_config_undo, preflight_config_undo

            config_receipts = [
                self._read(context, c["receipt"])
                for c in record["commits"]
                if c["status"] == "recorded" and c.get("kind") == "config"
            ]
            config_receipt = combine_config_undo(config_receipts) if config_receipts else None
            if config_receipt is not None:
                preflight_config_undo(self.configuration(), config_receipt)
            file_commits = [c for c in record["commits"] if c["status"] == "recorded" and c.get("kind") == "files"]
            if file_commits:
                from .file_undo_capture import preflight_file_commits

                preflight_file_commits(self, context, file_commits, backup_root=self.file_backup_root)
            from transbridge.application.projects.assistant_binding_undo import (
                apply_binding_undo,
                combine_binding_undo,
                preflight_binding_undo,
            )

            bindings = [
                self._read(context, c["receipt"])
                for c in record["commits"]
                if c["status"] == "recorded" and c.get("kind") == "binding"
            ]
            binding_receipt = combine_binding_undo(bindings) if bindings else None
            if binding_receipt is not None:
                preflight_binding_undo(self.projects, binding_receipt)
            # Build/preflight before durable intent; the actual application checks CAS again.
            from transbridge.application.projects.assistant_undo import build_variant_inverse

            active = self.projects.active_variant_snapshot()
            if receipt is not None and active is None:
                raise RequestError("UNDO_TARGET_MISSING", "请打开本轮原项目版本")
            if receipt is not None and (
                context.project_id != active[0].ref.project_id.value
                or context.variant_id != active[0].ref.identity.value
            ):
                raise RequestError("UNDO_SCOPE_MISMATCH", "撤销目标与会话的工程版本范围不匹配")
            undo_id = uuid4().hex
            undo_context = replace(context, run_id=undo_id)
            if receipt is not None:
                build_variant_inverse(active[0], receipt, run_id=undo_id)

            def begin(state):
                sequence = (
                    max((record.get("undo_sequence", 0) for record in state["undo_rounds"].values()), default=0) + 1
                )
                state["undo_rounds"][round_id].update(status="undoing", undo_id=undo_id, undo_sequence=sequence)

            self.requests.transact(
                context,
                begin,
                cause=EventCause(
                    "round.undo_started",
                    "user",
                    references={"round_id": round_id},
                    record_unchanged=True,
                ),
            )
            if receipt is not None:
                self._progress(context, round_id, "variant", "applying")
                result = apply_variant_undo(self.projects, receipt, undo_context)
                if not result.is_success:
                    raise RequestError("UNDO_APPLY_FAILED", "; ".join(d.message for d in result.diagnostics))
                # Use the normal save boundary. Any failure leaves an explicit unresolved
                # intent; never repeat the inverse based only on visible field equality.
                saved = self.projects.save_active(undo_context)
                if not saved.is_success:
                    raise RequestError("UNDO_SAVE_FAILED", "; ".join(d.message for d in saved.diagnostics))
                self._progress(context, round_id, "variant", "applied")
            if config_receipt is not None:
                self._progress(context, round_id, "config", "applying")
                apply_config_undo(self.configuration(), config_receipt)
                self._progress(context, round_id, "config", "applied")
            if binding_receipt is not None:
                self._progress(context, round_id, "binding", "applying")
                binding_result = apply_binding_undo(self.projects, binding_receipt, undo_context)
                if not binding_result.is_success:
                    raise RequestError("UNDO_BINDING_FAILED", "; ".join(d.message for d in binding_result.diagnostics))
                self._progress(context, round_id, "binding", "applied")
            if file_commits:
                from .file_undo_capture import apply_file_commits

                self._progress(context, round_id, "files", "applying")
                file_result = apply_file_commits(self, context, file_commits, backup_root=self.file_backup_root)

                def save_files(state):
                    state["undo_rounds"][round_id]["file_result"] = file_result

                self.requests.transact(context, save_files)
                if file_result["status"] != "completed":
                    raise RequestError("UNDO_FILES_INCOMPLETE", "文件恢复未全部完成，请核对已恢复与剩余文件")
                self._progress(context, round_id, "files", "applied")

            def finish(state):
                state["undo_rounds"][round_id].update(
                    status="partially_undone" if preview["limitations"] else "undone",
                    limitations=deepcopy(preview["limitations"]),
                )

            self.requests.transact(
                context,
                finish,
                cause=EventCause(
                    "round.undone",
                    "user",
                    references={"round_id": round_id},
                    record_unchanged=True,
                ),
                append_messages=(
                    {
                        "message_id": "round-undo:" + undo_id,
                        "role": "assistant",
                        "content": json.dumps(
                            {
                                "kind": "round_undo_receipt",
                                "material_only": True,
                                "round_id": round_id,
                                "request_ids": sorted({e["request_id"] for e in record["effects"].values()}),
                                "status": "partially_undone" if preview["limitations"] else "undone",
                                "limitations": preview["limitations"],
                                "instruction": (
                                    "This is a later application fact. Earlier successful tool results "
                                    "remain historical; do not replay them."
                                ),
                            },
                            ensure_ascii=False,
                        ),
                    },
                ),
            )
            self.requests.notify(context.session_id)
            return {
                "round_id": round_id,
                "partial": bool(preview["limitations"]),
                "limitations": preview["limitations"],
            }

    def _progress(self, context, round_id, kind, status):
        def change(state):
            state["undo_rounds"][round_id].setdefault("progress", {})[kind] = status

        self.requests.transact(context, change)
