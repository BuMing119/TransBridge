"""Launch the one source-aware AI task from a frozen window draft."""

from __future__ import annotations

import logging

from PyQt6.QtWidgets import QMessageBox

from transbridge.ui.windowing import show_and_activate

from .run_controller import try_begin_run
from .run_spec import preflight_ai_run

logger = logging.getLogger(__name__)


def start_task(window) -> None:
    try:
        config = window._config_presenter.build()
        mode = window._view_port.mode
        tasks = tuple(task for task in window._task_sources(config=config) if task.entries)
    except Exception as exc:
        logger.exception("AI task source preparation failed")
        QMessageBox.warning(window, "AI 任务未启动", str(exc))
        return
    if not tasks:
        QMessageBox.warning(window, "AI 翻译", "所选来源没有符合范围的可处理词条。")
        return
    for task in tasks:
        preflight = preflight_ai_run(
            mode,
            config,
            task.entries,
            esp_path=task.esp_path,
            mixed_has_translation=bool(task.translate_entries),
        )
        if not preflight.ready:
            QMessageBox.warning(window, "AI 运行条件未满足", f"{task.label}：{preflight.reason}")
            return
    from transbridge.ui.paratranz.target_context import bound_paratranz_project

    from .term_source_inspector import TermSourceInspector

    remote = bound_paratranz_project(window._ctx)
    if (
        any(task.translate_entries for task in tasks)
        and not remote
        and all(TermSourceInspector.all_empty(config, task.esp_path) for task in tasks)
    ):
        answer = QMessageBox.question(
            window,
            "术语库为空",
            "当前术语来源为空，翻译质量可能下降。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
    request = try_begin_run(
        window._run_controller,
        mode,
        config,
        [e for task in tasks for e in task.entries],
        lambda: QMessageBox.warning(window, "AI 翻译", "已有任务正在启动。"),
        on_error=lambda message: QMessageBox.warning(window, "术语不可用", message),
        overwrite=window._view_port.overwrite,
        esp_path=tasks[0].esp_path,
        project_id=getattr(window._ctx, "active_project_id", None),
        variant_id=getattr(window._ctx, "active_variant_id", None),
        terminology_owner=window._ctx,
    )
    if request is None:
        return
    activity = progress = client = None
    try:
        from .task_progress import AiTaskProgressWindow
        from .task_session import TaskSession

        activity = window._run_controller.create_activity(request)
        session = TaskSession(window._ctx, tasks, request.spec)
        if remote:
            from transbridge.paratranz.api.paratranz_terms_api import ParatranzTermsAPI

            client = ParatranzTermsAPI(window._ctx.config)
        progress = AiTaskProgressWindow(
            request,
            session,
            activity,
            client=client,
            project_id=remote["id"] if remote else None,
            theme_view=window._theme_view,
        )
        window.progress_window_created.emit(progress)
        show_and_activate(progress)
        progress.prepare()
    except Exception as exc:
        logger.exception("AI task startup failed")
        if progress is not None:
            progress._prepare_failed(str(exc))
            progress.close()
        else:
            if activity is not None:
                activity.fail(str(exc))
            if client is not None:
                client.close()
        QMessageBox.warning(window, "AI 任务未启动", str(exc))
        return
    finally:
        window._run_controller.finish(request.run_id)
    window.close()
