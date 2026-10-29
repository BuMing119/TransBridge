"""Adapt the shared project download use case to a Qt window and task runtime."""

from PyQt6.QtWidgets import QMessageBox

from transbridge.application.sync.project_download import ProjectDownloadSession
from transbridge.config.task_results import TaskResultPreferences
from transbridge.paratranz.batch_download_snapshot import BatchDownloadSnapshotReader
from transbridge.paratranz.service import ParaTranzService

from .batch_download_commit import run_download_task
from .batch_download_dialog import BatchDownloadDialog


class BatchDownloadSession(ProjectDownloadSession):
    """Supply GUI dependencies; all download behavior lives in the shared use case."""

    def __init__(self, context, runtime, *, reader_factory=BatchDownloadSnapshotReader, service_factory=None):
        provider = getattr(context, "task_result_preferences_provider", None)
        policy = (provider() if provider else TaskResultPreferences()).download_policy
        self.runtime = runtime
        super().__init__(
            context,
            execute=lambda *args: run_download_task(runtime.tasks, *args),
            reader_factory=reader_factory,
            service_factory=service_factory or ParaTranzService.from_config,
            default_policy=policy,
        )


def open_batch_download(context, runtime, parent=None, *, values=None):
    try:
        if values:
            from .download_context import DownloadContext

            context = DownloadContext(context, values)
        session = BatchDownloadSession(context, runtime)
    except (ValueError, RuntimeError, AttributeError) as exc:
        QMessageBox.warning(parent, "无法开始下载", str(exc))
        return None
    dialog = BatchDownloadDialog(
        context.project_name or "当前工程",
        str(context.active_variant or "当前版本"),
        session.target.project_name or str(session.target.project_id),
        load_sources=session.load_sources,
        compare=session.compare,
        apply=session.apply,
        discard=getattr(session, "discard", None),
        default_policy=getattr(session, "default_policy", "keep_current"),
        parent=parent,
    )
    dialog.show()
    return dialog
