# AI 工作流预设与校改润色实施计划

- **状态**：已完成（2026-08-26，Story 5 相关 QA 通过）
- **日期**：2026-08-26
- **需求**：FR5.13、FR6.9、FR26.7.1
- **架构**：ADR-026

## 目标

让翻译、润色、混合成为可编辑且可分别记忆的预设；以用户当前配置生成不可变有效执行档案；让独立润色和混合润色按所选阶段执行错误检测、定向修复、语言润色和裁决，并只在唯一提交边界修改正式集合。

## 非目标

- 不重写三轮翻译算法、术语检索实现或 TaskRuntime。
- 不新增外部依赖，不改变翻译文件格式。
- 不在本次重做进度窗口和 Excel 报告的视觉布局。

## 当前实现事实与约束

- `WindowConfigView` 已能采集全部后处理控件，但 `LLMConfig` 只保存一份全局后处理设置。
- `AITranslatorWindow._on_polish_start()` 当前只创建 `LLMPolisher`；检测、修复和裁决开关被忽略。
- `PostProcessor` 已包含完整阶段组件，但会直接修改传入条目，并存在历史 `id`/`key` 映射差异；不能直接对正式集合运行。
- `AiRunSpec` 已保存不可变配置摘要，适合扩展有效档案摘要。
- `ai_translator_window.py` 和 `run_controller.py` 接近模块规模门禁；新业务责任必须进入独立模块。

## Story 1：模式预设配置与有效执行档案

### 验收标准

- 三个预设分别保存后处理设置，切换后互不污染；旧配置自动迁移且无需用户操作。
- 润色首次默认启用检测、修复、润色和裁决；用户显式关闭后再次打开仍保持关闭。
- 当前界面值覆盖预设保存值，并冻结进入单次运行；后续 UI/文件修改不改变已启动任务。
- 有效档案可输出不含密钥的阶段摘要和稳定 digest。

### 文件与步骤

- 新增 `application/translation/ai_execution_profile.py`：定义冻结档案、预设默认与配置合并合同。
- 修改 `config/llm.py`：增加版本化预设 JSON 的安全序列化、校验和旧字段迁移；旧 `pp_*` 字段保留兼容。
- 修改 `ui/tools/ai_translator/config_presenter.py` 与配置 adapter：切换预设前保存当前字段，切换后渲染目标预设。
- 修改 `run_spec.py`：记录有效阶段摘要/digest；不复制 secret。

### 测试

- 配置往返、畸形 JSON 回退、旧配置迁移、预设互不污染。
- UI 切换后控件恢复、用户修改优先级、运行冻结快照。

## Story 2：候选式校改润色流水线

### 验收标准

- 默认按检测 → 修复 → 润色 → 裁决执行，且每个开关都能真正跳过对应阶段。
- 检测出的错误进入 Refiner；Polisher 接收到修复后的译文，而不是旧译文。
- 正式 `TranslationEntry` 在 worker 执行和预览取消时不发生修改。
- 所有结果按稳定 EntryKey 归一化；`id != key` 时仍能关联问题、修复、润色和裁决。
- 自动应用只提交 pass；pending/reject/failed 保留原译文并有原因。

### 文件与步骤

- 新增 `ai_translator/post_processor/proofread_pipeline.py`：组合现有 Checker/Refiner/Polisher/Arbiter，返回结构化候选与兼容的润色结果 projection。
- 修正 `post_processor.py` 的阶段输入链和 identity 归一化，保证翻译后公共后处理也不会丢失修复结果。
- 改造 `_polish_worker.py` 为通用 pipeline worker，保持现有 Qt signal 生命周期协议。
- 扩展 `result_presenter.py`：按候选 verdict 提交，保留不可变 Entry identity。

### 测试

- 用 stub checker/refiner/polisher/arbiter 验证阶段顺序、关闭阶段、修复后再润色、保守失败语义和无正式 mutation。
- 覆盖纯润色、只检查、检查+修复、完整校改四种组合。

