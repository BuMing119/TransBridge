"""Execute captured uploads and retain receipts before scoped local association."""

from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import json
import logging

from transbridge.application.io.identity import EntryKey
from transbridge.application.io.paratranz_context_order import context_orders, format_ordered_context
from transbridge.application.ports.paratranz import ExternalServiceCategory, ExternalServiceError, ParaTranzEntry
from transbridge.application.sync.entry_upload_results import EntryUploadResultStore
from transbridge.application.tasks import TaskCancelled

from ._entry_upload_associations import associate_upload_receipts, upload_result_directory, upload_result_owner
from ._project_tool_mutations import ProjectToolTarget
from .base import ToolResult
from .diagnostics import technical_error
from .input_prerequisites import source_input_failure

logger = logging.getLogger(__name__)
_REJECTED = frozenset({
    ExternalServiceCategory.AUTHENTICATION,
    ExternalServiceCategory.AUTHORIZATION,
    ExternalServiceCategory.NOT_FOUND,
    ExternalServiceCategory.CONFLICT,
    ExternalServiceCategory.RATE_LIMITED,
})


def upload_entries(
    args,
    ctx,
    collection,
    client,
    project_id,
    cancellation,
    *,
    captured_target=None,
    captured_config=None,
    captured_user=None,
):
    target = captured_target or ProjectToolTarget.capture(ctx)
    source_entries = tuple(collection)
    orders = {
        entry.identity: order for entry, order in zip(source_entries, context_orders(source_entries), strict=True)
    }
    requested = args.get("entry_ids")
    entries = [collection.get(key) for key in requested] if requested is not None else list(collection)
    if any(entry is None for entry in entries):
        return ToolResult.fail("找不到指定条目，未开始上传。", error_code="ENTRY_NOT_FOUND")
    if len({entry.identity for entry in entries}) != len(entries):
        return ToolResult.fail("上传条目重复，未开始上传。", error_code="DUPLICATE_ENTRY")
    total = len(entries)
    skipped = sum(entry.requires_original_match for entry in entries)
    warnings = []
    if skipped:
        warning = f"ParaTranz 无法区分相同 key 的原文，已跳过 {skipped} 条需要原文匹配的词条。"
        logger.warning("PARATRANZ_ORIGINAL_MATCH_SKIPPED: project=%s skipped=%d", project_id, skipped)
        warnings.append(warning)
    entries = tuple(deepcopy(entry) for entry in entries if not entry.requires_original_match)
    missing = source_input_failure(ctx, entries)
    if missing is not None:
        return missing
    overwrite = bool(args.get("force_overwrite", False))
    scope = f"project:{project_id}"
    inputs = [_input(entry, orders[entry.identity], scope, overwrite) for entry in entries]
    if cancellation is not None:
        cancellation.raise_if_cancelled()
    store = EntryUploadResultStore(upload_result_directory(target))
    config = captured_config if captured_config is not None else deepcopy(getattr(ctx, "config", None))
    user = captured_user if captured_config is not None else deepcopy(getattr(ctx, "current_user", None))
    remote_target = {
        **upload_result_owner(ctx, target),
        "source_key": next((key for key, slot in target.app.slots.items() if slot.collection is collection), None),
        "project_id": project_id,
        "scope": scope,
        "endpoint": getattr(config, "base_url", "https://paratranz.cn/api"),
        "account_user_id": user.get("id") if isinstance(user, dict) else getattr(config, "user_id", None),
        "force_overwrite": overwrite,
        "config": {"timeout": getattr(config, "timeout", None), "revision": getattr(config, "config_revision", None)},
    }
    if remote_target["source_key"] is None:
        return ToolResult.fail("上传来源已移除，未发送远端请求。", error_code="UPLOAD_SOURCE_MISSING")
    blocked = _previous_unresolved(store, remote_target, entries, inputs, overwrite)
    warnings.extend(store.diagnostics)
    if blocked:
        return ToolResult.fail(
            "所选条目存在已确认或结果不明的上传回执，未重复发送。",
            data={"blocked_uploads": blocked},
            error_code="UPLOAD_RECEIPT_REQUIRES_RECOVERY",
            recovery_action="已确认回执请恢复本地关联；已有正确关联时使用 force_overwrite；结果不明回执须先核对远端。",
        )
    record = store.create(remote_target, inputs)
    uploaded = 0
    failed, deferred, storage_errors = [], [], []
    cancelled = False
    not_attempted = 0
    for index, (entry, captured) in enumerate(zip(entries, inputs, strict=True)):
        if cancellation is not None and cancellation.is_cancelled:
            if not uploaded and not record["receipts"]:
                raise TaskCancelled("上传已取消")
            cancelled = True
            not_attempted = len(entries) - index - 1
            failed.append({"key": entry.key, "error": "上传已取消", "remote_id": None, "cancelled": True})
            break
        receipt = {
            "index": index,
            "entry_key": entry.identity.to_dict(),
            "key": entry.key,
            "outcome": "in_flight",
            "remote_id": None,
            "source_digest": _source_digest(captured["base"]),
        }
        record["receipts"].append(receipt)
        if not _save_receipt(store, record, receipt, storage_errors):
            receipt["outcome"] = "not_sent"
            not_attempted = len(entries) - index
            break
        try:
            result = client.upsert_entry(
                project_id, ParaTranzEntry(**captured["payload"]), force_overwrite=overwrite, cancellation=cancellation
            )
        except Exception as exc:
            cancelled = isinstance(exc, TaskCancelled)
            rejected = isinstance(exc, ExternalServiceError) and exc.category in _REJECTED
            receipt.update(outcome="rejected" if rejected else "unknown", error=technical_error(exc), remote_id=None)
            failed.append({"key": entry.key, "error": receipt["error"], "remote_id": None, "uncertain": not rejected})
        else:
            uploaded += 1
            returned_id = getattr(result, "remote_id", None)
            remote_id = returned_id if type(returned_id) in (int, str, bool, type(None)) else None
            receipt.update(outcome="confirmed", remote_id=remote_id, association="pending")
            if (
                type(remote_id) is not int
                or remote_id < 1
                or getattr(result, "key", None) != entry.key
                or getattr(result, "original", None) != entry.original
            ):
                receipt.update(outcome="unverified", error="远端返回的 ID 或条目身份无效；须核对远端，勿重新创建。")
                failed.append({"key": entry.key, "error": receipt["error"], "remote_id": remote_id, "uncertain": True})
        durable = _save_receipt(store, record, receipt, storage_errors)
        if cancelled or receipt["outcome"] in {"unknown", "unverified"} or not durable:
            not_attempted = len(entries) - index - 1
            break
    deferred = associate_upload_receipts(target, record)
    warnings.extend(record.get("association_warnings", ()))
    for receipt in record["receipts"]:
        if receipt["outcome"] == "confirmed":
            _save_receipt(store, record, receipt, storage_errors)
    complete = not failed and not deferred and not storage_errors and uploaded == len(entries)
    record.update(state="completed" if complete else "cancelled" if cancelled else "partial", deferred=deferred)
    try:
        store.finish(record)
    except Exception as exc:
        storage_errors.append(technical_error(exc))
        complete = False
    if storage_errors:
        store.retain_pending(record)
        warnings.append("上传回执未能完整写入磁盘；本次结果仍含完整内存回执，请立即保存，勿重复创建。")
    return ToolResult(
        success=complete,
        partial=bool(skipped or (uploaded and not complete)),
        message=f"已上传 {uploaded}/{total} 条"
        + (f"，跳过 {skipped} 条需原文匹配的词条" if skipped else "")
        + ("，其余上传已取消" if cancelled else f"，失败 {len(failed)} 条" if failed else "")
        + (f"，{len(deferred)} 条本地关联待处理" if deferred else ""),
        data={
            "uploaded": uploaded,
            "total": total,
            "skipped": skipped,
            "failed_items": failed,
            "cancelled": cancelled,
            "not_attempted": not_attempted,
            "deferred_associations": deferred,
            "result_id": record["result_id"],
            "result_path": str(store.directory(record["result_id"])),
            "durable": not storage_errors,
            "storage_errors": storage_errors,
            "upload_result": record,
        },
        failed_items=failed or None,
        warnings=warnings or None,
        error_code=None if complete else "UPLOAD_CANCELLED" if cancelled else "UPLOAD_INCOMPLETE",
        recovery_action=None
        if complete
        else "保留上传回执；已确认条目仅重试本地关联，结果不明项先核对远端，勿直接重新创建。",
    )


