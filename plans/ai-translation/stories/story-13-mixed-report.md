# Story 13: 合并报告

**所属方案**: `plans/ai-translation/plan.md`
**状态**: 🚧 待编码
**对应需求**: FR5.11.6
**引用 ADR**: ADR-007

## 概述

实现合并报告生成和对话框混合模板，将翻译和润色的结果汇总到一份报告中。

## 验收标准

- [ ] `ReportGenerator.generate_mixed_report()` 生成合并 Excel
- [ ] Excel 结构：翻译-Summary/Entries/Issues/Refinements/Arbitrations + 润色-Summary/Entries/Polish
- [ ] 应用内对话框新增混合模板：顶部 Tab「翻译部分」「润色部分」，各自内含子 Tab
- [ ] 合并报告入口信号连接到 MainWindow 跳转

## 实现步骤

### 步骤 1: generate_mixed_report()
- `ReportGenerator` 新增方法：`generate_mixed_report(translate_result, polish_results, polish_entries, polish_stats) -> str | None`
- 内部调用翻译和润色的 Sheet 写入逻辑，Sheet 名加前缀「翻译-」「润色-」
- 文件命名：`{esp_stem}_mixed_report_{YYYYMMDD_HHMMSS}.xlsx`
- 涉及文件: `src/transbridge/ai_translator/post_processor/report_generator.py`

### 步骤 2: 对话框混合模板
- `_TranslationReportDialog` 新增混合模式初始化参数
- 构造函数接受 `mixed_translate_result` + `mixed_polish_data`
- QTabWidget 顶层两个 Tab：「翻译部分」「润色部分」
- 每个顶层 Tab 内含对应的子 Tab（复用现有 `_build_*_tab` 方法）
- 双击条目跳转逻辑不变
- 涉及文件: `src/transbridge/ui/tools/ai_translator/_translation_report_dialog.py`

## 涉及文件

| 文件 | 操作 |
|------|------|
| `src/transbridge/ai_translator/post_processor/report_generator.py` | 修改 |
| `src/transbridge/ui/tools/ai_translator/_translation_report_dialog.py` | 修改 |
