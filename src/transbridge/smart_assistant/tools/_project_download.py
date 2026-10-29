"""Adapt Assistant calls to the same project download used by the GUI."""

from dataclasses import replace
import logging
from threading import Event
from types import SimpleNamespace

from transbridge.application.sync.download_context import DownloadContext
from transbridge.application.sync.download_run import run_download
from transbridge.application.sync.download_view import DownloadReceipt
from transbridge.application.sync.project_download import ProjectDownloadSession
from transbridge.application.tasks import TaskCancelled
from transbridge.config.task_results import TaskResultPreferences
from transbridge.paratranz.batch_download_snapshot import BatchDownloadSnapshotReader
from transbridge.paratranz.file_transport import UploadCancellation
from transbridge.paratranz.service import ParaTranzService

from ._project_tool_mutations import ProjectToolTarget
from .base import ToolResult
from .diagnostics import technical_error
from .types import ExecutionContext

logger = logging.getLogger(__name__)


def download_entries(args, ctx):
    """Download selected plugin directories, merge existing entries and save once."""
    dispatch = ctx if isinstance(ctx, ExecutionContext) else ExecutionContext(app_context=ctx)
    target = ProjectToolTarget.capture(dispatch)
    token = getattr(ctx, "cancellation_token", None)
    cancel = Event() if token is None else SimpleNamespace(is_set=lambda: token.is_cancelled, wait=token.wait)
    cancellation = token or UploadCancellation(cancel)
    session = None
    delivery_warnings = []
    try:
        cancellation.raise_if_cancelled()
        target.check()
        context = DownloadContext(dispatch, {"paratranz_project_id": args.get("project_id")})
        context.runtime_context = dispatch.request_context or target.app.runtime_context

        def execute(task_context, _fingerprint, operation, _cancel):
            def commit(mutation):
                cancellation.raise_if_cancelled()
                target.check()
                # The project command owns its worker-safe commit and projection.
                # Reuse Assistant admission here without moving a held lock to Qt.
                return dispatch._run_dispatched(mutation)

            result = operation(replace(task_context, run_id=task_context.run_id or _fingerprint), commit, cancellation)

            def acknowledge():
                target.check()
                target.acknowledge_projection()

            # Only acknowledge delivery on Qt, after the worker's project commit
            # and Assistant admission have both released their locks.
            try:
                ExecutionContext(app_context=target.app).safe_mutate_wait(acknowledge)
            except Exception as exc:
                logger.exception("Assistant download committed but projection acknowledgement failed")
                delivery_warnings.append(f"译文已保存，但界面交付确认暂未完成：{technical_error(exc)}")
            return result

        provider = getattr(target.app, "task_result_preferences_provider", None) or TaskResultPreferences
        policy = provider().download_policy.value
        session = ProjectDownloadSession(
            context,
            execute=execute,
            reader_factory=BatchDownloadSnapshotReader,
            service_factory=ParaTranzService.from_config,
            default_policy=policy,
        )

        def progress(*_):
            pass

        sources = session.load_sources(progress, cancel)
        selected = args.get("source_ids")
        selected = (
            tuple(source.source_id for source in sources if source.selected) if selected is None else tuple(selected)
        )
        latest = None

        def compare(*values):
            nonlocal latest
            latest = session.compare(*values)
            return latest

        outcome = run_download(selected, policy, (), compare, session.apply, progress, cancel)
        preview = (outcome.preview or latest) if isinstance(outcome, DownloadReceipt) else outcome
        updated = outcome.updated if isinstance(outcome, DownloadReceipt) else 0
        pending = preview.deferred if preview is not None else 0
        retryable = outcome.retryable
        unchanged = preview.unchanged if preview is not None else 0
        if isinstance(outcome, DownloadReceipt) and outcome.preview is None and latest is not None:
            unchanged += max(0, latest.updates - updated)
        if retryable:
            message = (
                f"已下载并保存 {updated} 条译文；其余来源未完成，请重新下载。"
                if updated
                else "本轮下载未完成，请重新下载。"
            )
        elif pending:
            message = f"已下载并保存 {updated} 条译文。" if updated else "本轮分歧译文未合入。"
        elif isinstance(outcome, DownloadReceipt):
            message = f"已下载并保存 {updated} 条译文。" if updated else "已保存当前本地版本，无新增译文。"
        else:
            message = "所选插件译文无需更新。"
        if pending:
            message += f"{pending} 条分歧保留本地内容，请在需要时重新下载并处理。"
        return ToolResult(
            success=not retryable,
            partial=bool(pending or retryable),
            message=message,
            data={
                "updated_count": updated,
                "unchanged_count": unchanged,
                "pending_count": pending,
                "source_ids": list(selected),
                "retryable": retryable,
            },
            error_code="DOWNLOAD_INCOMPLETE" if retryable else None,
            warnings=delivery_warnings or None,
        )
    except TaskCancelled:
        raise
    except Exception as exc:
        cancellation.raise_if_cancelled()
        return ToolResult.fail(f"下载失败: {technical_error(exc)}", error_code="DOWNLOAD_FAILED")
    finally:
        if session is not None:
            session.discard()
