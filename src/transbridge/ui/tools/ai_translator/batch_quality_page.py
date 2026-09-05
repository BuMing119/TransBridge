"""Quality-processing options for a batch AI translation task."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QComboBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from transbridge.ui.foundation.components import ComponentKind, ComponentStyle


class BatchQualityPage(QWidget):
    changed = pyqtSignal()

    def __init__(self, config: object, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        title = QLabel("质量检查与优化", self)
        title.setProperty("tbTaskSectionTitle", True)
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)
        self.enabled = QCheckBox("启用质量检查与优化", self)
        self.enabled.setChecked(bool(getattr(config, "enable_post_process", True)))
        layout.addWidget(self.enabled)

        self.strategy = QComboBox(self)
        ComponentStyle.apply_static(self.strategy, ComponentKind.INPUT)
        self.strategy.setAccessibleName("质量处理策略")
        self.strategy.addItem("校对", "proofread")
        self.strategy.addItem("严格（独立多阶段）", "strict")
        self.strategy.setCurrentIndex(max(0, self.strategy.findData(getattr(config, "pp_strategy", "proofread"))))
        strategy_row = QHBoxLayout()
        strategy_row.addWidget(QLabel("质量处理策略", self))
        strategy_row.addWidget(self.strategy, 1)
        layout.addLayout(strategy_row)

        grid = QGridLayout()
        grid.setSpacing(10)
        detection = QGroupBox("质量检测", self)
        detection.setProperty("tbTaskPanel", True)
        detection_layout = QVBoxLayout(detection)
        self.consistency = self._check("术语一致性检查", config, "pp_enable_consistency_check", True)
        self.format_validation = self._check("格式验证", config, "pp_enable_format_validation", True)
        self.quality_gate = self._check("LLM 质量检测", config, "pp_enable_quality_gate", True)
        detection_layout.addWidget(self.consistency)
        detection_layout.addWidget(self.format_validation)
        detection_layout.addWidget(self.quality_gate)
        grid.addWidget(detection, 0, 0)

        refinement = QGroupBox("修复与润色", self)
        refinement.setProperty("tbTaskPanel", True)
        refinement_layout = QVBoxLayout(refinement)
        self.refinement = self._check("LLM 自动修复", config, "pp_enable_refinement", True)
        self.polish = self._check("润色优化", config, "pp_enable_polish", False)
        self.polish_level = QComboBox(self)
        ComponentStyle.apply_static(self.polish_level, ComponentKind.INPUT)
        self.polish_level.setAccessibleName("润色强度")
        self.polish_level.addItem("轻微", "light")
        self.polish_level.addItem("适中", "moderate")
        self.polish_level.addItem("深度", "aggressive")
        self.polish_level.setCurrentIndex(
            max(0, self.polish_level.findData(getattr(config, "pp_polish_level", "moderate")))
        )
        self.polish_scope = QComboBox(self)
        ComponentStyle.apply_static(self.polish_scope, ComponentKind.INPUT)
        self.polish_scope.setAccessibleName("润色范围")
        self.polish_scope.addItem("全部条目", "all")
        self.polish_scope.addItem("仅通过检测的条目", "passed")
        self.polish_scope.addItem("仅有问题需修复的条目", "has_issues")
        self.polish_scope.setCurrentIndex(max(0, self.polish_scope.findData(getattr(config, "pp_polish_scope", "all"))))
        refinement_layout.addWidget(self.refinement)
        refinement_layout.addWidget(self.polish)
        refinement_layout.addWidget(self.polish_scope)
        refinement_layout.addWidget(self.polish_level)
        grid.addWidget(refinement, 0, 1)

        arbitration = QGroupBox("质量裁决", self)
        arbitration.setProperty("tbTaskPanel", True)
        arbitration_layout = QVBoxLayout(arbitration)
        self.arbitration = self._check("LLM 质量裁决", config, "pp_enable_arbitration", True)
        self.strict_arbitration = self._check("严格模式", config, "pp_strict_arbitration", False)
        arbitration_layout.addWidget(self.arbitration)
        arbitration_layout.addWidget(self.strict_arbitration)
        arbitration_layout.addStretch(1)
        grid.addWidget(arbitration, 0, 2)
        layout.addLayout(grid)

        note = QLabel("启用 LLM 检测、修复、润色或裁决会增加耗时与 API 调用。", self)
        note.setProperty("tbTaskHint", True)
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)

        self.enabled.toggled.connect(self._update_enabled)
        self.strategy.currentIndexChanged.connect(self._update_enabled)
        self.polish.toggled.connect(self._update_enabled)
        self.arbitration.toggled.connect(self._update_enabled)
        self._update_enabled()

    def apply_to(self, config: object) -> None:
        config.enable_post_process = self.enabled.isChecked()
        config.pp_strategy = str(self.strategy.currentData() or "proofread")
        config.pp_enable_consistency_check = self.consistency.isChecked()
        config.pp_enable_format_validation = self.format_validation.isChecked()
        config.pp_enable_quality_gate = self.quality_gate.isChecked()
        config.pp_enable_refinement = self.refinement.isChecked()
        config.pp_enable_polish = self.polish.isChecked()
        config.pp_polish_scope = str(self.polish_scope.currentData() or "all")
        config.pp_polish_level = str(self.polish_level.currentData() or "moderate")
        config.pp_enable_arbitration = self.arbitration.isChecked()
        config.pp_strict_arbitration = self.strict_arbitration.isChecked()

    def _update_enabled(self, *_args) -> None:
        enabled = self.enabled.isChecked()
        strict = enabled and self.strategy.currentData() == "strict"
        for control in (
            self.consistency,
            self.format_validation,
            self.quality_gate,
            self.refinement,
            self.polish,
            self.arbitration,
        ):
            control.setEnabled(strict)
        self.polish_level.setEnabled(strict and self.polish.isChecked())
        self.polish_scope.setEnabled(strict and self.polish.isChecked())
        self.strict_arbitration.setEnabled(strict and self.arbitration.isChecked())
        self.changed.emit()

    def _check(self, text: str, config: object, field: str, default: bool) -> QCheckBox:
        control = QCheckBox(text, self)
        control.setChecked(bool(getattr(config, field, default)))
        control.toggled.connect(self.changed.emit)
        return control


__all__ = ["BatchQualityPage"]
