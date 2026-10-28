"""Local-only recovery of independently retained entry-upload receipts."""

from transbridge.application.sync.entry_upload_results import EntryUploadResultStore

from ._entry_upload_associations import associate_upload_receipts, upload_result_directory, upload_result_owner
from ._project_tool_mutations import ProjectToolTarget
from .base import ToolResult
from .diagnostics import technical_error


def recover_upload_associations(ctx, *, result_id=None, record=None):
    target = ProjectToolTarget.capture(ctx)
    store = EntryUploadResultStore(upload_result_directory(target))
    owner = upload_result_owner(ctx, target)
    record = store.load(result_id, expected_owner=owner) if record is None else record
    if any(record["target"].get(key) != value for key, value in owner.items()):
        raise ValueError("上传成果不属于当前会话、所有者或工程版本。")
    deferred = associate_upload_receipts(target, record)
    errors = []
    for receipt in record["receipts"]:
        try:
            store.save_receipt(record, receipt)
        except Exception as exc:
            errors.append(technical_error(exc))
    uncertain = [row for row in record["receipts"] if row["outcome"] in {"in_flight", "unknown", "unverified"}]
    associated = sum(row.get("association") == "applied" for row in record["receipts"])
    record.update(state="partial" if deferred or uncertain else "completed", deferred=deferred)
    try:
        store.finish(record)
    except Exception as exc:
        errors.append(technical_error(exc))
    complete = not deferred and not uncertain and not errors
    if errors:
        store.retain_pending(record)
    else:
        store.release_pending(record["result_id"])
    return ToolResult(
        success=complete,
        partial=bool(associated and not complete),
        message=f"已恢复 {associated} 条本地上传关联，未发送远端请求。",
        warnings=record.get("association_warnings") or None,
        data={
            "result_id": record["result_id"],
            "associated": associated,
            "deferred_associations": deferred,
            "uncertain_receipts": uncertain,
            "durable": not errors,
            "storage_errors": errors,
            "upload_result": record,
        },
        error_code=None if complete else "UPLOAD_ASSOCIATION_PENDING",
        recovery_action=None if complete else "保留回执并处理关联分歧；结果不明项须先核对远端，勿重复创建。",
    )
