# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Map confirmed file selections to the workbench's project upload session."""

from dataclasses import asdict
from threading import Event
from types import SimpleNamespace

from transbridge.application.assistant_requests.admission import validate_effect
from transbridge.application.file_upload import UPLOAD_MODES
from transbridge.application.sync.download_context import DownloadContext
from transbridge.application.tasks import TaskCancelled
from transbridge.paratranz.project_upload import CategorizedUploadSession, Selection, upload_target_identity

from ._project_tool_mutations import ProjectToolTarget
from .base import ToolResult
from .diagnostics import technical_error


def _ids(args, field):
    if field not in args:
        return ()
    values = args[field]
    if not isinstance(values, list) or not values or not all(isinstance(v, str) and v.strip() for v in values):
        raise ValueError(f"{field} 必须是非空字符串列表。")
    if len(values) != len(set(values)):
        raise ValueError(f"{field} 中包含重复项。")
    return tuple(values)


def _upload_context(args, ctx):
    if "project_id" in args:
        pid = args["project_id"]
        if type(pid) not in (str, int) or not str(pid).isdecimal() or int(pid) <= 0:
            raise ValueError("project_id 必须是正整数。")
    context = DownloadContext(ctx, {"paratranz_project_id": args.get("project_id")})
    context.runtime_context = getattr(ctx, "request_context", None) or ctx.runtime_context
    return context


def capture_upload_target(args, ctx):
    """Pin the workbench destination before the confirmation dialog yields to Qt."""
    context = _upload_context(args, ctx)
    ctx.confirmed_upload_target = upload_target_identity(context, context.resolve_paratranz_target())


def project_files(args, ctx, *, compare):
    try:
        if unknown := args.keys() - {"project_id", "mode", "source_ids", "categorized", "file_keys"}:
            raise ValueError(f"文件操作不支持这些参数：{', '.join(sorted(unknown))}。请按插件和文件选择。")
        mode = args.get("mode")
        if mode not in UPLOAD_MODES:
            raise ValueError(f"请明确选择文件上传模式：{', '.join(UPLOAD_MODES)}。")
        categorized = args.get("categorized", False)
        if not isinstance(categorized, bool):
            raise ValueError("categorized 必须是布尔值。")
        source_ids = _ids(args, "source_ids")
        requested_keys = _ids(args, "file_keys")
        if not compare and not requested_keys:
            raise ValueError("请指定本次授权上传的完整 file_keys，可先比较文件取得。")
        if getattr(ctx, "selected_entry_ids", ()):
            raise ValueError("当前请求限定了词条选区。文件上传无法精确表达该选区，请重新选择完整文件。")
        target = ProjectToolTarget.capture(ctx)

        def check_allowed():
            target.check()
            effect_id = getattr(ctx, "assistant_effect_id", "")
            if effect_id:
                validate_effect(ctx.assistant_gate.current_request(), effect_id)

        check_allowed()
        token = getattr(ctx, "cancellation_token", None)
        if token is not None:
            token.raise_if_cancelled()
        cancel = Event() if token is None else SimpleNamespace(is_set=lambda: token.is_cancelled, wait=token.wait)
        context = _upload_context(args, ctx)
        namespace = None
        if not source_ids:
            namespaces = {entry.identity.namespace for entry in target.collection or ()}
            if len(namespaces) != 1:
                raise ValueError("请指定本次操作的工程来源 source_ids。")
            namespace = namespaces.pop()
        session = CategorizedUploadSession(
            context,
            source_namespace=namespace,
            source_ids=source_ids or None,
            categorized=categorized,
            check_allowed=check_allowed,
        )
        if getattr(ctx, "confirmed_upload_target", session.stamp) != session.stamp:
            raise ValueError("已确认的上传工程、版本、云端项目或账号已变化，请重新确认。")
        sources = session.load_preview(_progress, cancel)
        available = {source.source_id for source in sources}
        if source_ids and not set(source_ids) <= available:
            raise ValueError("所选来源不在当前上传范围内。")
        selected_sources = source_ids or tuple(source.source_id for source in sources)
        keys = tuple(
            file.file_key for source in sources if source.source_id in selected_sources for file in source.files
        )
        if requested_keys and not set(requested_keys) <= set(keys):
            raise ValueError("所选文件不在当前范围内，请重新比较。")
        selection = Selection(selected_sources, requested_keys or keys, translation_mode=mode)
        if compare:
            return ToolResult.ok("文件差异比较完成", data={"files": session.compare(selection, cancel), "mode": mode})
        receipt = session.upload(selection, _progress, cancel)
        ok = not any((receipt.failed_count, receipt.pending_count, receipt.cancelled_count, receipt.retry_count))
        data = asdict(receipt)
        actual = getattr(session, "last_receipt", None)
        files = actual.files if actual is not None else ()
        data["files"] = [asdict(file) for file in files]
        committed = any(file.source_applied or file.translation_applied for file in files)
        return ToolResult(
            success=ok,
            partial=not ok and (receipt.success_count > 0 or committed),
            message=(
                f"上传完成 {receipt.success_count} 个文件，失败 {receipt.failed_count} 个，"
                f"取消 {receipt.cancelled_count} 个，待处理 {receipt.pending_count} 个。"
            ),
            data=data,
            error_code=None if ok else "UPLOAD_INCOMPLETE",
        )
    except TaskCancelled:
        raise
    except Exception as exc:
        return ToolResult.fail(f"文件操作失败：{technical_error(exc)}", error_code="PROJECT_FILE_OPERATION_FAILED")


def _progress(*_):
    pass
