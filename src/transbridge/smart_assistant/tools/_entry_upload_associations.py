"""Associate confirmed remote receipts without replacing newer local content."""

from dataclasses import replace
import json

from transbridge.application.io.identity import EntryKey, ExternalEntryRef
from transbridge.application.projects.variant_commands import EntryStatePatch
from transbridge.persistence.v2.ids import ProjectId, VariantId, VariantRef

from .undo_capture import capture_variant_command


def upload_result_owner(context, target):
    request = getattr(context, "request_context", None) or target.app.runtime_context
    return {
        "owner_id": getattr(context, "owner_id", None) or request.owner_id,
        "session_id": request.session_id,
        "project_identity": list(target.identity) if target.identity is not None else None,
    }


def upload_result_directory(target):
    if target.identity is None:
        raise ValueError("请先打开上传对应的工程版本。")
    request = replace(target.app.runtime_context, project_id=target.identity[0], variant_id=target.identity[1])
    return target.app.project_commands.task_result_directory(request)


def associate_upload_receipts(target, record):
    """Use one latest-state command for the captured keys, retrying only local CAS."""
    confirmed = [receipt for receipt in record["receipts"] if receipt["outcome"] == "confirmed"]
    if not confirmed:
        return []

    def commit():
        app = target.app
        for _attempt in range(3):
            for receipt in confirmed:
                receipt["association"] = "pending"
            if list(getattr(app, "active_version_identity", ()) or ()) != record["target"]["project_identity"]:
                return _defer(confirmed, "任务对应的工程版本已切换，上传回执已保留。")
            config = getattr(app, "config", None)
            user = getattr(app, "current_user", None)
            account = user.get("id") if isinstance(user, dict) else getattr(config, "user_id", None)
            if (getattr(config, "base_url", "https://paratranz.cn/api"), account) != (
                record["target"]["endpoint"],
                record["target"]["account_user_id"],
            ):
                return _defer(confirmed, "云端地址或账号已变化；原目标上传回执已保留。")
            source = app.slots.get(record["target"]["source_key"])
            if source is None:
                return _defer(confirmed, "上传对应的来源已移除，上传回执已保留。")
            collection = source.collection
            project_revision, variant_revision = app.project_revision, app.variant_revision
            changes, deferred = {}, []
            for receipt in confirmed:
                base = record["inputs"][receipt["index"]]["base"]
                key = EntryKey.from_dict(receipt["entry_key"])
                current = collection.get(key)
                reference = ExternalEntryRef("paratranz", record["target"]["scope"], receipt["remote_id"])
                if current is None or (
                    current.original,
                    current.context,
                    current.string_id,
                    _metadata(current.metadata),
                ) != (
                    base["original"],
                    base["context"],
                    base["string_id"],
                    _metadata(base["metadata"]),
                ):
                    deferred.extend(_defer([receipt], "原文、上下文、来源定位或条目已变化，关联待处理。"))
                    continue
                relevant = tuple(
                    ref
                    for ref in current.external_refs
                    if (ref.system, ref.scope) == (reference.system, reference.scope)
                )
                captured = tuple(
                    ExternalEntryRef.from_dict(ref)
                    for ref in base["external_refs"]
                    if (ref["system"], ref["scope"]) == (reference.system, reference.scope)
                )
                if relevant != captured and relevant != (reference,):
                    deferred.extend(_defer([receipt], "该词条已有新的远端关联，旧上传回执已保留。"))
                    continue
                refs = tuple(
                    ref
                    for ref in current.external_refs
                    if (ref.system, ref.scope) != (reference.system, reference.scope)
                )
                updated = replace(current, external_refs=(*refs, reference))
                if updated.external_refs != current.external_refs:
                    changes[key] = updated
            if not changes:
                for receipt in confirmed:
                    if receipt["association"] == "pending":
                        receipt["association"] = "applied"
                        receipt.pop("association_error", None)
                return deferred
            project_id, variant_id = target.identity
            committed = []

            def write():
                result = app.project_commands.replace_entry_records(
                    {
                        key: EntryStatePatch(row.translation, row.stage, row.external_refs)
                        for key, row in changes.items()
                    },
                    app.runtime_context,
                    expected_project_revision=project_revision,
                    expected_variant_revision=variant_revision,
                    expected_variant_ref=VariantRef(VariantId(variant_id), ProjectId(project_id)),
                )
                committed.append(result)
                if result.is_success:
                    from .types import ExecutionContext

                    if isinstance(target.context, ExecutionContext):
                        object.__setattr__(target.context, "_target_project_revision", app.project_revision)
                        object.__setattr__(target.context, "_target_variant_revision", app.variant_revision)
                return result

            try:
                result = capture_variant_command(target.context, write)
            except Exception as exc:
                if not committed or not committed[0].is_success:
                    raise
                from .diagnostics import technical_error

                record.setdefault("association_warnings", []).append(
                    f"本地关联已提交，但撤销回执保存失败：{technical_error(exc)}"
                )
                result = committed[0]
            if result.is_success:
                for receipt in confirmed:
                    if receipt["association"] == "pending":
                        receipt["association"] = "applied"
                        receipt.pop("association_error", None)
                from transbridge.ui.entry_projection import EntryProjectionUpdate

                snapshot = app._project_projection.snapshot()
                update = EntryProjectionUpdate.from_snapshot(snapshot, keys=changes)
                previous = source.collection
                source.collection = update.collection(previous)
                signal = getattr(app, "collection_changed", None)
                if signal is not None and source.collection is not previous:
                    signal.emit(app.collection)
                return deferred
            if not any(
                item.code
                in {"ACTIVE_PROJECT_REVISION_CHANGED", "ACTIVE_VARIANT_REVISION_CHANGED", "SOURCE_UPDATE_STALE"}
                for item in result.diagnostics
            ):
                return _defer(confirmed, "；".join(item.message for item in result.diagnostics))
        return _defer(confirmed, "当前内容持续变化，上传回执已保留，请稍后仅重试本地关联。")

    try:
        return target.dispatch(commit)
    except Exception as exc:
        from .diagnostics import technical_error

        pending = [receipt for receipt in confirmed if receipt.get("association") != "applied"]
        if len(pending) != len(confirmed):
            record.setdefault("association_warnings", []).append(
                f"本地关联已提交，但界面或请求回执更新失败：{technical_error(exc)}"
            )
        return _defer(pending, technical_error(exc))


def _metadata(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _defer(receipts, reason):
    result = []
    for receipt in receipts:
        receipt.update(association="deferred", association_error=reason)
        result.append({"key": receipt["key"], "remote_id": receipt["remote_id"], "error": reason})
    return result
