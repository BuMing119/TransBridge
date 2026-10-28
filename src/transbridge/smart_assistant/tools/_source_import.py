"""Source commands and projection publication for Assistant imports."""

from __future__ import annotations

from threading import RLock

from transbridge.application.io import default_format_catalog
from transbridge.application.projects.prepared_source_results import (
    decode_prepared_source,
    encode_prepared_source,
    encode_source_snapshot,
    prepare_parsed_source,
)
from transbridge.persistence.v2.ids import ProjectId, VariantId, VariantRef

from .base import ToolResult

_RECEIPT_LOCK = RLock()
_SOURCE_RECEIPTS = {}


def _receipt_key(store, record):
    return str(store.root.resolve()), record["task_id"]


def retry_source_receipt(store, record):
    """A command success is evidence; a prior attempt cannot prove publication."""
    key = _receipt_key(store, record)
    with _RECEIPT_LOCK:
        pending = _SOURCE_RECEIPTS.get(key)
    if pending is None:
        return ""
    record.update(pending)
    try:
        store.update_metadata(record["task_id"], **pending)
    except (OSError, ValueError) as exc:
        return f"来源命令回执待重试：{exc}"
    with _RECEIPT_LOCK:
        _SOURCE_RECEIPTS.pop(key, None)
    return ""


def _retain_source_receipt(store, record, **fields):
    with _RECEIPT_LOCK:
        _SOURCE_RECEIPTS[_receipt_key(store, record)] = fields
    return retry_source_receipt(store, record)


def publish_import(target, path, label, collection, snapshot, format_id, action, options=(), *, task):
    if action == "append":
        if snapshot is not None:
            task.record["input_snapshot"] = encode_source_snapshot(snapshot)
        task.candidates(tuple(collection))
        result = task.finish()
        result.data.update(
            action="append",
            added_count=result.data.get("merge", {}).get("applied_count", 0),
            total_count=len(target.app.collection or ()),
            target_label=label,
        )
        return result
    if snapshot is None or default_format_catalog().adapter(format_id) is None:
        raise ValueError("该格式尚无正式来源适配器；内部 JSON 可用 append 更新同一 EntryKey，不能新增未登记来源。")
    prepared = prepare_parsed_source(path, tuple(collection), snapshot, format_id, options)
    task.record.update(
        prepared_source=encode_prepared_source(prepared, tuple(collection)),
        entries=[entry.to_dict() for entry in collection],
        label=label,
    )
    try:
        task.persist(state="partial" if task.record.get("parse_outcome") == "partial" else "completed")
        summary, warning = merge_source_result(task.ctx, task.store, task.record)
    except Exception as exc:
        return ToolResult(
            success=False,
            partial=True,
            message=f"解析成果已保留，登记待重试：{exc}",
            data={"result_id": task.result_id},
            recovery_action="使用 get_task_result 查询，merge_task_result 重试；无需重新解析文件。",
        )
    return ToolResult(
        success=True,
        partial=task.record.get("parse_outcome") == "partial",
        message=f"已创建并激活集合「{label}」",
        data={
            "result_id": task.result_id,
            "action": action,
            "label": label,
            "entry_count": len(collection),
            "activated": summary["activated"],
            "parse_outcome": task.record.get("parse_outcome"),
            "parse_diagnostics": task.record.get("parse_diagnostics", []),
        },
        warnings=[warning] if warning else None,
    )


def merge_source_result(ctx, store, record):
    import os

    from transbridge.application.projects.source_registry import migrate_legacy_source_registry

    from ._project_tool_mutations import ProjectToolTarget
    from .translation_task_results import _authorized, task_ports

    app, commands, context, identity, _root = task_ports(ctx)
    _authorized(record, context)
    retry_source_receipt(store, record)
    if identity != (record["project_id"], record["variant_id"]):
        raise ValueError("来源成果对应的工程版本已切换，请切回原版本再登记。")
    target = ProjectToolTarget.capture(ctx)
    prepared = decode_prepared_source(record["prepared_source"])
    path = dict(prepared.descriptor)["location"]
    expected_registration = migrate_legacy_source_registry(identity[0], (prepared.to_dict(),)).sources[0].to_dict()

    def publish():
        cancellation = getattr(ctx, "cancellation_token", None)
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        if app.active_version_identity != identity:
            raise ValueError("活动工程版本已切换，解析成果已保留。")
        existing = [
            source
            for source in app.project_sources
            if os.path.normcase(str(source.get("location", source.get("path", "")))) == os.path.normcase(path)
        ]
        if record.get("registration_state") == "committed" or record.get("registered"):
            if len(existing) != 1 or any(
                existing[0].get(key) != expected_registration.get(key)
                for key in ("source_id", "fingerprint", "format_id")
            ):
                raise ValueError("该工程来源已经存在，或其身份/内容与解析成果不同。")
            record["registered"] = True
            return {"activated": app.active_key == path, "registered": True}, ""
        if record.get("registration_state") == "attempting" or (
            record.get("registration_state") is None and record.get("registration_attempted")
        ):
            raise ValueError("此前来源登记结果不确定；解析成果已保留，不能自动重放新增或认领同路径来源。")
        if existing:
            raise ValueError("该工程来源已经存在；不能将其他登记认作本次命令的成功回执。")
        store.update_metadata(record["task_id"], registration_state="attempting")
        record["registration_state"] = "attempting"
        result = commands.add_prepared_source(
            prepared,
            context,
            expected_variant_ref=VariantRef(VariantId(identity[1]), ProjectId(identity[0])),
        )
        if not result.is_success:
            _retain_source_receipt(store, record, registration_state="failed")
            raise ValueError("；".join(item.message for item in result.diagnostics))
        receipt_warning = _retain_source_receipt(store, record, registered=True, registration_state="committed")
        from transbridge.ui.source_hydration import slot_from_hydration

        try:
            app.add_slot(path, slot_from_hydration(result.value.hydration))
            app.activate_slot(path)
            target.acknowledge_projection()
        except Exception as exc:
            return {"activated": False, "registered": True}, f"来源已登记，视图待重新加载：{exc}"
        return {"activated": True, "registered": True}, receipt_warning

    summary, warning = target.dispatch(publish)
    try:
        store.update_metadata(record["task_id"], registered=True, registration_state="committed", merge=summary)
    except (OSError, ValueError) as exc:
        warning = f"来源已登记，但成果回执待重试：{exc}"
    return summary, warning