## Story 3：翻译/润色/混合入口统一接线

### 验收标准

- 独立润色从有效档案创建校改流水线，不再固定只调用 `LLMPolisher`。
- 混合润色复用同一流水线，且不再强制关闭用户选择的阶段。
- 启动前摘要显示实际启用阶段；预览和直接应用都使用同一最终候选。
- 同一稳定 EntryKey 在混合任务内只分配一个主动作，不重复执行。
- 现有暂停、取消、进度、报告入口和三模式按钮保持可用。

### 文件与步骤

- 新增 `ui/tools/ai_translator/workflow_profiles.py` 或等价薄协调器，避免扩张窗口 facade。
- 修改 `run_controller.py` 的 worker factory 和 `_mixed_worker.py` 的润色子流程，统一从档案构建 pipeline。
- 最小修改 `ai_translator_window.py`：模式切换委托预设协调器；启动时传递有效档案和候选结果。
- 调整快速运行摘要、预览/报告映射，使其反映“检测/修复/润色/裁决”实际阶段。

### 测试

- 运行控制器 factory 参数映射、独立润色和混合润色等价性、混合去重、迟到结果 guard。
- 相关 UI slice 测试、AI post-process 聚焦测试、Ruff 检查与格式检查。

## 依赖顺序

Story 1 → Story 2 → Story 3。Story 2 的纯 Python 流水线可先在不接 UI 的情况下验证；Story 3 只负责组合。

## 风险、兼容与回退

- 预设 JSON 损坏时回退到默认/旧字段并忽略非法项，不覆盖原文件直到用户再次保存。
- 旧配置 API 和 `pp_*` 属性继续有效；非 UI 入口未传预设时使用翻译预设兼容行为。
- 完整校改会增加 API 调用；有效阶段摘要和每个独立开关提供成本控制。
- 任一入口回归可切回旧 adapter，但不得删除预设数据或恢复静默忽略配置。

## 明确假设

- 用户已经确认“用户配置优先、模式只是预设”以及“润色默认检查并修复错误翻译”，实现范围以此为准。
- 默认非严格裁决把不确定结果留给人工审核，不自动覆盖。

## Story 4：统一详细运行面板

**状态**：已完成（2026-08-26）

### 验收标准

- 润色运行显示实际启用的检测、修复、润色、裁决与汇总阶段，并持续显示阶段进度、总进度、状态统计和详细日志。
- 混合运行在同一窗口展示翻译及公共校改阶段；`AutoTranslator` 和 `ProofreadPipeline` 的内部进度都被连续转发，不再在子流程完成前保持 0%。
- 用户关闭或组合阶段形成自定义流程时，面板只按冻结的 `AiExecutionProfile` 创建实际阶段，不按预设名称伪造执行状态。
- 润色和混合均支持暂停/继续与安全停止；完成、失败、取消、预览和报告的既有所有权及提交语义保持不变。
- 运行中可查看轮次/阶段日志以及成功、失败、待审、新增术语等当前可用统计；可从统一入口打开按翻译批次、校改阶段和 LLM 调用拆分的完整请求/响应日志；长文本不得撑宽窗口。

### 文件与步骤

- 扩展 `ui/tools/ai_translator/run_view.py`：提供可按有效阶段构建的统一详细运行视图，并保留 `AiMixedProgressWindow` 兼容入口。
- 扩展 `_polish_worker.py`、`_mixed_worker.py` 与 `proofread_pipeline.py`：增加结构化阶段进度和日志转发；混合 Worker 共享暂停事件并接入翻译/校改回调。
- 修改 `run_controller.py`：让润色、混合使用统一视图，同时保留 RunController guard、TaskRuntime activity、预览和报告回调。
- 补充 UI slice、布局稳定、Worker 回调与流水线日志透传测试。

### 测试

