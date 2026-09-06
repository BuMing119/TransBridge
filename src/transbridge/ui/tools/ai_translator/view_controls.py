"""Public control namespace owned by the translator view."""

from __future__ import annotations

from typing import Protocol


class TranslatorControls:
    """Expose the view's concrete widgets without leaking its private storage."""

    _NAMES = frozenset({
        "advanced_btn",
        "apikey_edit",
        "baseurl_edit",
        "batch_btn",
        "concurrent_spin",
        "custom_base_mode_combo",
        "custom_profile_combo",
        "custom_profile_delete_btn",
        "custom_profile_export_btn",
        "custom_profile_group",
        "custom_profile_import_btn",
        "custom_profile_new_btn",
        "custom_profile_rename_btn",
        "custom_profile_status_label",
        "embed_apikey_edit",
        "embed_apikey_label",
        "embed_api_provider_combo",
        "embed_api_provider_label",
        "embed_baseurl_edit",
        "embed_baseurl_label",
        "embed_local_model_edit",
        "embed_local_model_id_edit",
        "embed_local_model_label",
        "embed_local_status_label",
        "embed_manage_btn",
        "embed_model_edit",
        "embed_model_label",
        "embed_provider_combo",
        "embed_test_btn",
        "estimate_lbl",
        "excel_orig_col_edit",
        "excel_path_edit",
        "excel_trans_col_edit",
        "csv_path_edit",
        "json_path_edit",
        "llm_test_btn",
        "max_terms_spin",
        "mixed_estimate_lbl",
        "naming_scheme_combo",
        "naming_scheme_manage_btn",
        "naming_scheme_status_label",
        "mode_mixed",
        "mode_custom",
        "mode_polish",
        "mode_translate",
        "model_edit",
        "order_combo",
        "output_tokens_spin",
        "overwrite_check",
        "polish_preview_check",
        "pp_box",
        "preset_table_view",
        "preset_selection",
        "preset_untranslated",
        "pp_arbitration_check",
        "pp_consistency_check",
        "pp_enable_check",
        "pp_format_check",
        "pp_polish_check",
        "pp_polish_level_combo",
        "pp_polish_scope_combo",
        "pp_quality_gate_check",
        "pp_refinement_check",
        "pp_strict_mode_check",
        "pp_strategy_combo",
        "priority_list",
        "save_term_source_as_scheme_btn",
        "provider_combo",
        "rule_editor",
        "scope_cat_all_btn",
        "scope_cat_btns",
        "scope_label_all_btn",
        "scope_label_btns",
        "preflight_label",
        "scope_stack",
        "scope_stage_all_btn",
        "scope_stage_btns",
        "service_summary_label",
        "settings_btn",
        "start_btn",
        "tabs",
        "target_lang_combo",
        "tokens_spin",
    })

    def __init__(self, owner: object) -> None:
        object.__setattr__(self, "_owner", owner)

    def __getattr__(self, name: str) -> object:
        if name not in self._NAMES:
            raise AttributeError(name)
        return getattr(self._owner, f"_{name}")

    def __setattr__(self, name: str, value: object) -> None:
        if name not in self._NAMES:
            raise AttributeError(name)
        setattr(self._owner, f"_{name}", value)


class TranslatorViewOwner(Protocol):
    controls: TranslatorControls
