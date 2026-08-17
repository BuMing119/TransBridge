# 002: 补全逐插件翻译循环（AutoTranslator 兜底）+ 文档回写

**日期**: 2026-08-14
**类型**: 改
**关联**: Epic: FOMOD 安装包翻译流水线 > Story 03: 流水线编排

## 修改文件

### src/transbridge/fomod/pipeline.py (改)
- **修改内容**: 补全逐插件翻译循环——_translate_plugins（遍历 .esp/.esm/.esl，PluginParser 解析→migrator.migrate 键对齐→tm.apply_to_collection 词典兜底→AutoTranslator AI 翻译→PluginWriter 写回）、_ai_translate（TranslatorConfig + AutoTranslator.translate 处理 stage=0 无译文）、_write_back（SSEPluginWithContext + PluginStringsLookup + PluginWriter）。FomodPipeline 增加 llm_config/tm_manager 运行时注入，run() 增加 progress_callback/stop_event
- **原因**: 补全之前留空的「词条迁移+词典兜底+AI翻译」骨架。AI 兜底确定复用 AutoTranslator（含术语库+名词提取，翻译正确性必要环节），而非裸 LLMClient

### docs/adr/014-fomod-translation-memory.md (改)
- **修改内容**: 追加「更新: 2026-08-14 - 逐插件翻译循环与 AI 兜底入口（AutoTranslator）」节，记录逐插件循环流程 + AutoTranslator vs 裸 LLMClient 对比（术语库/名词提取/批量/后处理）+ 运行时上下文注入方式
- **原因**: 固化 AI 兜底入口决策（复用 AutoTranslator），供后续开发引用

### plans/fomod-translation/stories/story-03-pipeline.md (改)
- **修改内容**: 更新跨 Plan 依赖（补 AutoTranslator/plugin_with_context）、数据流（逐插件循环 + AutoTranslator）、验收标准（补逐插件循环/术语库/上下文注入）、关键接口（FomodPipeline 注入 llm_config/tm_manager）
- **原因**: Story 文档与实现对齐（此前写的 AI 翻译用的是裸 LLMClient，与实际 AutoTranslator 不符）

### plans/fomod-translation/plan.md (改)
- **修改内容**: Story 03 验收标准更新为逐插件循环 + AutoTranslator + 上下文注入
- **原因**: 同步方案验收标准

### tests/test_fomod_pipeline.py (增)
- **修改内容**: 3 个用例——插件扩展名集合、AI 翻译跳过已译/锁定条目、PipelineResult.to_dict 完整性
- **原因**: 覆盖 pipeline 纯逻辑部分（真实 ESP 集成需 sse-plugin-interface，不适合沙箱）