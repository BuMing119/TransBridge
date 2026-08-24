"""Post-process form construction for the AI translator."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from transbridge.ui.tools.ai_translator.view_controls import TranslatorViewOwner


def build_postprocess_view(view: TranslatorViewOwner) -> None:
    # ── 后处理配置区 ───────────────────────────────────────────────────────
    view.controls.pp_box = QGroupBox("后处理配置")
    pp_layout = QVBoxLayout(view.controls.pp_box)
    pp_layout.setSpacing(6)

    # 总开关
    view.controls.pp_enable_check = QCheckBox("启用翻译后质量检查与优化")
    view.controls.pp_enable_check.setChecked(True)
    view.controls.pp_enable_check.setToolTip("启用后将对翻译结果进行质量检查、修复和润色，可能增加额外耗时和API调用")
    pp_layout.addWidget(view.controls.pp_enable_check)

    # 分隔线
    line1 = QFrame()
    line1.setFrameShape(QFrame.Shape.HLine)
    line1.setStyleSheet("color: #ccc;")
    pp_layout.addWidget(line1)

    # 阶段1: 检测
    detect_label = QLabel("<b>阶段1: 质量检测</b>")
    pp_layout.addWidget(detect_label)

    view.controls.pp_consistency_check = QCheckBox("术语一致性检查")
    view.controls.pp_consistency_check.setChecked(True)
    view.controls.pp_consistency_check.setToolTip("检查译文是否使用了术语表中的标准译法")
    pp_layout.addWidget(view.controls.pp_consistency_check)

    view.controls.pp_format_check = QCheckBox("格式验证（占位符、标签、引号等）")
    view.controls.pp_format_check.setChecked(True)
    view.controls.pp_format_check.setToolTip("检查译文是否保留了原文的占位符、格式标记和引号闭合")
    pp_layout.addWidget(view.controls.pp_format_check)

    view.controls.pp_quality_gate_check = QCheckBox("LLM质量检测")
    view.controls.pp_quality_gate_check.setChecked(True)
    view.controls.pp_quality_gate_check.setToolTip("使用LLM评估译文质量，识别漏翻、错翻等问题")
    pp_layout.addWidget(view.controls.pp_quality_gate_check)

    # 分隔线
    line2 = QFrame()
    line2.setFrameShape(QFrame.Shape.HLine)
    line2.setStyleSheet("color: #ccc;")
    pp_layout.addWidget(line2)

    # 阶段2: 修复与润色
    refine_label = QLabel("<b>阶段2: 修复与润色</b>")
    pp_layout.addWidget(refine_label)

    view.controls.pp_refinement_check = QCheckBox("启用LLM自动修复")
    view.controls.pp_refinement_check.setChecked(True)
    view.controls.pp_refinement_check.setToolTip("对检测出的问题使用LLM进行自动修复")
    pp_layout.addWidget(view.controls.pp_refinement_check)

    view.controls.pp_polish_check = QCheckBox("启用润色优化（需要额外LLM调用）")
    view.controls.pp_polish_check.setChecked(False)
    view.controls.pp_polish_check.setToolTip("对译文进行流畅度和风格优化，显著提升翻译质量但消耗更多API调用")
    pp_layout.addWidget(view.controls.pp_polish_check)

    # 润色选项子布局
    polish_options = QHBoxLayout()
    polish_options.addSpacing(20)

    polish_options.addWidget(QLabel("润色范围:"))
    view.controls.pp_polish_scope_combo = QComboBox()
    view.controls.pp_polish_scope_combo.addItems(["全部条目", "仅通过检测的条目", "仅有问题需修复的条目"])
    view.controls.pp_polish_scope_combo.setToolTip(
        "全部: 润色所有译文\n仅通过: 只润色没有问题的译文\n仅问题: 只润色修复后的译文"
    )
    polish_options.addWidget(view.controls.pp_polish_scope_combo)

    polish_options.addSpacing(10)
    polish_options.addWidget(QLabel("润色强度:"))
    view.controls.pp_polish_level_combo = QComboBox()
    view.controls.pp_polish_level_combo.addItems(["轻微（仅修正明显错误）", "适中（平衡优化）", "深度（追求最佳表达）"])
    view.controls.pp_polish_level_combo.setToolTip("轻微: 保守润色\n适中: 适度优化\n深度: 深度改写追求最佳表达")
    polish_options.addWidget(view.controls.pp_polish_level_combo)

    polish_options.addStretch()
    pp_layout.addLayout(polish_options)

    # 润色预览确认
    view.controls.polish_preview_check = QCheckBox("润色后预览确认（逐条对比接受/拒绝）")
    view.controls.polish_preview_check.setChecked(False)
    view.controls.polish_preview_check.setToolTip(
        "勾选后，独立润色完成后弹出预览窗口，可逐条对比并选择接受或拒绝润色结果"
    )
    pp_layout.addWidget(view.controls.polish_preview_check)

    # 分隔线
    line3 = QFrame()
    line3.setFrameShape(QFrame.Shape.HLine)
    line3.setStyleSheet("color: #ccc;")
    pp_layout.addWidget(line3)

    # 阶段3: 裁决
    arbitrate_label = QLabel("<b>阶段3: 质量裁决</b>")
    pp_layout.addWidget(arbitrate_label)

    view.controls.pp_arbitration_check = QCheckBox("启用LLM质量裁决")
    view.controls.pp_arbitration_check.setChecked(True)
    view.controls.pp_arbitration_check.setToolTip("对修复/润色后的译文进行最终质量裁决（通过/打回/待审）")
    pp_layout.addWidget(view.controls.pp_arbitration_check)

    view.controls.pp_strict_mode_check = QCheckBox("严格模式（质量存疑时直接打回而非标记待审）")
    view.controls.pp_strict_mode_check.setChecked(False)
    view.controls.pp_strict_mode_check.setToolTip("严格模式下，不确定质量的译文会被打回重翻而非保留待审")
    pp_layout.addWidget(view.controls.pp_strict_mode_check)

    # 备注说明
    pp_note = QLabel("<i>提示：润色会在修复后执行，最终译文优先采用润色结果</i>")
    pp_note.setStyleSheet("color: #888; font-size: 11px;")
    pp_layout.addWidget(pp_note)

    # Tab 3: 后处理
    tab_pp = QWidget()
    tab_pp_layout = QVBoxLayout(tab_pp)
    tab_pp_layout.setSpacing(6)
    tab_pp_layout.addWidget(view.controls.pp_box)
    tab_pp_layout.addStretch()
    view.controls.tabs.addTab(tab_pp, "后处理")
