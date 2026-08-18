# AI 自动翻译

> **状态**: ✔️ 已实现（Story 1-14 全部完成）
> **模块**: `src/transbridge/ai_translator/`

## 概述

基于 LLM 的自动翻译系统，支持三轮翻译策略、多来源术语管理、向量语义检索、断点续传、流式增量写回、暂停/停止控制。

## Story 清单

| Story | 标题 | 状态 |
|-------|------|------|
| Story-01 | LLM 客户端抽象层（OpenAI + Anthropic + 流式 + 取消） | ✔️ |
| Story-02 | Prompt 构建器（TOML 模板 + 术语注入 + 响应解析） | ✔️ |
| Story-03 | 术语库管理器（四来源 + 优先级合并 + in-flight 缓存） | ✔️ |
| Story-04 | 批次规划器（三轮策略：实体→对话→长文本） | ✔️ |
| Story-05 | 翻译控制器（AutoTranslator：并发执行 + 断点续传 + 暂停/停止） | ✔️ |
| Story-06 | 专有名词抽取器（NounExtractor） | ✔️ |
| Story-07 | 向量术语检索（FAISS + 两阶段召回） | ✔️ |
| Story-08 | Embedding 客户端（本地模型 + API 双实现） | ✔️ |
| Story-09 | 组合式作用域选择器（三维度标签 + 快捷预设 + 翻译/润色自适应） | ✔️ · [详细](stories/story-09-scope-selector.md) |
| Story-10 | ActionRule模型+规则编辑器（规则映射表数据模型 + LLMConfig持久化 + _RuleEditorWidget） | ✔️ 已实现 · [详细](stories/story-10-action-rule-editor.md) |
| Story-11 | 三模式制+混合模式UI（混合RadioButton + 面板切换 + 执行顺序配置 + _on_start分流） | ✔️ 已实现 · [详细](stories/story-11-mixed-mode-ui.md) |
| Story-12 | MixedWorker+统一进度窗口（_MixedWorker统一调度 + 双进度条 + 失败隔离） | ✔️ 已实现 · [详细](stories/story-12-mixed-worker.md) |
| Story-13 | 合并报告（generate_mixed_report + 对话框混合模板） | ✔️ 已实现 · [详细](stories/story-13-mixed-report.md) |
| Story-14 | 冲突处理+集成收尾（后处理润色禁用 + 空作用域提示 + 全链路集成） | ✔️ 已实现 · [详细](stories/story-14-integration-polish.md) |

## 关键文件

- `src/transbridge/ai_translator/translator.py` — AutoTranslator, TranslatorConfig, ProgressCheckpoint
- `src/transbridge/ai_translator/llm_client.py` — LLMClient, OpenAICompatibleClient, AnthropicClient
- `src/transbridge/ai_translator/prompt_builder.py` — PromptBuilder
- `src/transbridge/ai_translator/term_database.py` — TermDatabaseManager, DynamicTermDatabase
- `src/transbridge/ai_translator/batch_planner.py` — BatchPlanner, Batch, BatchPlan
- `src/transbridge/ai_translator/noun_extractor.py` — NounExtractor
- `src/transbridge/ai_translator/term_vector_index.py` — TermVectorIndex, VectorSearchResult
- `src/transbridge/ai_translator/embedding_client.py` — EmbeddingClient, create_embedding_client

## 相关 ADR

- [ADR-003: 三轮 AI 翻译策略](../../docs/adr/003-three-round-translation-strategy.md)
- [ADR-005: TOML Prompt 模板](../../docs/adr/005-toml-prompt-no-langchain.md)

---

## Story-09: 组合式作用域选择器

**对应需求**: FR5.10.1 ~ FR5.10.6  
**状态**: ✔️ 已实现  
**验收标准**:
- [ ] 3 个 RadioButton 替换为组合式作用域面板（翻译状态 + 标记 + 分类三维度标签）
- [ ] 快捷预设按钮（全部未翻译/已翻译条目/当前主表视图）
- [ ] 翻译模式默认选中「未翻译」标签，润色模式默认选中「已翻译」标签
- [ ] 覆盖策略复选框保留现有行为
- [ ] `_update_estimate` 按三维度筛选实时显示预估条目数
- [ ] 不再调用 `get_selected_entries()`，与主表标记系统完全解耦

**实现步骤**:
1. 移除 `_scope_all`/`_scope_filtered`/`_scope_selected` 三个 RadioButton 及 `_scope_group`；新建作用域面板区域，包含三个维度标签行（翻译状态、标记、分类），复用 `_TAG_NORMAL/_TAG_ACTIVE` 样式和 `_build_*_tags` 模式 → 涉及文件: `src/transbridge/ui/tools/ai_translator/ai_translator_window.py`
2. 添加快捷预设按钮行（「全部未翻译」「已翻译条目」「当前主表视图」），点击即设置对应维度标签选中状态 → 涉及文件: `src/transbridge/ui/tools/ai_translator/ai_translator_window.py`
3. 翻译/润色模式切换时自动调整默认值：翻译模式→状态默认选中「未翻译」；润色模式→状态默认选中「已翻译」→ 涉及文件: `src/transbridge/ui/tools/ai_translator/ai_translator_window.py`
4. 重写 `_update_estimate()`：通过 `_ctx.collection` 获取全量条目，按三维度筛选（翻译状态 AND 标记 AND 分类）计算候选条目，调用 BatchPlanner.plan() 预估 → 涉及文件: `src/transbridge/ui/tools/ai_translator/ai_translator_window.py`
5. 移除 `get_selected_entries()` 调用，翻译/润色真正执行时从作用域面板的筛选条件构建候选条目列表 → 涉及文件: `src/transbridge/ui/tools/ai_translator/ai_translator_window.py`

## 综合整改状态增量（2026-08-18）

- `partially-verified`：保留 14 Story 历史交付；MixedWorker 构造、上下文分配、RunSpec、取消与唯一提交点未通过本轮成功链。
- `blocked_by`：`unified-task-translation-runtime-v2` S03～S05/S07、`translation-io-kernel-v2` S02/S05、`release-hardening-v2` S02/S03。
- `superseded_by`：worker 直接修改 Collection 与独立 checkpoint 生命周期由 CandidateSet、TaskRuntime 和 CheckpointPort 取代；翻译策略资产保留。
