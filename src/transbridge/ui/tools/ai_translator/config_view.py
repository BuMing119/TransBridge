"""View facade and configuration adapters for the AI translator."""

from __future__ import annotations

from collections.abc import Callable
import logging
from typing import Protocol

from PyQt6.QtCore import QObject, QSignalBlocker, QTimer
from PyQt6.QtWidgets import QLineEdit, QListWidgetItem, QWidget

from transbridge.config.language_profiles import discover_language_profiles
from transbridge.ui.foundation.adapters import ThemeView

from .config_dialogs import render_paratranz_source
from .single_task_view import build_single_task_view, refresh_service_summary
from .view_controls import TranslatorControls

logger = logging.getLogger(__name__)


class AITranslatorViewCallbacks(Protocol):
    def on_provider_changed(self) -> None: ...
    def on_embed_provider_changed(self) -> None: ...
    def on_embedding_mode_activated(self) -> None: ...
    def on_embedding_api_provider_activated(self, index: int) -> None: ...
    def on_manage_embedding_models(self) -> None: ...
    def on_test_connection(self, target: str = "llm") -> None: ...
    def browse_file(self, target: QLineEdit, file_filter: str) -> None: ...
    def on_view_terms(self) -> None: ...
    def on_open_history(self) -> None: ...
    def on_open_settings(self) -> None: ...
    def on_batch_start(self) -> None: ...
    def on_start(self) -> None: ...
    def on_mode_changed(self) -> None: ...
    def update_estimate(self) -> None: ...
    def update_quick_run(self) -> None: ...
    def on_pp_enable_changed(self) -> None: ...
    def on_polish_changed(self) -> None: ...


class AITranslatorView:
    """Own all task controls while delegating their visual composition."""

    def __init__(
        self,
        parent: QWidget,
        callbacks: AITranslatorViewCallbacks,
        *,
        theme_view: ThemeView | None = None,
    ) -> None:
        self.theme_view = theme_view
        self.controls = TranslatorControls(self)
        build_single_task_view(
            self,
            parent,
            callbacks,
            language_profiles=discover_language_profiles(),
        )

    def refresh_service_summary(self) -> None:
        refresh_service_summary(self)


