# Story 09: AI 翻译浮动窗口

**所属方案**: `plans/ui-workbench/plan.md`
**状态**: ✔️ 已实现

## 概述

AI 翻译配置的浮动工具窗口。QTabWidget 三标签页布局：LLM 与模型 / 术语库 / 后处理。窗口高度 520px，避免超出屏幕。

## 关键设计

- **QTabWidget 三标签页**: LLM 与模型（provider/model/API 配置/Embedding 配置）/ 术语库（四来源配置+优先级）/ 后处理（14 字段开关+参数）
- **翻译范围**: 当前插件/全部插件选择
- **常驻可见**: 翻译范围选择和开始翻译按钮不受 Tab 切换影响
- **配置持久化**: 修改后保存到 LLMConfig（data/paratranz_config.ini [llm] section）

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/ui/tools/ai_translator/ai_translator_window.py` | AITranslatorWindow |
