"""Preflight and settings actions kept outside the window composition root."""

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


def open_settings_from_window(window: object) -> None:
    """Open global AI settings and refresh service-only fields on return."""

    callback = window._settings_requested
    if callback is None:
        return
    callback()

    from transbridge.config.llm import LLMConfig

    config = LLMConfig.load_from_file()
    window._config_presenter.refresh_service(config)
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


def update_window_quick_run(window: object, *, config=None, tasks=None) -> None:
    if not hasattr(window, "_custom_profiles") or window._custom_profiles.block_unavailable_start():
        return
    mode = window._view_port.mode
    from transbridge.application.translation.ai_execution_profile import AiExecutionProfile

    config = config if config is not None else window._config_presenter.build()
    profile = AiExecutionProfile.from_config(mode, config)
    try:
        tasks = tasks if tasks is not None else window._task_sources(config=config)
    except ValueError as exc:
        _render_quick_run(window, str(exc), ready=False)
        return
    entries = [entry for task in tasks for entry in task.entries]
    checks = [
        preflight_ai_run(
            mode,
            config,
            task.entries,
            esp_path=task.esp_path,
            mixed_has_translation=bool(task.translate_entries),
        )
        for task in tasks
        if task.entries
    ]
    reason = next((check.reason for check in checks if not check.ready), None)
    if not entries:
        reason = "所选来源没有符合范围的可处理词条"
    if window._run_controller.is_running:
        reason = "已有 AI 任务正在启动"
    controls = window._view.controls
    controls.start_btn.setText("开始 AI 翻译" if mode == "translate" else "开始 AI 任务")
    naming_schemes = getattr(window, "_naming_schemes", None)
    naming_scheme = getattr(naming_schemes, "summary_label", "保持当前译名")
    text = reason or (
        f"已选 {len(tasks)} 个插件，本次处理 {len(entries)} 条；译名方案：{naming_scheme}；流程：{profile.summary}"
    )
    _render_quick_run(window, text, ready=reason is None)


def _render_quick_run(window, text: str, *, ready: bool) -> None:
    controls = window._view.controls
    controls.start_btn.setEnabled(ready)
    controls.preflight_label.set_full_text(text)
    controls.preflight_label.setToolTip(text)
    controls.preflight_label.setAccessibleDescription(text)
    ComponentStyle.apply_state(controls.preflight_label, SemanticState.SUCCESS if ready else SemanticState.WARNING)
    set_widget_brush(
        controls.preflight_label,
        window._theme_binding.report("success" if ready else "warning"),
    )


def apply_window_theme(window: object, binding: AiThemeBinding) -> None:
    ready = window._view.controls.start_btn.isEnabled()
    set_widget_brush(window._view.controls.preflight_label, binding.report("success" if ready else "warning"))


__all__ = [
    "apply_window_theme",
    "open_settings_from_window",
    "preflight_candidates",
    "require_ready",
    "update_window_quick_run",
]
