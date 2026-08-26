"""Composition for the detailed standalone-proofreading progress surface."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QDialog, QMessageBox

from transbridge.ui.foundation.adapters import ThemeView
from transbridge.ui.windowing import show_and_activate

from .run_view import AiWorkflowProgressWindow
from .workflow_progress import WorkflowProgress, stages_for_profile

if TYPE_CHECKING:
    from .run_controller import RunController, TranslationRunRequest


def show_polish_progress(
    controller: RunController,
    request: TranslationRunRequest,
    parent: object,
    worker: object,
    entries: list,
    *,
    on_results: Callable[[object], None],
    preview: bool,
    theme_view: ThemeView | None = None,
) -> AiWorkflowProgressWindow:
    """Start proofreading and project its real effective stages into one window."""

    from .task_adapter import AiLegacyRunState

    profile = request.spec.execution_profile
    run_id = request.run_id
    activity = controller.create_activity(request)
    progress = AiWorkflowProgressWindow(
        worker,
        activity,
        title="AI 润色",
        workflow_summary=profile.summary,
        stages=stages_for_profile(profile, include_translation=False),
        auxiliary_stat="问题",
        parent=parent,
        theme_view=theme_view,
    )
    controller.attach(run_id, worker=worker, activity=activity)

    detailed_signal = getattr(worker, "detailed_progress", None)
    if detailed_signal is not None:
        detailed_signal.connect(controller.guard(run_id, progress.apply_progress))
        detailed_signal.connect(
            controller.guard(
                run_id,
                lambda value: activity.progress(value.overall_current, value.overall_total, value.message),
            )
        )
    else:
        worker.progress.connect(
            controller.guard(
                run_id,
                lambda current, total, message: progress.apply_progress(
                    WorkflowProgress(
                        stage="polish",
                        stage_label="润色",
                        current=current,
                        total=total,
                        message=message,
                        overall_current=round(current / total * 1000) if total else 0,
                    )
                ),
            )
        )
        worker.progress.connect(controller.guard(run_id, activity.progress))

    log_signal = getattr(worker, "log", None)
    if log_signal is not None:
        log_signal.connect(controller.guard(run_id, progress.append_log))

    def done(results: object) -> None:
        cancelled = activity.activity.state is AiLegacyRunState.CANCELLING
        if cancelled:
            progress.mark_cancelled()
        else:
            progress.mark_finished()
        if not preview:
            on_results(results)
            return
        from ._polish_preview_dialog import _PolishPreviewDialog

        preview_dialog = _PolishPreviewDialog(entries, results, parent=parent, theme_view=theme_view)
        if preview_dialog.exec() == QDialog.DialogCode.Accepted:
            on_results((results, preview_dialog.get_results()))

    worker.finished_all.connect(controller.terminal_guard(run_id, done))
    worker.finished_all.connect(
        lambda _results: activity.finish(cancelled=activity.activity.state is AiLegacyRunState.CANCELLING)
    )
    worker.error.connect(controller.guard(run_id, progress.mark_error))
    worker.error.connect(controller.guard(run_id, activity.fail))
    worker.error.connect(
        controller.terminal_guard(
            run_id,
            lambda error: QMessageBox.critical(parent, "润色错误", error),
        )
    )
    worker.finished.connect(worker.deleteLater)
    try:
        worker.start()
        show_and_activate(progress, deferred=True)
    except Exception:
        controller.cancel(run_id)
        raise
    return progress


__all__ = ["show_polish_progress"]