class WindowConfigView:
    """Adapter exposing config fields without leaking widgets to the presenter."""

    def __init__(
        self,
        view: AITranslatorView,
        callbacks: AITranslatorViewCallbacks,
        current_project: Callable[[], object | None],
    ) -> None:
        self._view = view
        self._callbacks = callbacks
        self._current_project = current_project

    def render_config(self, cfg: object) -> None:
        # Programmatic hydration is one transaction, never a sequence of user edits.
        blockers = [QSignalBlocker(value) for value in vars(self._view).values() if isinstance(value, QObject)]
        try:
            self._render_config(cfg)
        finally:
            del blockers

    def _render_config(self, cfg: object) -> None:
        h = self._view.controls
        h.provider_combo.setCurrentIndex(0 if cfg.provider != "anthropic" else 1)
        target_index = h.target_lang_combo.findData(cfg.target_lang)
        if target_index < 0:
            h.target_lang_combo.addItem(f"{cfg.target_lang}（配置不可用）", cfg.target_lang)
            target_index = h.target_lang_combo.count() - 1
        h.target_lang_combo.setCurrentIndex(target_index)
        h.model_edit.setText(cfg.model)
        h.apikey_edit.setText(cfg.api_key)
        h.baseurl_edit.setText(cfg.base_url)
        h.concurrent_spin.setValue(cfg.max_concurrent)
        h.tokens_spin.setValue(cfg.max_tokens_per_batch)
        h.output_tokens_spin.setValue(cfg.max_output_tokens)
        h.max_terms_spin.setValue(cfg.max_terms_per_batch)
        h.json_path_edit.setText(cfg.local_json_path)
        h.csv_path_edit.setText(getattr(cfg, "local_csv_path", ""))
        h.excel_path_edit.setText(cfg.local_excel_path)
        h.excel_orig_col_edit.setText(cfg.excel_original_col)
        h.excel_trans_col_edit.setText(cfg.excel_translation_col)
        h.pp_enable_check.setChecked(cfg.enable_post_process)
        h.pp_strategy_combo.setCurrentIndex(1 if getattr(cfg, "pp_strategy", "proofread") == "strict" else 0)
        h.pp_consistency_check.setChecked(cfg.pp_enable_consistency_check)
        h.pp_format_check.setChecked(cfg.pp_enable_format_validation)
        h.pp_quality_gate_check.setChecked(cfg.pp_enable_quality_gate)
        h.pp_refinement_check.setChecked(cfg.pp_enable_refinement)
        h.pp_polish_check.setChecked(cfg.pp_enable_polish)
        h.pp_polish_scope_combo.setCurrentIndex({"all": 0, "passed": 1, "has_issues": 2}.get(cfg.pp_polish_scope, 0))
        h.pp_polish_level_combo.setCurrentIndex(
            {"light": 0, "moderate": 1, "aggressive": 2}.get(cfg.pp_polish_level, 1)
        )
        h.pp_arbitration_check.setChecked(cfg.pp_enable_arbitration)
        h.pp_strict_mode_check.setChecked(cfg.pp_strict_arbitration)
        h.polish_preview_check.setChecked(cfg.polish_preview_enabled)
        h.order_combo.setCurrentIndex(1 if getattr(cfg, "mixed_execution_order", "serial") == "parallel" else 0)
        h.rule_editor.set_rules(list(getattr(cfg, "action_rules", [])))
        embed_mode = str(getattr(cfg.embedding, "mode", "disabled") or "disabled").casefold()
        if embed_mode not in {"disabled", "local", "api"}:
            embed_mode = "local" if cfg.embedding.provider == "local" else "api"
        h.embed_provider_combo.setCurrentIndex(max(h.embed_provider_combo.findData(embed_mode), 0))
        h.embed_api_provider_combo.setCurrentIndex(max(h.embed_api_provider_combo.findData(cfg.embedding.provider), 0))
        h.embed_local_model_id_edit.setText(getattr(cfg.embedding, "local_model_id", ""))
        h.embed_local_model_edit.setText(cfg.embedding.local_model_path)
        h.embed_model_edit.setText(cfg.embedding.model)
        h.embed_apikey_edit.setText(cfg.embedding.api_key)
        h.embed_baseurl_edit.setText(cfg.embedding.base_url)
        priority_map = {
            "dynamic": "dynamic（动态词库）",
            "paratranz": "paratranz（ParaTranz 术语）",
            "json": "json（本地 JSON）",
            "csv": "csv（本地 CSV）",
            "excel": "excel（本地 Excel）",
        }
        if cfg.term_priority:
            h.priority_list.clear()
            for key in cfg.term_priority:
                if key in priority_map:
                    h.priority_list.addItem(QListWidgetItem(priority_map[key]))
        self._callbacks.on_provider_changed()
        self._callbacks.on_embed_provider_changed()
        from .view_state import TranslatorViewPort

        TranslatorViewPort(self._view).update_post_process_controls()
        render_paratranz_source(h.priority_list, self._current_project())
        self._view.refresh_service_summary()

    def update_config(self, cfg: object) -> object:
        h = self._view.controls
        cfg.provider = "anthropic" if h.provider_combo.currentIndex() == 1 else "openai_compatible"
        cfg.target_lang = str(h.target_lang_combo.currentData() or "")
        cfg.model = h.model_edit.text().strip()
        cfg.api_key = h.apikey_edit.text().strip()
        cfg.base_url = h.baseurl_edit.text().strip()
        cfg.max_concurrent = h.concurrent_spin.value()
        cfg.max_tokens_per_batch = h.tokens_spin.value()
        cfg.max_output_tokens = h.output_tokens_spin.value()
        cfg.max_terms_per_batch = h.max_terms_spin.value()
        cfg.local_json_path = h.json_path_edit.text().strip()
        cfg.local_csv_path = h.csv_path_edit.text().strip()
        cfg.local_excel_path = h.excel_path_edit.text().strip()
        cfg.excel_original_col = h.excel_orig_col_edit.text().strip() or "A"
        cfg.excel_translation_col = h.excel_trans_col_edit.text().strip() or "B"
        cfg.enable_post_process = h.pp_enable_check.isChecked()
        cfg.pp_strategy = "strict" if h.pp_strategy_combo.currentIndex() == 1 else "proofread"
        cfg.pp_enable_consistency_check = h.pp_consistency_check.isChecked()
        cfg.pp_enable_format_validation = h.pp_format_check.isChecked()
        cfg.pp_enable_quality_gate = h.pp_quality_gate_check.isChecked()
        cfg.pp_enable_refinement = h.pp_refinement_check.isChecked()
        cfg.pp_enable_polish = h.pp_polish_check.isChecked()
        cfg.pp_polish_scope = {0: "all", 1: "passed", 2: "has_issues"}.get(
            h.pp_polish_scope_combo.currentIndex(), "all"
        )
        cfg.pp_polish_level = {0: "light", 1: "moderate", 2: "aggressive"}.get(
            h.pp_polish_level_combo.currentIndex(), "moderate"
        )
        cfg.pp_enable_arbitration = h.pp_arbitration_check.isChecked()
        cfg.pp_strict_arbitration = h.pp_strict_mode_check.isChecked()
        cfg.polish_preview_enabled = h.polish_preview_check.isChecked()
        cfg.mixed_execution_order = "parallel" if h.order_combo.currentIndex() == 1 else "serial"
        cfg.action_rules = h.rule_editor.get_rules()
        embed_mode = str(h.embed_provider_combo.currentData() or "disabled")
        cfg.embedding.mode = embed_mode
        if embed_mode == "local":
            cfg.embedding.provider = "local"
        elif embed_mode == "api":
            cfg.embedding.provider = str(h.embed_api_provider_combo.currentData() or "openai")
        cfg.embedding.local_model_id = h.embed_local_model_id_edit.text().strip()
        cfg.embedding.local_model_path = ""
        cfg.embedding.model = h.embed_model_edit.text().strip()
        cfg.embedding.api_key = h.embed_apikey_edit.text().strip()
        cfg.embedding.base_url = h.embed_baseurl_edit.text().strip()
        key_map = {
            "dynamic（动态词库）": "dynamic",
            "paratranz（ParaTranz 术语）": "paratranz",
            "json（本地 JSON）": "json",
            "csv（本地 CSV）": "csv",
            "excel（本地 Excel）": "excel",
        }
        cfg.term_priority = [
            key_map[h.priority_list.item(index).text()]
            for index in range(h.priority_list.count())
            if h.priority_list.item(index).text() in key_map
        ]
        return cfg


