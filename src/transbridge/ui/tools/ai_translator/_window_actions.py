"""Preflight and batch-entry actions kept outside the window composition root."""

from __future__ import annotations

from PyQt6.QtCore import QSignalBlocker
from PyQt6.QtWidgets import QMessageBox

from transbridge.ui.foundation.components import ComponentStyle, SemanticState

from ._theme_support import AiThemeBinding, set_widget_brush
from .run_spec import AiPreflightCode, preflight_ai_run
from .scope_presenter import MixedScope


def preflight_candidates(window: object, mode: str, *, mixed_scope: MixedScope | None = None) -> list:
    if mode == "polish":
        return [entry for entry in window._build_scope_candidates() if entry.translation]
    if mode == "mixed":
        partition = mixed_scope
        if partition is None:
            collection = list(window._ctx.collection or ())
            partition = window._scope_presenter.partition_mixed(window._view_port.rules, collection)
        return list(partition.translate_entries) + list(partition.polish_entries)
    return window._build_scope_candidates()


def require_ready(
    window: object, mode: str, config: object, entries: list, *, mixed_has_translation: bool | None = None
) -> bool:
    result = preflight_ai_run(
        mode, config, entries, esp_path=window._ctx.esp_path, mixed_has_translation=mixed_has_translation
    )
    embedding = getattr(config, "embedding", None)
    missing_local_model = str(getattr(embedding, "mode", "disabled") or "disabled").casefold() == "local" and any(
        issue.code == AiPreflightCode.MISSING_EMBEDDING_CONFIGURATION for issue in result.issues
    )
    if missing_local_model:
        if not window._embedding_models.resolve_missing():
            return False
        config = window._config_presenter.build()
        result = preflight_ai_run(
            mode, config, entries, esp_path=window._ctx.esp_path, mixed_has_translation=mixed_has_translation
        )
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
        settings_requested=window._settings_requested,
    )
    if progress is not None:
        window.progress_window_created.emit(progress)
        window.close()


def open_settings_from_window(window: object) -> None:
    """Open global AI settings and refresh service-only fields on return."""

    callback = window._settings_requested
    if callback is None:
        return
    callback()

    from transbridge.config.llm import LLMConfig

    config = LLMConfig.load_from_file()
    controls = window._view.controls
    widgets = (controls.provider_combo, controls.model_edit, controls.apikey_edit, controls.baseurl_edit)
    blockers = [QSignalBlocker(widget) for widget in widgets]
    controls.provider_combo.setCurrentIndex(0 if config.provider != "anthropic" else 1)
    controls.model_edit.setText(config.model)
    controls.apikey_edit.setText(config.api_key)
    controls.baseurl_edit.setText(config.base_url)
    del blockers
    window.on_provider_changed()
    refresh_summary = getattr(window._view, "refresh_service_summary", None)
    if callable(refresh_summary):
        refresh_summary()
    window.update_quick_run()


def update_window_quick_run(window: object) -> None:
    if window._custom_profiles.block_unavailable_start():
        return
    mode = window._view_port.mode
    config = window._config_presenter.build()
    execution_profile = window._config_presenter.execution_profile()
    mixed_scope = (
        window._scope_presenter.partition_mixed(window._view_port.rules, window._ctx.collection or ())
        if mode == "mixed"
        else None
    )
    candidates = preflight_candidates(window, mode, mixed_scope=mixed_scope)
    preflight = preflight_ai_run(
        mode,
        config,
        candidates,
        esp_path=window._ctx.esp_path,
        mixed_has_translation=None if mixed_scope is None else bool(mixed_scope.translate_entries),
    )
    controls = window._view.controls
    estimate = controls.mixed_estimate_lbl.text() if mode == "mixed" else controls.estimate_lbl.text()
    active = window._run_controller.active_request
    state = window._quick_run_presenter.present(
        mode=mode,
        entry_count=len(candidates),
        estimate_text=estimate,
        overwrite=window._view_port.overwrite,
        preflight=preflight,
        active_run_id=None if active is None else active.run_id,
    )
    controls.start_btn.setEnabled(state.enabled)
    preflight_text = state.status_text(execution_profile.summary)
    if mode == "polish" and not execution_profile.enable_polish:
        controls.start_btn.setText("▶ 开始执行")
    controls.preflight_label.set_full_text(preflight_text)
    controls.preflight_label.setToolTip(preflight_text)
    controls.preflight_label.setAccessibleDescription(state.enabled_reason or state.scope_summary or "运行条件已满足")
    ComponentStyle.apply_state(
        controls.preflight_label,
        SemanticState.SUCCESS if state.enabled else SemanticState.WARNING,
    )
    set_widget_brush(
        controls.preflight_label,
        window._theme_binding.report("success" if state.enabled else "warning"),
    )


def apply_window_theme(window: object, binding: AiThemeBinding) -> None:
    ready = window._view.controls.start_btn.isEnabled()
    set_widget_brush(window._view.controls.preflight_label, binding.report("success" if ready else "warning"))


__all__ = [
    "apply_window_theme",
    "open_batch_from_window",
    "open_settings_from_window",
    "preflight_candidates",
    "require_ready",
    "update_window_quick_run",
]
