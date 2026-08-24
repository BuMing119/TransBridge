# AI 自动翻译

> **状态**: Story 1-16 已实现
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
| Story-15 | 翻译 Prompt 结构化与供应商缓存命中优化 | ✔️ 已实现 · [详细](stories/story-15-prompt-cache-structure.md) |
| Story-16 | 逐条术语作用域与翻译 JSON 绑定 | ✔️ 已实现 · [详细](stories/story-16-entry-scoped-terminology.md) |

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

---

## Story-16：逐条术语作用域与翻译 JSON 绑定

**状态**: ✔️ 已实现
**依赖**: Story-03、Story-07、Story-15

### 目标

保持现有术语库、召回规则、优先级、语义 Top 3、批次术语上限、精确直填和 AI 返回协议不变；在请求期保留“条目 → 术语”的临时归属，把每条相关术语嵌入该条目的输入 JSON，消除批次级术语对无关条目的串扰。

### 非目标

- 不修改 `TermEntry`、四来源术语文件、合并缓存或动态术语持久化结构。
- 不修改现有精确全等词条直填代码及执行顺序。
- 不改变术语筛选语义、模型输出 `{id: 译文}` 协议、缓存 A/B 边界或后处理流程。
- 不在本 Story 中解决同一条目内部的多义词消歧。

### 验收标准

- [x] 现有平面 `{term: translation}` 结果继续可用，并新增仅存在于请求生命周期内的逐条术语绑定。
- [x] 精确、正向子串、variant、冠词规范化、反向匹配、相关 in-flight 与语义召回均能保留条目归属；语义召回仍为每条 Top 3。
- [x] 批次候选仍按既有优先级和 `max_terms_per_batch` 选择，不产生“每条各 50 个”的放大。
- [x] 已被精确直填的条目及仅属于它们的术语不进入 LLM 请求；现有直填实现无修改。
- [x] 动态 USER 不再包含批次级 `mandatory_terminology`，每个待翻译 JSON 项使用 `source` 和可选 `terms`；无术语时省略 `terms`。
- [x] AI 返回协议仍为 `{id: 译文}`，流式解析、截断恢复和候选写回保持兼容。
- [x] 缓存 A/B 拓扑不变，逐条术语和原文仍位于断点 B 后。
- [x] 术语检索关闭或向量能力不可用时安全降级，仍可构造仅含 `source` 的合法请求。

### 文件落点

- `src/transbridge/ai_translator/term_database.py`（改）：保留现有平面结果，增加请求期逐条绑定结果及既有优先级/上限复用。
- `src/transbridge/ai_translator/translator.py`（改）：把逐条绑定传给 PromptBuilder，并在直填后只发送剩余条目绑定；不改直填代码块。
- `src/transbridge/ai_translator/prompt_builder.py`（改）：生成逐条嵌套输入 JSON，移除批次级术语段，保持输出协议。
- `data/prompts/langs/zh_CN.toml`（改）：同步逐条术语作用域说明和动态 USER 模板。
- `tests/ai_translator/test_prompt_builder.py`（改）：覆盖逐条 JSON、无术语、共享术语和输出协议。
- `tests/ai_translator/test_term_database.py`（增）：覆盖条目归属、语义 Top 3、in-flight 分配、优先级和批次上限。
- `tests/contracts/translation/test_workload_commit.py`（按需改）：更新 PromptBuilder 测试替身签名并验证直填边界。

### 实施与验证

1. 在不改变术语存储及平面兼容结果的前提下，保留现有召回过程中已经存在的条目归属。
2. 沿用当前候选优先级和批次上限选择术语，再把入选术语分配到相关条目；in-flight 只绑定到按现有文本匹配规则相关的条目。
3. 精确直填完成后过滤逐条绑定，仅把 `llm_entries` 传入请求构造。
4. 把动态输入改为 `{id: {source, terms?}}`，更新稳定规则，并保持模型输出 JSON 协议不变。
5. 运行 PromptBuilder、术语管理器、翻译 workload、缓存转换与 AI 翻译相关回归测试；对涉及文件运行 Ruff 和 `git diff --check`。

### 风险与回退

- 同一术语关联多条时会在 JSON 中重复，可能增加动态 token；仍由批次唯一术语上限约束，并通过测试测量实际输入增长。
- 较弱模型可能跟随嵌套输入返回嵌套输出；SYSTEM 和 USER 明确要求输出扁平 `{id: 译文}`，并用解析回归覆盖。
- 逐条作用域只消除跨条目串扰，不改变同一条目内部的多义词行为。

### 已确认假设

- 用户确认每条仅携带自己的术语，不复制整个批次词典。
- 术语库及其外部/持久化结构保持不变；逐条归属是请求期临时数据。
