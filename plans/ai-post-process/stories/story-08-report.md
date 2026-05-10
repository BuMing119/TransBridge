# Story 08: 后处理报告生成

**所属方案**: `plans/ai-post-process/plan.md`
**状态**: ✔️ 已实现

## 概述

后处理完成后自动生成结构化 Excel 报告（.xlsx），帮助用户了解译文质量状况和追踪修改细节。

## 关键设计

- **多 Sheet 结构**: Summary（汇总配置/统计）+ Entries（条目明细）+ Issues（问题明细）+ Refinements（修复记录）+ Arbitrations（裁决记录）
- **Summary Sheet**: total_checked, issue_count, passed/rejected/pending, refined_count, polished_count, config_snapshot, timestamp
- **Entries Sheet**: entry_id, original, initial_translation, refined_translation, polished_translation, final_translation, stage, verdict, confidence
- **openpyxl**: 使用项目已有依赖生成 .xlsx 文件，无需额外安装

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/ai_translator/post_processor/post_process_report.py` | 报告生成器 |
