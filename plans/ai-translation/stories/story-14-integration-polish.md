# Story 14: 冲突处理+集成收尾

**所属方案**: `plans/ai-translation/plan.md`
**状态**: 🚧 待编码
**对应需求**: FR5.11.7, FR5.11.9
**引用 ADR**: ADR-007

## 概述

实现后处理润色阶段在混合模式下的自动禁用、空作用域处理，以及全链路集成收尾。

## 验收标准

- [ ] 混合模式下翻译后处理的「润色」阶段自动禁用（checkbox 置灰+提示文字）
- [ ] 翻译条目数为 0 时跳过翻译仅执行润色
- [ ] 润色条目数为 0 时跳过润色仅执行翻译
- [ ] 两部分均为 0 时弹出提示「当前筛选条件下无匹配条目」
- [ ] 全链路集成：规则配置 → 执行 → 进度 → 报告 端到端可工作
- [ ] 翻译/润色独立模式完全不受影响

## 实现步骤

### 步骤 1: 后处理润色禁用
- `_on_start()` 混合模式分支中，创建 `PostProcessorConfig` 后检查 `self._mode_mixed.isChecked()`
- 若混合模式：`pp_config.enable_polish = False`
- UI 同步：后处理 Tab 中润色相关控件 `setEnabled(False)` + tooltip 提示
- 涉及文件: `src/transbridge/ui/tools/ai_translator/ai_translator_window.py`

### 步骤 2: 空作用域处理
- `_on_start()` 混合模式中，规则匹配后检查 `translate_entries` 和 `polish_entries` 是否为空
- 两者都为空 → `QMessageBox.warning("当前筛选条件下无匹配条目，请调整作用域")`
- 仅翻译为空 → 跳过翻译，创建仅含润色的 MixedWorker（或直接调用 _PolishWorker）
- 仅润色为空 → 跳过润色，创建仅含翻译的 MixedWorker（或直接调用 _TranslationWorker）
- 涉及文件: `src/transbridge/ui/tools/ai_translator/ai_translator_window.py`

### 步骤 3: 全链路集成验证
- 确保规则配置 → `apply_rules()` → 条目分流 → `_MixedWorker` 启动 → 进度窗口 → 报告生成 全链路通畅
- 验证串行/并行两种执行顺序
- 验证暂停/停止/后台运行
- 涉及文件: 上述所有文件

## 涉及文件

| 文件 | 操作 |
|------|------|
| `src/transbridge/ui/tools/ai_translator/ai_translator_window.py` | 修改 |
