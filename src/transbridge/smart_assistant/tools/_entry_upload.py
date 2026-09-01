"""Retain remote identity and truthful outcomes for confirmed entry uploads."""

from dataclasses import replace

from transbridge.application.io.identity import ExternalEntryRef
from transbridge.application.ports.paratranz import ParaTranzEntry
from transbridge.application.tasks import TaskCancelled

from ._project_tool_mutations import ProjectToolTarget
from .base import ToolResult


def upload_entries(args, ctx, collection, client, project_id, cancellation):
    target = ProjectToolTarget.capture(ctx)
    target.check()
    requested = args.get("entry_ids")
    entries = [collection.get(key) for key in requested] if requested is not None else list(collection)
    if any(entry is None for entry in entries):
        return ToolResult.fail("找不到指定条目，未开始上传。", error_code="ENTRY_NOT_FOUND")
    if len({entry.identity for entry in entries}) != len(entries):
        return ToolResult.fail("上传条目重复，未开始上传。", error_code="DUPLICATE_ENTRY")
    entries = tuple(replace(entry) for entry in entries)
    uploaded = 0
    failed = []
    cancelled = False
    not_attempted = 0
    scope = f"project:{project_id}"
    for index, entry in enumerate(entries):
        remote_id = None
        remote_completed = False
        try:
            target.check()
            if cancellation is not None and cancellation.is_cancelled:
                raise TaskCancelled("上传已取消")
            references = [ref for ref in entry.external_refs if ref.system == "paratranz" and ref.scope == scope]
            if len(references) > 1:
                raise ValueError("条目存在重复 ParaTranz 远端引用")
            reference = references[0] if references and args.get("force_overwrite", False) else None
            result = client.upsert_entry(
                project_id,
                ParaTranzEntry(
                    reference.opaque_id if reference else None,
                    entry.key,
                    entry.original,
                    entry.translation or "",
                    entry.context or "",
                    entry.stage,
                ),
                force_overwrite=bool(args.get("force_overwrite", False)),
                cancellation=cancellation,
            )
            uploaded += 1
            remote_completed = True
            remote_id = getattr(result, "remote_id", None)
            if isinstance(remote_id, bool) or not isinstance(remote_id, int) or remote_id < 1:
                raise ValueError("远端未返回有效 ID；请检查已上传条目后重试。")
            if result.key != entry.key or result.original != entry.original:
                raise ValueError("远端返回的条目身份不匹配；请检查上传结果。")
            refs = tuple(ref for ref in entry.external_refs if not (ref.system == "paratranz" and ref.scope == scope))
            updated = replace(entry, external_refs=(*refs, ExternalEntryRef("paratranz", scope, remote_id)))
            target.commit_records((updated,))
        except TaskCancelled as exc:
            if not uploaded:
                raise
            cancelled = True
            not_attempted = len(entries) - index - 1
            failed.append({"key": entry.key, "error": str(exc), "remote_id": remote_id, "cancelled": True})
            break
        except Exception as exc:
            failed.append({"key": entry.key, "error": str(exc), "remote_id": remote_id})
            if remote_completed:
                # A remote side effect already happened: never automatically create again.
                not_attempted = len(entries) - index - 1
                break
    complete = not failed and uploaded == len(entries)
    return ToolResult(
        success=complete,
        partial=bool(uploaded and not complete),
        message=f"已上传 {uploaded}/{len(entries)} 条"
        + ("，其余上传已取消" if cancelled else f"，失败 {len(failed)} 条" if failed else ""),
        data={
            "uploaded": uploaded,
            "total": len(entries),
            "failed_items": failed,
            "cancelled": cancelled,
            "not_attempted": not_attempted,
        },
        failed_items=failed or None,
        error_code=None if complete else "UPLOAD_CANCELLED" if cancelled else "UPLOAD_INCOMPLETE",
        recovery_action=None if complete else "检查失败项和未处理项；已上传条目须核对远端 ID，勿直接重新创建。",
    )
