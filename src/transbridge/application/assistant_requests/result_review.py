"""Read-only user review of attributed receipts, never reconstructed live state."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from transbridge.application.projects.assistant_undo import VariantUndoReceipt

from .models import RequestError
from .task_diagnostics import recorded_task_diagnostics
from .transcript import AttachmentRef


@dataclass(frozen=True, slots=True)
class RecordedChange:
    entry_id: str
    original: str | None
    before: str
    after: str
    before_stage: int
    after_stage: int
    tool: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class RecordedOutcome:
    tool: str
    status: str
    message: str
    entry_id: str = ""


@dataclass(frozen=True, slots=True)
class RequestReview:
    request_id: str
    goal: str
    scope: tuple[tuple[str, str], ...]
    changes: tuple[RecordedChange, ...] = ()
    outcomes: tuple[RecordedOutcome, ...] = ()
    exported_files: tuple[str, ...] = ()
    notices: tuple[str, ...] = ()
    undo_available: bool = False
    undo_summary: str = ""

    @property
    def entry_ids(self):
        return tuple(dict.fromkeys(row.entry_id for row in self.changes))

    @property
    def failures(self):
        return tuple(row for row in self.outcomes if row.status in {"failed", "partial", "outcome_unknown"})


def _read_artifact(service, context, reference):
    if service.transcript_store is None:
        raise ValueError("会话结果凭据存储不可用")
    raw = service.transcript_store.read_artifact(context.session_id, AttachmentRef.from_dict(reference))
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("结果凭据不是有效对象")
    return value


def _variant_changes(payload, scope, tool):
    receipt = VariantUndoReceipt.from_dict(payload)
    if receipt.before.ref.project_id.value != scope.get("project_id") or receipt.before.ref.identity.value != scope.get(
        "variant_id"
    ):
        raise ValueError("修改凭据的工程版本与原任务不一致")
    rows = []
    for before, after in zip(receipt.before.entries, receipt.after.entries, strict=True):
        detail = "标签已修改" if before.labels != after.labels else ""
        if before.external_refs != after.external_refs:
            detail = "；".join(filter(None, (detail, "外部关联已修改")))
        rows.append(
            RecordedChange(
                before.entry_key.serialize(),
                before.entry_key.original,
                before.translation,
                after.translation,
                before.stage.value,
                after.stage.value,
                tool,
                detail,
            )
        )
    return rows


def _exported_files(payload, commit):
    # Only the application's captured authorized targets can create an Open action.
    trusted = commit.get("targets", [])
    files = payload.get("files", [])
    if not isinstance(files, list) or [row.get("path") for row in files] != trusted:
        raise ValueError("导出凭据与已授权文件范围不一致")
    return [
        str(path)
        for row in files
        if isinstance(row, dict)
        and row.get("after", {}).get("exists") is True
        and (path := Path(row["path"])).is_absolute()
        and ".." not in path.parts
    ]


def _outcomes(effect, tool):
    payload = {}
    if effect.receipt.startswith("{"):
        try:
            payload = json.loads(effect.receipt)
        except (ValueError, TypeError):
            return [RecordedOutcome(tool, "outcome_unknown", "结果凭据无法读取，请核对执行过程。")]
    if not isinstance(payload, dict):
        payload = {}
    result = payload.get("result", {})
    if not isinstance(result, dict):
        result = {}
    tool = str(payload.get("tool") or tool)
    status = str(effect.status)
    if status == "succeeded" and result.get("partial"):
        status = "partial"
    progress = payload.get("progress", {})
    parts = []
    if isinstance(progress, dict):
        reason = progress.get("error") or progress.get("message")
        if isinstance(reason, str) and reason:
            parts.append(reason)
        for key, label in (("success_count", "成功"), ("failed_count", "失败"), ("skipped_count", "跳过")):
            if isinstance(progress.get(key), int):
                parts.append(f"{label} {progress[key]} 条")
        if status == "succeeded" and (progress.get("partial") or progress.get("failed_count")):
            status = "partial"
    message = str(result.get("message") or "；".join(parts) or "仅保存了操作状态，没有词条级结果明细。")
    rows = [RecordedOutcome(tool, status, message)]
    for item in result.get("failed_items") or ():
        if isinstance(item, dict):
            rows.append(
                RecordedOutcome(
                    tool, "failed", str(item.get("reason") or "未保存失败原因"), str(item.get("entry_id") or "")
                )
            )
    return rows


def read_request_review(service, context, request_id) -> RequestReview:
    """Read persisted evidence on a worker; no live collection or task history needed."""
    state = service.state(context)
    request = next((r for r in service.requests(state) if r.request_id == request_id), None)
    if request is None or request.session_id != context.session_id:
        raise RequestError("REVIEW_SCOPE_MISMATCH", "原任务已不存在或不属于当前会话")
    scope = dict(request.scope)
    effects = {
        effect.effect_id: effect
        for effect in request.effects
        if effect.execution.request_id == request_id and effect.execution.session_id == context.session_id
    }
    changes, outcomes, files, notices = [], [], [], []
    tools = {}
    for round_id, record in state.get("undo_rounds", {}).items():
        owned = {
            identity: value
            for identity, value in record.get("effects", {}).items()
            if value.get("request_id") == request_id
            and identity in effects
            and effects[identity].execution.work_round_id == round_id
        }
        tools.update({identity: value.get("tool", "") for identity, value in owned.items()})
        for commit in record.get("commits", ()):
            identity = commit.get("effect_id")
            if identity not in owned:
                continue
            if commit.get("status") != "recorded":
                if commit.get("status") != "rejected":
                    notices.append("有操作尚未形成完整修改凭据；不会把当前译文当作其执行结果。")
                continue
            try:
                payload = _read_artifact(service, context, commit["receipt"])
                if commit.get("kind", "variant") == "variant":
                    changes.extend(_variant_changes(payload, scope, tools[identity]))
                elif commit.get("kind") == "files":
                    files.extend(_exported_files(payload, commit))
                else:
                    notices.append("此任务还包含设置或项目绑定修改；词条表仅展示已保存的词条变更。")
            except (ValueError, KeyError, TypeError, OSError) as exc:
                notices.append(f"一项修改凭据无法展示：{exc}")
        if owned and record.get("status") in {"undone", "partially_undone"}:
            notices.append("该输入的修改已执行撤销；下表保留当时的修改记录，不表示当前译文。")
    for identity, effect in effects.items():
        outcomes.extend(_outcomes(effect, tools.get(identity, "操作")))
    for dispatch in request.dispatches:
        for receipt in dispatch.step_receipts:
            if receipt.status == "completed":
                continue
            index = int(receipt.step_id) if receipt.step_id.isdecimal() else -1
            tool = dispatch.step_specs[index][0] if 0 <= index < len(dispatch.step_specs) else "操作"
            outcomes.append(
                RecordedOutcome(tool, receipt.status, receipt.error_code or "操作未完成，未保存更详细原因。")
            )
    for diagnostic, status in recorded_task_diagnostics(request):
        row = RecordedOutcome(str(diagnostic.get("tool") or "操作"), status, str(diagnostic["message"]))
        if not any(existing.status == row.status and existing.message == row.message for existing in outcomes):
            outcomes.append(row)
    if not changes:
        notices.append("没有可展示的词条修改前后凭据；已有结果说明见下方操作记录。")
    undo_available, undo_summary = False, "没有可核验的撤销范围。"
    if request.work_round_id and service.undo is not None:
        preview = service.undo.preview(context, request.work_round_id)
        undo_available = bool(preview.get("available"))
        undo_summary = describe_undo_scope(service, context, request, preview, state=state)
    return RequestReview(
        request_id,
        request.goal,
        request.scope,
        tuple(changes),
        tuple(outcomes),
        tuple(dict.fromkeys(files)),
        tuple(dict.fromkeys(notices)),
        undo_available,
        undo_summary,
    )


def describe_undo_scope(service, context, request, preview, *, state=None):
    """Describe the actual user-input undo boundary, including other owned tasks."""
    if not preview.get("available"):
        return str(preview.get("reason") or "没有可安全撤销的修改。")
    state = service.state(context) if state is None else state
    record = state.get("undo_rounds", {}).get(request.work_round_id, {})
    owners = {value["request_id"] for value in record.get("effects", {}).values()}
    count = preview.get("commit_count")
    amount = f"{count} 次已记录修改" if isinstance(count, int) else "已记录修改"
    entries, files = set(), set()
    complete = True
    for commit in record.get("commits", ()):
        if commit.get("status") != "recorded":
            continue
        try:
            payload = _read_artifact(service, context, commit["receipt"])
            if commit.get("kind", "variant") == "variant":
                receipt = VariantUndoReceipt.from_dict(payload)
                entries.update(entry.entry_key.serialize() for entry in receipt.before.entries)
            elif commit.get("kind") == "files":
                # Undo can restore a deleted file too, so count all bound targets.
                _exported_files(payload, commit)
                files.update(commit["targets"])
        except (ValueError, KeyError, TypeError, OSError):
            complete = False
    details = []
    if entries:
        details.append(f"{len(entries)} 条词条")
    if files:
        details.append(f"{len(files)} 个文件")
    if complete and details:
        amount += "（涉及" + "、".join(details) + "）"
    elif not complete:
        amount += "（部分细目无法读取，执行前需重新核验凭据）"
    text = f"撤销“{request.goal}”对应这次输入产生的{amount}"
    if len(owners) > 1:
        text += f"，涉及 {len(owners)} 项任务"
    text += "。执行前会再次核验，之后手动改过的内容不会被强行覆盖。"
    if preview.get("limitations"):
        text += "部分操作不能自动恢复，撤销前会列明。"
    return text