- 聚焦运行视图测试覆盖动态阶段、进度累计、统计、日志、暂停/继续和停止。
- Worker 测试覆盖混合翻译回调不丢失、共享暂停事件、润色阶段信号与日志。
- 运行 AI translator UI、proofread pipeline 相关测试及 Ruff lint/format、`git diff --check`。

### 风险与回退

- 不修改配置序列化、报告快照或正式集合提交边界；若新视图回归，可回退展示层而不迁移数据。
- 校改各阶段实际候选数可能不同；阶段条使用真实阶段总数，总进度按有效阶段等权折算，避免把未启用阶段计入分母。

## 完成证据

- AI 翻译、后处理、UI slice 与配置等价性回归：153 passed。
- 统一配置仓库契约：12 passed。
- 本次变更相关 Ruff lint/format：全部通过。
- Story 4 的 AI 工具 UI、Worker、校改流水线、混合报告与提交契约回归：169 passed。
- 扩大 AI 回归：174 passed，7 failed；失败均为系统 Python 缺少 `xlrd`、`faiss`、`rank_bm25`，未进入本次变更调用链。
- Story 4 涉及文件 Ruff lint/format 与 `git diff --check`：全部通过；全仓 Ruff 仍有既有 387 项 lint 和 142 个未格式化文件，本次未混入无关整改。

## Story 5：术语初始化进度与失败可见性

**状态**：已完成（2026-08-26）

### 验收标准

- 翻译和混合运行把“从已有译文抽取术语”显示为翻译前的独立阶段，展示真实总批次、已完成批次、当前批次描述和连续日志。
- 无需初始化、已有 `existing_text` 结果或关闭术语检索时不发起 LLM 请求，并以明确的跳过消息结束准备阶段。
- 暂停/停止在批次安全点阻止后续抽取；认证、网络或解析异常在首个失败批次终止本次抽取并记录可定位原因，不再逐批静默重试。
- 原有名称术语直提、冲突过滤、动态术语持久化和后续翻译语义保持兼容；新增术语统计包含本次初始化结果。
- AI 工具 UI 测试的配置仓库与系统凭据存储完全隔离，关闭自动保存窗口不得改写用户的 `data/transbridge.ini` 或真实凭据。

### 文件与步骤

- 扩展 `ai_translator/existing_term_extractor.py` 与 `noun_extractor.py`：报告批次进度，检查暂停/停止，并为调用方提供失败快速退出选项。
- 扩展 `ai_translator/translator.py`：增加独立准备阶段回调，将术语加载、跳过、批次完成和失败写入统一日志，同时保持失败后继续使用已有术语库的兼容策略。
- 扩展 `_translation_worker.py`、`_translation_progress_window.py`、`_mixed_worker.py` 与 `workflow_progress.py`：把术语阶段接入翻译和混合详细面板。
- 在 `tests/ui/tools/conftest.py` 隔离默认 LLM 配置仓库和凭据存储，覆盖所有 AI 工具 UI 测试；补充术语批次、取消、失败快速退出及面板投影回归。

### 测试

- 术语 Seeder/NounExtractor 聚焦测试覆盖多批次进度、已有结果跳过、停止和异常传播。
- AutoTranslator/Worker/UI 测试覆盖独立术语阶段、混合阶段顺序、日志与新增术语统计。
- 在测试前后校验真实配置文件摘要不变，再运行相关 pytest、Ruff lint/format 和 `git diff --check`。

### 风险与回退

- `AutoTranslator.translate()` 只新增可选回调，现有调用方无需迁移；未提供回调时保持旧行为。
- 术语抽取失败仍允许后续翻译使用已有术语库，但错误不再被吞掉；若服务端暂时不可用，本次不会保存不完整的 `existing_text` 初始化标记。

### 完成证据

- 术语 Seeder/NounExtractor、AI 工具 UI 与后处理流水线回归：159 passed。
- Story 5 涉及文件 Ruff lint/format 与 `git diff --check`：全部通过。
- UI 回归前后 `data/transbridge.ini` 的 SHA-256 与修改时间不变，确认测试自动保存未再写入真实配置。
