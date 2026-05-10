# Story 11: 三模式制+混合模式UI

**所属方案**: `plans/ai-translation/plan.md`
**状态**: 🚧 待编码
**对应需求**: FR5.11.1, FR5.11.4
**引用 ADR**: ADR-007

## 概述

将 AI 翻译窗口升级为三模式制（翻译/润色/混合），混合模式下替换作用域面板为规则映射表，增加执行顺序配置。

## 验收标准

- [ ] 顶部 RadioButton 新增「混合」，三模式并存
- [ ] 选中混合模式时，作用域面板替换为 `_RuleEditorWidget`
- [ ] 执行顺序配置：串行/并行（下拉或RadioButton）
- [ ] 切换模式时保留各自的配置状态
- [ ] 翻译/润色模式完全向后兼容，行为不变
- [ ] 覆盖策略 checkbox 在混合模式下依然可用

## 实现步骤

### 步骤 1: 新增混合 RadioButton
- `_mode_group` 新增 `_mode_mixed = QRadioButton("混合")`
- `_on_mode_changed()` 增加 `elif self._mode_mixed.isChecked():` 分支
- 涉及文件: `src/transbridge/ui/tools/ai_translator/ai_translator_window.py`

### 步骤 2: 面板切换逻辑
- 创建 `_mixed_panel`（QWidget），内含 `_RuleEditorWidget` + 执行顺序配置
- `_on_mode_changed()` 中：翻译/润色→显示现有作用域面板；混合→显示 `_mixed_panel`
- 使用 `QStackedWidget` 或 `setVisible` 切换
- 涉及文件: `src/transbridge/ui/tools/ai_translator/ai_translator_window.py`

### 步骤 3: 执行顺序配置
- 添加 `_order_combo = QComboBox(["串行（先翻译后润色）", "并行"])`
- 或两个 RadioButton
- 存入 `LLMConfig` 的 `mixed_execution_order: str = "serial"`
- 涉及文件: `src/transbridge/ui/tools/ai_translator/ai_translator_window.py`

### 步骤 4: _on_start() 混合模式分流
- 翻译模式 → 现有逻辑不变
- 润色模式 → 现有逻辑不变
- 混合模式 → 收集规则匹配结果 → 拆分为翻译条目列表和润色条目列表 → 创建 `_MixedWorker` → 启动
- 涉及文件: `src/transbridge/ui/tools/ai_translator/ai_translator_window.py`

### 步骤 5: 按钮文案与状态
- 混合模式下「开始翻译」按钮文案改为「开始执行」
- 条目数预估显示「翻译 X 条 + 润色 Y 条」
- 涉及文件: `src/transbridge/ui/tools/ai_translator/ai_translator_window.py`

## 涉及文件

| 文件 | 操作 |
|------|------|
| `src/transbridge/ui/tools/ai_translator/ai_translator_window.py` | 修改 |
| `src/transbridge/paratranz/config_manager.py` | 修改（新增 mixed_execution_order） |
