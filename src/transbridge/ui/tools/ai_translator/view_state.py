"""Semantic facade over the translator configuration widgets.

Only this module knows the concrete controls owned by :class:`AITranslatorView`.
The window orchestrator consumes intent-sized values and presentation updates.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .view_controls import TranslatorViewOwner

ViewMode = Literal["translate", "polish", "mixed"]
UiMode = Literal["translate", "polish", "mixed", "custom"]


@dataclass(frozen=True, slots=True)
class ScopeOptions:
    mode: ViewMode
    rules: list | None
    overwrite: bool
    max_tokens: int
    max_concurrent: int


class TranslatorViewPort:
    """Narrow, semantic interface used by ``AITranslatorWindow``."""

    def __init__(self, view: TranslatorViewOwner) -> None:
        self._view = view

    @property
    def selected_mode(self) -> UiMode:
        if self._view.controls.mode_custom.isChecked():
            return "custom"
        if self._view.controls.mode_mixed.isChecked():
            return "mixed"
        if self._view.controls.mode_polish.isChecked():
            return "polish"
        return "translate"

    @property
    def mode(self) -> ViewMode:
        if self.selected_mode == "custom":
            value = self._view.controls.custom_base_mode_combo.currentData()
            return value if value in {"translate", "polish", "mixed"} else "polish"
        return self.selected_mode

    @property
    def overwrite(self) -> bool:
        return self._view.controls.overwrite_check.isChecked()

    @property
    def rules(self) -> list:
        return self._view.controls.rule_editor.get_rules()

    @property
    def execution_order(self) -> Literal["serial", "parallel"]:
        return "serial" if self._view.controls.order_combo.currentIndex() == 0 else "parallel"

    @property
    def polish_preview_enabled(self) -> bool:
        return self._view.controls.polish_preview_check.isChecked()

    def scope_options(self) -> ScopeOptions:
        mode = self.mode
        return ScopeOptions(
            mode=mode,
            rules=self.rules if mode == "mixed" else None,
            overwrite=self.overwrite,
            max_tokens=self._view.controls.tokens_spin.value(),
            max_concurrent=self._view.controls.concurrent_spin.value(),
        )

    def update_provider_controls(self) -> None:
        self._view.controls.baseurl_edit.setEnabled(self._view.controls.provider_combo.currentIndex() == 0)

    def update_mode_controls(self) -> None:
        mode = self.mode
        selected_mode = self.selected_mode
        self._view.controls.custom_profile_group.setVisible(selected_mode == "custom")
        self._view.controls.overwrite_check.setVisible(mode == "translate")
        self._view.controls.scope_stack.setCurrentIndex(1 if mode == "mixed" else 0)
        self._view.controls.start_btn.setText(
            "▶ 开始执行"
            if selected_mode == "custom"
            else {
                "translate": "▶ 开始翻译",
                "polish": "▶ 开始润色",
                "mixed": "▶ 开始执行",
            }[mode]
        )

    def update_embedding_controls(self) -> None:
        mode = str(self._view.controls.embed_provider_combo.currentData() or "disabled")
        is_local = mode == "local"
        is_api = mode == "api"
        self._view.controls.embed_local_model_label.setVisible(is_local)
        self._view.controls.embed_local_status_label.setVisible(is_local)
        self._view.controls.embed_manage_btn.setVisible(is_local)
        raw_model_path = self._view.controls.embed_local_model_edit.text().strip()
        model_path = Path(raw_model_path) if raw_model_path else None
        installed = model_path is not None and model_path.is_dir() and (model_path / "modules.json").is_file()
        self._view.controls.embed_local_status_label.setText(
            f"{model_path.name} · 已安装并选中"
            if installed and model_path is not None
            else "尚未选择可用的本地向量模型"
        )
        for widget in (
            self._view.controls.embed_api_provider_label,
            self._view.controls.embed_api_provider_combo,
            self._view.controls.embed_model_label,
            self._view.controls.embed_model_edit,
            self._view.controls.embed_apikey_label,
            self._view.controls.embed_apikey_edit,
            self._view.controls.embed_baseurl_label,
            self._view.controls.embed_baseurl_edit,
        ):
            widget.setVisible(is_api)
        test_button = self._view.controls.embed_test_btn
        test_button.setVisible(is_api)
        test_button.setEnabled(is_api and not bool(test_button.property("embeddingConnectionBusy")))

    def update_post_process_controls(self) -> None:
        enabled = self._view.controls.pp_enable_check.isChecked()
        strict = enabled and self._view.controls.pp_strategy_combo.currentIndex() == 1
        self._view.controls.pp_strategy_combo.setEnabled(enabled)
        for widget in (
            self._view.controls.pp_consistency_check,
            self._view.controls.pp_format_check,
            self._view.controls.pp_quality_gate_check,
            self._view.controls.pp_refinement_check,
            self._view.controls.pp_arbitration_check,
            self._view.controls.pp_polish_check,
        ):
            widget.setEnabled(strict)
        self._view.controls.pp_strict_mode_check.setEnabled(
            strict and self._view.controls.pp_arbitration_check.isChecked()
        )
        self.update_polish_controls()

    def update_polish_controls(self) -> None:
        workflow_enabled = self._view.controls.pp_enable_check.isChecked()
        strict = workflow_enabled and self._view.controls.pp_strategy_combo.currentIndex() == 1
        enabled = workflow_enabled and (not strict or self._view.controls.pp_polish_check.isChecked())
        self._view.controls.pp_polish_scope_combo.setEnabled(strict and enabled)
        self._view.controls.pp_polish_level_combo.setEnabled(enabled)