def _input(entry, order, scope, overwrite):
    references = [ref for ref in entry.external_refs if (ref.system, ref.scope) == ("paratranz", scope)]
    if len(references) > 1:
        raise ValueError("条目存在重复 ParaTranz 远端引用")
    reference = references[0] if references and overwrite else None
    snapshot = entry.snapshot()
    return {
        "base": {
            **asdict(snapshot),
            "entry_key": snapshot.entry_key.to_dict(),
            "external_refs": [ref.to_dict() for ref in snapshot.external_refs],
            "revision": snapshot.revision.value,
            "provenance": [item.to_dict() for item in snapshot.provenance],
        },
        "payload": asdict(
            ParaTranzEntry(
                reference.opaque_id if reference else None,
                entry.key,
                entry.original,
                entry.translation or "",
                format_ordered_context(entry.context, order) or "",
                entry.stage,
            )
        ),
    }


def _save_receipt(store, record, receipt, errors):
    try:
        store.save_receipt(record, receipt)
        return True
    except Exception as exc:
        errors.append(technical_error(exc))
        return False


def _previous_unresolved(store, target, entries, inputs, overwrite):
    selected = {entry.identity: entry for entry in entries}
    remote_keys = {entry.key for entry in entries}
    source_digests = {entry.identity: _source_digest(row["base"]) for entry, row in zip(entries, inputs, strict=True)}
    blocked = []
    for record in store.list_records(target):
        for receipt in record["receipts"]:
            key = EntryKey.from_dict(receipt["entry_key"])
            if receipt["outcome"] in {"in_flight", "unknown", "unverified"} and key.local_key in remote_keys:
                blocked.append({"key": key.local_key, "outcome": "requires_remote_verification"})
                continue
            if key not in selected or receipt["outcome"] not in {"in_flight", "unknown", "unverified", "confirmed"}:
                continue
            refs = selected[key].external_refs
            associated = any(
                (ref.system, ref.scope, ref.opaque_id) == ("paratranz", target["scope"], receipt.get("remote_id"))
                for ref in refs
            )
            if receipt["outcome"] == "confirmed" and associated and overwrite:
                continue
            if receipt["outcome"] == "confirmed" and receipt.get("source_digest") != source_digests[key]:
                continue
            visible = all(record["target"].get(field) == target.get(field) for field in ("owner_id", "session_id"))
            blocked.append({
                "key": key.local_key,
                **(
                    {
                        "result_id": record["result_id"],
                        "outcome": receipt["outcome"],
                        "remote_id": receipt.get("remote_id"),
                    }
                    if visible
                    else {"outcome": "requires_remote_verification"}
                ),
            })
    return blocked


def _source_digest(base):
    payload = {key: base[key] for key in ("entry_key", "original", "context", "metadata", "string_id")}
    return sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
