# 存量译文术语初始化

> **状态**：已完成（2026-08-26，相关 QA 通过）
> **关联需求**：FR5.2 术语库管理
> **关联方案**：[AI 自动翻译](../ai-translation/plan.md)、[术语格式兼容](../terminology-format-compatibility/plan.md)

## 目标

当项目同时包含已有译文和待翻译条目时，在翻译批次开始前仅从存量译文初始化术语：名称类字段整条提取，普通文本调用现有专有名词抽取器提取双语子段，使剩余条目可以沿用已有译名。

## 非目标

- 不引入翻译记忆、整句复用、风格参考或后处理能力。
- 不建立置信度评分、证据晋级、多级候选状态或人工审批界面。
- 不改变现有术语来源优先级、向量召回、逐条术语绑定和 Prompt 输出协议。
- 不覆盖已存在的人工、导入或动态术语。

## 当前事实与约束

- `AUTO_TERM_CONTEXTS` 已定义人物、地点、书名、种族等可整条作为术语的名称字段。
- `NounExtractor` 已能从原文—译文对中返回 `TermEntry`，但目前只检查字段非空，没有验证两个子段是否来自同一双语条目。
- `DynamicTermDatabase.add()` 会覆盖同名的非人工条目，因此存量提取结果必须先在内存中去重和冲突检查，不能按扫描顺序逐条写入。
- `term_database.py` 已超过仓库职责阈值；新提取、聚合责任放入独立模块，只通过现有管理器窄接口写入。
- 当前工作区包含未提交的术语格式兼容改动；实现必须保留 `TermEntry` 规范模型、CSV 来源和缓存适配差异。

## Story 1：双路径提取与最小安全合并

### 验收标准

- 原文和译文非空、非隐藏且非“有疑问”的存量条目可参与扫描。
- `AUTO_TERM_CONTEXTS` 中的条目按整条原文—译文生成 `existing_name` 术语。
- 其余存量条目按受控批次交给 `NounExtractor`，生成 `existing_text` 术语。
- 文本抽取结果只有在原文子段和译文子段同时出现在同一个输入对中时才保留。
- 同词同译折叠为一条；同词多译整组跳过；已有术语保持不变。
- 原文与译文相同的保留词不被误删。

### 文件落点

- `src/transbridge/ai_translator/existing_term_extractor.py`（新增）：语料筛选、双路径提取、规范化、冲突合并和批量写入。
- `src/transbridge/ai_translator/noun_extractor.py`（修改）：增加同一双语输入对的精确子段校验和重复结果折叠。
- `src/transbridge/ai_translator/prompt_builder.py`（修改）：内置抽取 Prompt 明确要求返回连续原样子段。
- `tests/ai_translator/test_existing_term_extractor.py`（新增）。
- `tests/ai_translator/test_noun_extractor.py`（新增）。

### 实现约束

- 源术语以 Unicode NFKC、收敛空白和 `casefold()` 作为去重键，展示文本保留首次观察到的原样值。
- 目标译文以 Unicode NFKC 和收敛空白作为冲突比较键。
- 名称字段优先于文本抽取结果；同词同译时保留 `existing_name` 来源及其 context。
- 文本提取按固定小批次调用现有抽取器，避免一次把全部存量译文送入 Prompt。
- 已存在 `existing_text` 来源时不重复调用文本抽取；名称字段扫描保持低成本幂等，可补入此前缺失的名称术语。

## Story 2：翻译启动前自动初始化

### 验收标准

- 只有当前集合同时存在可用存量译文和待翻译候选时才执行初始化。
- 初始化发生在术语来源加载之后、首个翻译批次调用之前，新术语在本次运行即可参与现有精确/子串匹配。
- 初始化结果计入 `new_dynamic_terms`，并在日志中报告名称提取数、文本提取数、冲突数和已有术语跳过数。
- 无存量译文、无待翻译候选、文本抽取失败或没有提取结果时保持当前翻译行为。
- 断点恢复和重复运行不会生成重复术语，也不会覆盖已有术语。

### 文件落点

- `src/transbridge/ai_translator/translator.py`（修改）：在已加载术语源后调用独立初始化器并记录摘要。
- `tests/ai_translator/test_existing_term_extractor.py`（扩展）：覆盖部分翻译集合判定与重复运行行为。
- 现有翻译合同测试（回归）：确保全新项目和精确直填路径不变。

## 依赖顺序与验证

1. 完成 Story 1 的纯提取和合并逻辑及单元测试。
2. 完成 Story 2 的翻译入口接入和回归测试。
3. 运行聚焦测试：`uv run pytest tests/ai_translator/test_existing_term_extractor.py tests/ai_translator/test_noun_extractor.py tests/ai_translator/test_term_database.py -q`。
4. 运行相关翻译合同测试、Ruff 检查、Ruff 格式检查和 `git diff --check`。

## 风险与回退

- 文本抽取会增加一次性 LLM 调用成本；通过 `existing_text` 来源存在性避免后续翻译运行重复抽取。
- 用户自定义抽取 Prompt 保持原样；无论 Prompt 如何表述，运行时仍以“同一双语对中的精确子段”作为最终门禁。
- 同词多译采用整组跳过，可能少生成术语，但不会因扫描顺序产生错误覆盖。
- 回退时移除翻译入口调用和独立提取模块即可；现有动态术语文件中的 `existing_name` / `existing_text` 条目仍是规范 `TermEntry`，旧加载路径可继续读取。

## 明确假设

- 存量译文中隐藏和“有疑问”条目不是可靠术语来源，首版直接排除。
- 首版不启用 context 级同词多译；只要同一源术语出现多个译文就跳过。

## 完成记录

- Story 1：已完成。名称字段整条提取、普通文本精确子段校验、规范化去重、同词多译整组跳过和已有术语保护均已实现。
- Story 2：已完成。半成品集合会在术语源加载后、首批翻译前自动初始化术语；新增术语在同一运行中即可用于精确匹配。
- QA：AI 翻译、术语、Prompt、翻译运行合同和依赖降级相关 187 项测试通过；本次目标文件 Ruff 检查与格式检查通过。仓库级 Ruff 仍被大量既有未格式化文件阻断，本次未扩大范围修复。