class ConfigAutosaveBinding:
    """Own the debounced Qt connections and releases its timer on close."""

    def __init__(
        self,
        view: AITranslatorView,
        parent: QWidget,
        save_callback: Callable[[], object],
        callbacks: AITranslatorViewCallbacks,
        *,
        refresh_callback: Callable[[], None] | None = None,
    ) -> None:
        self._view = view
        self._callbacks = callbacks
        self._save_callback = save_callback
        self._refresh_callback = refresh_callback
        self._timer = QTimer(parent)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._save_safely)
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        h = self._view.controls
        for signal in (
            h.provider_combo.currentIndexChanged,
            h.target_lang_combo.currentIndexChanged,
            h.apikey_edit.textChanged,
            h.baseurl_edit.textChanged,
            h.output_tokens_spin.valueChanged,
            h.max_terms_spin.valueChanged,
            h.json_path_edit.textChanged,
            h.csv_path_edit.textChanged,
            h.excel_path_edit.textChanged,
            h.excel_orig_col_edit.textChanged,
            h.excel_trans_col_edit.textChanged,
            h.priority_list.model().rowsMoved,
            h.pp_enable_check.toggled,
            h.pp_strategy_combo.currentIndexChanged,
            h.pp_consistency_check.toggled,
            h.pp_format_check.toggled,
            h.pp_quality_gate_check.toggled,
            h.pp_refinement_check.toggled,
            h.pp_polish_check.toggled,
            h.pp_polish_scope_combo.currentIndexChanged,
            h.pp_polish_level_combo.currentIndexChanged,
            h.pp_arbitration_check.toggled,
            h.pp_strict_mode_check.toggled,
            h.polish_preview_check.toggled,
            h.embed_provider_combo.currentIndexChanged,
            h.embed_api_provider_combo.currentIndexChanged,
            h.embed_local_model_id_edit.textChanged,
            h.embed_local_model_edit.textChanged,
            h.embed_model_edit.textChanged,
            h.embed_apikey_edit.textChanged,
            h.embed_baseurl_edit.textChanged,
            h.order_combo.currentIndexChanged,
        ):
            signal.connect(self.schedule)
        for signal in (
            h.model_edit.textChanged,
            h.concurrent_spin.valueChanged,
            h.tokens_spin.valueChanged,
            h.rule_editor.rules_changed,
        ):
            signal.connect(self.schedule_scope)
        h.pp_enable_check.toggled.connect(self._callbacks.on_pp_enable_changed)
        h.pp_strategy_combo.currentIndexChanged.connect(self._callbacks.on_pp_enable_changed)
        h.pp_polish_check.toggled.connect(self._callbacks.on_polish_changed)

    def schedule(self, *_args: object) -> None:
        request = self._refresh_callback
        if request is not None:
            request()
        else:
            self._callbacks.update_estimate()
            self._callbacks.update_quick_run()
        self._timer.start(2000)

    def schedule_scope(self, *_args: object) -> None:
        self.schedule()

    def close(self) -> None:
        if self._timer.isActive():
            self._timer.stop()
            self._save_safely()

    def _save_safely(self) -> None:
        try:
            self._save_callback()
        except Exception as exc:
            logger.warning("AI configuration autosave failed: %s", exc, exc_info=True)


__all__ = ["AITranslatorView", "ConfigAutosaveBinding", "WindowConfigView"]
