"""Built-in AI workflow defaults editor."""

from __future__ import annotations

from copy import deepcopy

from PyQt6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QLabel, QSpinBox, QVBoxLayout, QWidget

from transbridge.application.translation.ai_execution_profile import apply_profile_settings, ensure_workflow_profiles
from transbridge.config.language_profiles import discover_language_profiles

from .page_common import SettingsPage, apply_if_present

_PRESETS = (("translate", "翻译"), ("polish", "润色"), ("mixed", "混合"))
_BOOL_FIELDS = (
    ("enable_post_process", "启用质量处理"),
    ("pp_enable_consistency_check", "术语一致性检查"),
    ("pp_enable_format_validation", "格式验证"),
    ("pp_enable_quality_gate", "LLM 质量检测"),
    ("pp_enable_refinement", "LLM 自动修复"),
    ("pp_enable_polish", "润色优化"),
    ("polish_preview_enabled", "润色前预览"),
    ("pp_enable_arbitration", "LLM 质量裁决"),
    ("pp_strict_arbitration", "严格裁决模式"),
)
_BATCH_FIELDS = (
    ("pp_quality_gate_batch_size", "质量检测批次"),
    ("pp_refinement_batch_size", "自动修复批次"),
    ("pp_polish_batch_size", "润色批次"),
    ("pp_arbitration_batch_size", "质量裁决批次"),
)


class AiDefaultsPage(SettingsPage):
    def __init__(self, config: object, parent=None) -> None:
        super().__init__(parent)
        self._config = config
        self._profiles = deepcopy(ensure_workflow_profiles(config))
        self._active = "translate"
        self._rendering = False
        root = QVBoxLayout(self)
        note = QLabel("这里编辑新任务使用的默认质量方案；任务弹窗仍可为单次运行覆盖这些值。", self)
        note.setWordWrap(True)
        root.addWidget(note)
        form_host = QWidget(self)
        form = QFormLayout(form_host)
        self.preset_combo = QComboBox(form_host)
        for value, label in _PRESETS:
            self.preset_combo.addItem(label, value)
        self.preset_combo.currentIndexChanged.connect(self._switch_preset)
        form.addRow("默认方案", self.preset_combo)
        self.target_lang_combo = QComboBox(form_host)
        for profile in discover_language_profiles():
            self.target_lang_combo.addItem(f"{profile.display_name} ({profile.locale})", profile.locale)
        current_locale = str(getattr(config, "target_lang", "zh_CN"))
        self.target_lang_combo.setCurrentIndex(max(0, self.target_lang_combo.findData(current_locale)))
        form.addRow("默认目标语言", self.target_lang_combo)
        self.strategy_combo = QComboBox(form_host)
        self.strategy_combo.addItem("校对", "proofread")
        self.strategy_combo.addItem("严格（独立多阶段）", "strict")
        form.addRow("质量处理策略", self.strategy_combo)
        self.bool_controls: dict[str, QCheckBox] = {}
        for field, label in _BOOL_FIELDS:
            control = QCheckBox(label, form_host)
            self.bool_controls[field] = control
            form.addRow(control)
        self.scope_combo = QComboBox(form_host)
        for label, value in (("全部", "all"), ("仅通过检测", "passed"), ("仅有问题", "has_issues")):
            self.scope_combo.addItem(label, value)
        form.addRow("润色范围", self.scope_combo)
        self.level_combo = QComboBox(form_host)
        for label, value in (("轻度", "light"), ("适中", "moderate"), ("增强", "aggressive")):
            self.level_combo.addItem(label, value)
        form.addRow("润色强度", self.level_combo)
        self.batch_controls: dict[str, QSpinBox] = {}
        for field, label in _BATCH_FIELDS:
            control = QSpinBox(form_host)
            control.setRange(1, 10_000)
            self.batch_controls[field] = control
            form.addRow(label, control)
        self.order_combo = QComboBox(form_host)
        self.order_combo.addItem("串行", "serial")
        self.order_combo.addItem("并行", "parallel")
        self.order_combo.setCurrentIndex(
            max(0, self.order_combo.findData(str(getattr(config, "mixed_execution_order", "serial"))))
        )
        form.addRow("混合模式执行顺序", self.order_combo)
        root.addWidget(form_host)
        root.addStretch(1)
        self._render_profile()

    def _switch_preset(self) -> None:
        if self._rendering:
            return
        self._capture_profile()
        self._active = str(self.preset_combo.currentData())
        self._render_profile()

    def _capture_profile(self) -> None:
        profile = self._profiles[self._active]
        profile["pp_strategy"] = str(self.strategy_combo.currentData())
        for field, control in self.bool_controls.items():
            profile[field] = control.isChecked()
        profile["pp_polish_scope"] = str(self.scope_combo.currentData())
        profile["pp_polish_level"] = str(self.level_combo.currentData())
        for field, control in self.batch_controls.items():
            profile[field] = control.value()

    def _render_profile(self) -> None:
        self._rendering = True
        profile = self._profiles[self._active]
        self.strategy_combo.setCurrentIndex(
            max(0, self.strategy_combo.findData(profile.get("pp_strategy", "proofread")))
        )
        for field, control in self.bool_controls.items():
            control.setChecked(bool(profile.get(field, False)))
        self.scope_combo.setCurrentIndex(max(0, self.scope_combo.findData(profile.get("pp_polish_scope", "all"))))
        self.level_combo.setCurrentIndex(max(0, self.level_combo.findData(profile.get("pp_polish_level", "moderate"))))
        for field, control in self.batch_controls.items():
            control.setValue(int(profile.get(field, 1)))
        self._rendering = False

    def apply_to_draft(self) -> None:
        self._capture_profile()
        self._config.workflow_profiles = deepcopy(self._profiles)
        apply_profile_settings(self._config, "translate")
        apply_if_present(self._config, "target_lang", str(self.target_lang_combo.currentData() or "zh_CN"))
        apply_if_present(self._config, "mixed_execution_order", str(self.order_combo.currentData()))


__all__ = ["AiDefaultsPage"]
