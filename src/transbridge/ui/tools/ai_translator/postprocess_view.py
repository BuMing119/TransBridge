"""Post-process form construction for the AI translator."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from transbridge.ui.tools.ai_translator.view_controls import TranslatorViewOwner

from .task_widget_style import configure_task_input, configure_task_panel, configure_task_title


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
        "标准：一次请求同时纠错、处理术语并优化表达\n严格：按下方开关分别执行检测、修复、润色和裁决"
    )
    strategy_row.addWidget(view.controls.pp_strategy_combo, 1)
    pp_layout.addLayout(strategy_row)

    stages = QGridLayout()
    stages.setSpacing(10)
    detection = QGroupBox("质量检测")
    configure_task_panel(detection)
    detection_layout = QVBoxLayout(detection)
    view.controls.pp_consistency_check = QCheckBox("术语一致性检查")
    view.controls.pp_consistency_check.setChecked(True)
    view.controls.pp_consistency_check.setToolTip("检查译文是否使用了术语表中的标准译法")
    detection_layout.addWidget(view.controls.pp_consistency_check)

    view.controls.pp_format_check = QCheckBox("格式验证（占位符、标签、引号等）")
    view.controls.pp_format_check.setChecked(True)
    view.controls.pp_format_check.setToolTip("检查译文是否保留了原文的占位符、格式标记和引号闭合")
    detection_layout.addWidget(view.controls.pp_format_check)

    view.controls.pp_quality_gate_check = QCheckBox("LLM 质量检测")
    view.controls.pp_quality_gate_check.setChecked(True)
    view.controls.pp_quality_gate_check.setToolTip("使用 LLM 评估译文质量，识别漏翻、错翻等问题")
    detection_layout.addWidget(view.controls.pp_quality_gate_check)
    detection_layout.addStretch(1)
    stages.addWidget(detection, 0, 0)

    refinement = QGroupBox("修复与润色")
    configure_task_panel(refinement)
    refinement_layout = QVBoxLayout(refinement)
    view.controls.pp_refinement_check = QCheckBox("LLM 自动修复")
    view.controls.pp_refinement_check.setChecked(True)
    view.controls.pp_refinement_check.setToolTip("对检测出的问题使用 LLM 进行自动修复")
    refinement_layout.addWidget(view.controls.pp_refinement_check)

    view.controls.pp_polish_check = QCheckBox("润色优化（需要额外 LLM 调用）")
    view.controls.pp_polish_check.setChecked(False)
    view.controls.pp_polish_check.setToolTip("对译文进行流畅度和风格优化，显著提升翻译质量但消耗更多 API 调用")
    refinement_layout.addWidget(view.controls.pp_polish_check)

    view.controls.pp_polish_scope_combo = QComboBox()
    configure_task_input(view.controls.pp_polish_scope_combo)
    view.controls.pp_polish_scope_combo.addItems(["全部条目", "仅通过检测的条目", "仅有问题需修复的条目"])
    view.controls.pp_polish_scope_combo.setToolTip(
        "全部: 润色所有译文\n仅通过: 只润色没有问题的译文\n仅问题: 只润色修复后的译文"
    )
    refinement_layout.addWidget(view.controls.pp_polish_scope_combo)

    view.controls.pp_polish_level_combo = QComboBox()
    configure_task_input(view.controls.pp_polish_level_combo)
    view.controls.pp_polish_level_combo.addItems(["轻微（仅修正明显错误）", "适中（平衡优化）", "深度（追求最佳表达）"])
    view.controls.pp_polish_level_combo.setToolTip("轻微: 保守润色\n适中: 适度优化\n深度: 深度改写追求最佳表达")
    refinement_layout.addWidget(view.controls.pp_polish_level_combo)

    view.controls.polish_preview_check = QCheckBox("润色后预览确认（逐条对比接受/拒绝）")
    view.controls.polish_preview_check.setChecked(False)
    view.controls.polish_preview_check.setToolTip(
        "勾选后，独立润色完成后弹出预览窗口，可逐条对比并选择接受或拒绝润色结果"
    )
    refinement_layout.addWidget(view.controls.polish_preview_check)
    stages.addWidget(refinement, 0, 1)

    arbitration = QGroupBox("质量裁决")
    configure_task_panel(arbitration)
    arbitration_layout = QVBoxLayout(arbitration)
    view.controls.pp_arbitration_check = QCheckBox("LLM 质量裁决")
    view.controls.pp_arbitration_check.setChecked(True)
    view.controls.pp_arbitration_check.setToolTip("对修复/润色后的译文进行最终质量裁决（通过/打回/待审）")
    arbitration_layout.addWidget(view.controls.pp_arbitration_check)

    view.controls.pp_strict_mode_check = QCheckBox("严格模式（质量存疑时直接打回而非标记待审）")
    view.controls.pp_strict_mode_check.setChecked(False)
    view.controls.pp_strict_mode_check.setToolTip("严格模式下，不确定质量的译文会被打回重翻而非保留待审")
    arbitration_layout.addWidget(view.controls.pp_strict_mode_check)
    arbitration_layout.addStretch(1)
    stages.addWidget(arbitration, 0, 2)
    stages.setColumnStretch(0, 1)
    stages.setColumnStretch(1, 1)
    stages.setColumnStretch(2, 1)
    pp_layout.addLayout(stages)

    pp_note = QLabel("<i>校对策略使用单一校对阶段；下方独立阶段只在严格策略中生效</i>")
    configure_task_title(pp_note, "hint")
    pp_note.setAccessibleName("后处理提示")
    pp_note.setWordWrap(True)
    pp_layout.addWidget(pp_note)

    tab_pp = QWidget()
    tab_pp_layout = QVBoxLayout(tab_pp)
    tab_pp_layout.setSpacing(6)
    tab_pp_layout.addWidget(view.controls.pp_box)
    tab_pp_layout.addStretch()
    if attach:
        view.controls.tabs.addTab(tab_pp, "后处理")
    return tab_pp
