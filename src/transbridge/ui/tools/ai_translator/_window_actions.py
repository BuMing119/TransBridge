"""Preflight and settings actions kept outside the window composition root."""

from __future__ import annotations

from PyQt6.QtCore import QSignalBlocker
from PyQt6.QtWidgets import QMessageBox

from transbridge.ui.foundation.components import ComponentStyle, SemanticState
from transbridge.ui.source_input_state import original_known

from ._theme_support import AiThemeBinding, set_widget_brush
from .run_preparation import confirm_optional_retrieval
from .run_spec import preflight_ai_run
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
    """Validate a caller-owned execution draft, applying an approved fallback to it."""
    result = preflight_ai_run(
        mode, config, entries, esp_path=window._ctx.esp_path, mixed_has_translation=mixed_has_translation
    )
    if not result.blocking_issues:
        execution = confirm_optional_retrieval(window, config, result.optional_issues)
        if execution is None:
            return False
        if execution is not config:
            config.enable_semantic_match = execution.enable_semantic_match
            config.embedding = execution.embedding
        return True
    QMessageBox.warning(window, "AI 运行条件未满足", result.blocking_reason or "请检查运行配置。")
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
    if getattr(window, "_task_launch", None) is not None:
        window._view.controls.start_btn.setEnabled(False)
        window._view.controls.preflight_label.set_full_text("正在准备 AI 任务…")
        return
    if not hasattr(window, "_custom_profiles") or window._custom_profiles.block_unavailable_start():
        return
    if missing_original_source_keys(window):
        controls = window._view.controls
        controls.start_btn.setText("补充原文…")
        _render_quick_run(window, "所选保存译文缺少原文。选择输入文件并对应词条后，可继续准备 AI 任务。", ready=True)
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
    reason = next((check.blocking_reason for check in checks if check.blocking_issues), None)
    if not entries:
        reason = "所选来源没有符合范围的可处理词条"
    if window._run_controller.is_running:
        reason = "已有 AI 任务正在启动"
    controls = window._view.controls
    selection = getattr(window, "_recovery_selection", None)
    controls.start_btn.setText(
        ("重新开始" if selection[1] else "继续任务")
        if selection
        else ("开始 AI 翻译" if mode == "translate" else "开始 AI 任务")
    )
    naming_schemes = getattr(window, "_naming_schemes", None)
    naming_scheme = getattr(naming_schemes, "summary_label", "保持当前译名")
    text = reason or (
        f"已选 {len(tasks)} 个插件，本次处理 {len(entries)} 条；译名方案：{naming_scheme}；流程：{profile.summary}"
    )
    _render_quick_run(window, text, ready=reason is None)


def selected_source_keys(window):
    slots = getattr(window._ctx, "slots", {})
    panel = getattr(getattr(window, "_view", None), "sources_panel", None)
    selected = tuple(slots.values()) if panel is None else tuple(panel.selected_slots())
    return tuple(key for key, slot in slots.items() if any(slot is value for value in selected))


def missing_original_source_keys(window):
    return tuple(
        key
        for key in selected_source_keys(window)
        if any(
            not original_known(entry)
            for entry in window._task_scope.candidates(window._ctx.slots[key], window._scope_presenter.state)
        )
    )


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
