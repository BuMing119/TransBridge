"""Run launchers that gate AI work on a durable pre-translation snapshot."""

from __future__ import annotations

from PyQt6.QtWidgets import QMessageBox

from .polish_runtime import create_polish_worker
from .run_controller import show_polish_progress, start_mixed_run, start_translation_run
from .version_snapshot import prepare_versioned_run


def start_versioned_translation(window: object, request: object) -> None:
    def start(session: object) -> None:
        if not window._run_controller.accepts(request.run_id):
            return
        try:
            start_translation_run(
                window._run_controller,
                window._ctx,
                request,
                progress_created=window.progress_window_created.emit,
                entry_activated=window._scope_presenter.locate_entry,
                version_snapshot_session=session,
                theme_view=window._theme_view,
            )
        except Exception as exc:
            window._run_controller.create_activity(request).fail(str(exc))
            QMessageBox.warning(window, "AI 翻译启动失败", str(exc))
        finally:
            window._run_controller.finish(request.run_id)
        window.close()

    _prepare(window, request, start, "AI 翻译")


def start_versioned_mixed(
    window: object,
    request: object,
    translate_entries: list,
    polish_entries: list,
) -> None:
    def start(session: object) -> None:
        if not window._run_controller.accepts(request.run_id):
            return
        window._version_snapshot_session = session
        window._active_mixed_preview = request.spec.execution_profile.preview_enabled
        window._active_mixed_spec = request.spec
        window._active_mixed_config = request.config
        try:
            window._active_mixed_progress = start_mixed_run(
                window._run_controller,
                request,
                window._ctx,
                request.config,
                translate_entries,
                polish_entries,
                finished=window._on_mixed_finished,
                error=lambda message: QMessageBox.warning(window, "混合模式错误", message),
                progress_created=window.progress_window_created.emit,
                theme_view=window._theme_view,
            )
        except Exception as exc:
            window._run_controller.create_activity(request).fail(str(exc))
            window._run_controller.finish(request.run_id)
            QMessageBox.warning(window, "AI 混合模式启动失败", str(exc))
            return
        started = f"已启动：翻译 {len(translate_entries)} 条，润色 {len(polish_entries)} 条"
        label = window._view.controls.preflight_label
        label.set_full_text(started)
        label.setToolTip(started)
        label.setAccessibleDescription(started)

    _prepare(window, request, start, "AI 混合模式")


def start_versioned_polish(window: object, request: object, entries: list, collection: object) -> None:
    def start(session: object) -> None:
        if not window._run_controller.accepts(request.run_id):
            return
        window._version_snapshot_session = session
        try:
            worker = create_polish_worker(
                window._ctx,
                request.config,
                entries,
                request_budget=request.request_budget,
            )
        except Exception as exc:
            window._run_controller.create_activity(request).fail(str(exc))
            window._run_controller.finish(request.run_id)
            QMessageBox.warning(window, "AI 润色启动失败", str(exc))
            return
        preview = window._view_port.polish_preview_enabled
        window._active_polish_config = request.config
        window._active_polish_spec = request.spec

        def publish(callback, payload: object) -> None:
            if not worker.was_cancelled:
                session.mark_completed()
            callback(payload, entries, collection)

        on_results = (
            (lambda payload: publish(window._on_polish_preview_ready, payload))
            if preview
            else (lambda results: publish(window._on_polish_finished_direct, results))
        )
        progress = show_polish_progress(
            window._run_controller,
            request,
            window,
            worker,
            entries,
            on_results=on_results,
            preview=preview,
            theme_view=window._theme_view,
        )
        window.progress_window_created.emit(progress)

    _prepare(window, request, start, "AI 润色")


def _prepare(window: object, request: object, start, title: str) -> None:
    label = window._view.controls.preflight_label
    message = "正在创建翻译前版本快照…"
    label.set_full_text(message)
    label.setToolTip(message)
    label.setAccessibleDescription(message)
    window._view.controls.start_btn.setEnabled(False)
    activity = window._run_controller.create_activity(request)

    def failed(error: str) -> None:
        if not window._run_controller.accepts(request.run_id):
            activity.finish(cancelled=True)
            return
        activity.fail(f"翻译前版本快照创建失败：{error}")
        window._run_controller.finish(request.run_id)
        QMessageBox.warning(window, f"{title}未启动", f"翻译前版本快照创建失败：{error}")
        window.update_quick_run()

    def ready(session: object) -> None:
        if not window._run_controller.accepts(request.run_id):
            activity.finish(cancelled=True)
            return
        start(session)

    session = prepare_versioned_run(
        window._ctx,
        request.spec,
        display_mode=window._view_port.selected_mode,
        on_ready=ready,
        on_error=failed,
    )
    window._version_snapshot_session = session


__all__ = ["start_versioned_mixed", "start_versioned_polish", "start_versioned_translation"]
