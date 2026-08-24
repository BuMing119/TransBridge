"""Preflight and batch-entry actions kept outside the window composition root."""

from __future__ import annotations

from PyQt6.QtWidgets import QMessageBox

from ._theme_support import AiThemeBinding, set_widget_brush
from .run_spec import preflight_ai_run


def preflight_candidates(window: object, mode: str) -> list:
    if mode == "polish":
        return [entry for entry in window._build_scope_candidates() if entry.translation]
    if mode == "mixed":
        collection = list(window._ctx.collection or ())
        partition = window._scope_presenter.partition_mixed(window._view_port.rules, collection)
        return list(partition.translate_entries) + list(partition.polish_entries)
    return window._build_scope_candidates()


def require_ready(window: object, mode: str, config: object, entries: list) -> bool:
    result = preflight_ai_run(mode, config, entries, esp_path=window._ctx.esp_path)
    if result.ready:
        return True
    QMessageBox.warning(window, "AI 运行条件未满足", result.reason or "请检查运行配置。")
    window.update_quick_run()
    return False


def open_batch_from_window(window: object) -> None:
    progress = type(window).open_for_batch_translation(
        window._ctx,
        window._step2,
        window,
        task_runtime=window._task_runtime,
        theme_view=window._theme_view,
    )
    if progress is not None:
        window.progress_window_created.emit(progress)
        window.close()


def apply_window_theme(window: object, binding: AiThemeBinding) -> None:
    ready = window._view.controls.start_btn.isEnabled()
    set_widget_brush(window._view.controls.preflight_label, binding.report("success" if ready else "warning"))


__all__ = ["apply_window_theme", "open_batch_from_window", "preflight_candidates", "require_ready"]
