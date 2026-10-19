"""Post-process form construction for the AI translator."""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QSize
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLayout,
    QVBoxLayout,
    QWidget,
)

from transbridge.ui.tools.ai_translator.view_controls import TranslatorViewOwner

from .task_widget_style import configure_task_input, configure_task_panel, configure_task_title


class _QualityStages(QWidget):
    """Keep strict workflow stages readable in one, two or three columns."""

    def __init__(self, panels: tuple[QGroupBox, ...]):
        super().__init__()
        self._panels = panels
        self._columns = 0
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(10)
        self._grid.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self._reflow(0)

    def minimumSizeHint(self) -> QSize:
        size = self._grid.minimumSize()
        size.setWidth(max(panel.minimumSizeHint().width() for panel in self._panels))
        return size

    def resizeEvent(self, event) -> None:
        self._reflow(event.size().width())
        super().resizeEvent(event)

    def event(self, event) -> bool:
        result = super().event(event)
        if event.type() == QEvent.Type.LayoutRequest and hasattr(self, "_grid"):
            self._reflow(self.width())
        return result

    def _reflow(self, width: int) -> None:
        panel_width = max(panel.minimumSizeHint().width() for panel in self._panels)
        spacing = self._grid.spacing()
        columns = max(1, min(len(self._panels), (width + spacing) // max(1, panel_width + spacing)))
        if columns == self._columns:
            return
        self._columns = columns
        for panel in self._panels:
            self._grid.removeWidget(panel)
        for column in range(len(self._panels)):
            self._grid.setColumnStretch(column, int(column < columns))
        for index, panel in enumerate(self._panels):
            self._grid.addWidget(panel, index // columns, index % columns)
        self.updateGeometry()


def build_postprocess_view(view: TranslatorViewOwner, *, attach: bool = True) -> QWidget:
    """Build the shared three-stage quality surface without changing control contracts."""

    view.controls.pp_box = QGroupBox("质量检查与优化")
    configure_task_panel(view.controls.pp_box)
    pp_layout = QVBoxLayout(view.controls.pp_box)
    pp_layout.setSpacing(10)

    view.controls.pp_enable_check = QCheckBox("启用质量检查与优化")
    view.controls.pp_enable_check.setChecked(True)
    view.controls.pp_enable_check.setToolTip("启用后将对翻译结果进行质量检查、修复和润色，可能增加额外耗时和 API 调用")
    pp_layout.addWidget(view.controls.pp_enable_check)

    strategy_row = QHBoxLayout()
    strategy_row.addWidget(QLabel("质量处理策略"))
    view.controls.pp_strategy_combo = QComboBox()
    configure_task_input(view.controls.pp_strategy_combo)
    view.controls.pp_strategy_combo.addItems(["校对", "严格（独立多阶段）"])
    view.controls.pp_strategy_combo.setToolTip(
        "校对：一次请求同时纠错、处理术语并优化表达\n严格：按下方开关分别执行检测、修复、润色和裁决"
    )
    strategy_row.addWidget(view.controls.pp_strategy_combo, 1)
    pp_layout.addLayout(strategy_row)

    view.controls.pp_strategy_note = QLabel("校对会在同一阶段纠错、统一术语并优化表达；可调整下方润色强度。")
    configure_task_title(view.controls.pp_strategy_note, "hint")
    view.controls.pp_strategy_note.setAccessibleName("质量处理策略说明")
    view.controls.pp_strategy_note.setWordWrap(True)
    pp_layout.addWidget(view.controls.pp_strategy_note)

    view.controls.pp_stages = _build_strict_stages(view)
    view.controls.pp_stages.hide()
    pp_layout.addWidget(view.controls.pp_stages)
    pp_layout.addWidget(_build_polish_options(view))

    tab_pp = QWidget()
    tab_pp_layout = QVBoxLayout(tab_pp)
    tab_pp_layout.setSpacing(6)
    tab_pp_layout.addWidget(view.controls.pp_box)
    tab_pp_layout.addStretch()
    if attach:
        view.controls.tabs.addTab(tab_pp, "后处理")
    return tab_pp


def _build_strict_stages(view: TranslatorViewOwner) -> QWidget:
    detection = QGroupBox("质量检测")
    configure_task_panel(detection)
    detection_layout = QVBoxLayout(detection)
    view.controls.pp_consistency_check = QCheckBox("术语一致性检查")
    view.controls.pp_consistency_check.setChecked(True)
    view.controls.pp_consistency_check.setToolTip("检查译文是否使用了术语表中的标准译法")
    detection_layout.addWidget(view.controls.pp_consistency_check)

    view.controls.pp_format_check = QCheckBox("格式与标记验证")
    view.controls.pp_format_check.setChecked(True)
    view.controls.pp_format_check.setToolTip("检查译文是否保留了原文的占位符、格式标记和引号闭合")
    detection_layout.addWidget(view.controls.pp_format_check)

    view.controls.pp_quality_gate_check = QCheckBox("模型质量检测")
    view.controls.pp_quality_gate_check.setChecked(True)
    view.controls.pp_quality_gate_check.setToolTip("使用 LLM 评估译文质量，识别漏翻、错翻等问题")
    detection_layout.addWidget(view.controls.pp_quality_gate_check)
    detection_layout.addStretch(1)

    refinement = QGroupBox("修复与润色")
    configure_task_panel(refinement)
    refinement_layout = QVBoxLayout(refinement)
    view.controls.pp_refinement_check = QCheckBox("自动修复问题")
    view.controls.pp_refinement_check.setChecked(True)
    view.controls.pp_refinement_check.setToolTip("对检测出的问题使用 LLM 进行自动修复")
    refinement_layout.addWidget(view.controls.pp_refinement_check)

    view.controls.pp_polish_check = QCheckBox("额外润色优化")
    view.controls.pp_polish_check.setChecked(False)
    view.controls.pp_polish_check.setToolTip("对译文进行流畅度和风格优化，显著提升翻译质量但消耗更多 API 调用")
    refinement_layout.addWidget(view.controls.pp_polish_check)
    refinement_layout.addStretch(1)

    arbitration = QGroupBox("质量裁决")
    configure_task_panel(arbitration)
    arbitration_layout = QVBoxLayout(arbitration)
    view.controls.pp_arbitration_check = QCheckBox("模型质量裁决")
    view.controls.pp_arbitration_check.setChecked(True)
    view.controls.pp_arbitration_check.setToolTip("对修复/润色后的译文进行最终质量裁决（通过/打回/待审）")
    arbitration_layout.addWidget(view.controls.pp_arbitration_check)

    view.controls.pp_strict_mode_check = QCheckBox("质量存疑时打回重翻")
    view.controls.pp_strict_mode_check.setChecked(False)
    view.controls.pp_strict_mode_check.setToolTip("严格模式下，不确定质量的译文会被打回重翻而非保留待审")
    arbitration_layout.addWidget(view.controls.pp_strict_mode_check)
    arbitration_layout.addStretch(1)
    return _QualityStages((detection, refinement, arbitration))


def _build_polish_options(view: TranslatorViewOwner) -> QGroupBox:
    group = QGroupBox("润色与结果确认")
    configure_task_panel(group)
    layout = QVBoxLayout(group)
    form = QFormLayout()
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    view.controls.pp_polish_scope_label = QLabel("额外润色范围")
    view.controls.pp_polish_scope_combo = QComboBox()
    configure_task_input(view.controls.pp_polish_scope_combo)
    view.controls.pp_polish_scope_combo.addItems(["全部条目", "仅通过检测的条目", "仅有问题需修复的条目"])
    view.controls.pp_polish_scope_combo.setToolTip(
        "全部: 润色所有译文\n仅通过: 只润色没有问题的译文\n仅问题: 只润色修复后的译文"
    )
    view.controls.pp_polish_scope_label.setBuddy(view.controls.pp_polish_scope_combo)
    form.addRow(view.controls.pp_polish_scope_label, view.controls.pp_polish_scope_combo)
    view.controls.pp_polish_scope_label.hide()
    view.controls.pp_polish_scope_combo.hide()
    view.controls.pp_polish_level_combo = QComboBox()
    configure_task_input(view.controls.pp_polish_level_combo)
    view.controls.pp_polish_level_combo.addItems(["轻微（仅修正明显错误）", "适中（平衡优化）", "深度（追求最佳表达）"])
    view.controls.pp_polish_level_combo.setToolTip("轻微: 保守润色\n适中: 适度优化\n深度: 深度改写追求最佳表达")
    form.addRow("润色强度", view.controls.pp_polish_level_combo)
    layout.addLayout(form)
    view.controls.polish_preview_check = QCheckBox("应用前逐条确认候选")
    view.controls.polish_preview_check.setChecked(False)
    view.controls.polish_preview_check.setToolTip(
        "勾选后，独立润色完成后弹出预览窗口，可逐条对比并选择采用候选或保留原译文"
    )
    layout.addWidget(view.controls.polish_preview_check)
    return group
