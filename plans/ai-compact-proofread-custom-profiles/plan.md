# 一次校对润色与具名自定义工作流实施计划

- **状态**：已完成（2026-08-27）
- **日期**：2026-08-27
- **需求**：FR5.13.10、FR6.9
- **架构**：ADR-028

## 目标

把默认校改从多次 LLM 扫描收敛为一次「校对润色」，只保留极简技术校验；保留严格多阶段兼容策略；增加可安全持久化和导入导出的具名自定义入口，并复用现有三种业务执行器。

## 非目标

- 不实现任意 DAG、插件式阶段或第四套 Worker。
- 不用本地启发式判断翻译语义、数字、人名、否定、长度或自然语言术语质量。
- 不在配置文件中保存模型端点、凭据或本地术语路径。
- 不删除旧 QualityGate、Refiner、Polisher 或 Arbiter。

## Story 1：共享的一次校对润色阶段

### 验收标准

- 同一批原文、现译文、上下文和术语只产生一次校对润色 LLM 请求。
- 响应只要求稳定键和最终译文；同文返回有效。
- 缺失、重复、未知键、空译文、解析失败、占位符或程序标签损坏只拒绝对应候选并保留原译文。
- 数字、引号、括号、长度和普通术语差异不触发本地拒绝或第二次裁决。
- 翻译后处理与独立/混合润色使用相同的 prompt、响应和极简验证合同。

### 文件落点

- 新增 `application/translation/combined_proofread.py` 与 `protected_syntax.py`。
- 最小接线 `application/translation/postprocess_stages.py`、`ai_translator/post_processor/proofread_pipeline.py` 和 `ai_translator/translator.py`。
- 更新进度阶段映射、报告投影与聚焦测试。

## Story 2：显式策略、默认值与兼容迁移

### 验收标准

- 新建 translate/polish/mixed 预设均为 `combined`，运行摘要只显示一次「校对润色」。
- 已保存但没有策略字段的旧预设解释为 `strict`，原阶段开关不丢失。
- 严格策略继续按检测、修复、润色、裁决执行，后级消费前级候选。
- 策略进入冻结档案和 digest；标准策略不因旧开关值隐式执行多阶段链。

### 文件落点

- 修改 `application/translation/ai_execution_profile.py`、`config/llm.py`、后处理配置 View/Presenter。
- 更新默认、迁移、摘要、preflight 和 checkpoint/digest 测试。

## Story 3：具名自定义配置与第四入口

### 验收标准

- 用户可创建、选择、重命名、删除、导入和导出多个配置；无配置时启动被明确禁用。
- 每个配置声明基础模式并复用对应作用域和运行器；自定义 mixed 复用现有动作规则。
- 内部库和导出文件使用版本化 JSON，非法导入不部分写入。
- 导出内容不含模型、端点、凭据、Embedding 秘密或本地术语路径；运行时继承当前全局服务设置。
- 切换自定义和内置预设不串值，custom overlay 不回写全局配置。

### 文件落点

- 新增 `application/translation/custom_workflow_profile.py`、`config/ai_workflow_profiles.py`。
- 新增 `ui/tools/ai_translator/custom_profile_presenter.py`、`custom_profile_view.py`。
- 最小修改配置 presenter、view port、控件声明和窗口分派；不得突破既有 UI 模块规模门禁。

## Story 4：通用入口、日志与回归收口

### 验收标准

- 翻译、润色、混合与相关 Smart Assistant 校改入口显式选择 combined/strict，不存在绕过共享预算的默认单条润色旁路。
- 标准阶段的请求/错误进入对应 workflow log；运行面板只显示实际阶段。
- 新阶段遵守共享 `max_concurrent` 与业务内容 Token 批次边界。
- 日志查看器陈旧测试同步到手动选择、单文件懒加载和手动刷新合同。

## Story 5：Smart Assistant 后处理预设与丰富参数

### 验收标准

- `run_postprocess({})` 默认加载内置 `polish` 预设并使用 `combined`，只显示和执行一次「校对润色」。
- `profile` 可选择内置 translate/polish/mixed 预设，或按 UUID/名称选择具名自定义工作流；配置仍从全局继承 Provider、模型、端点、凭据和术语路径。
- `entry_ids` 优先；否则显式 `scope` 优先于 Smart Assistant 已设置作用域，再回退到预设的润色范围。只处理已有译文条目。
- 支持 `strategy`、`intensity`、`max_concurrent`、`max_tokens_per_batch`、`max_output_tokens`、`max_terms_per_batch` 覆盖；旧 `max_workers` 作为 `max_concurrent` 兼容别名。
- `phases` 只对 `strict` 生效；旧调用仅传 `phases` 时自动进入 strict，同时显式 `combined + phases` 被拒绝。
- combined 与 strict 均复用共享请求预算、准入后最新术语读取、工作流 LLM 日志、候选报告和单一受控提交。
- 启动结果、任务元数据、最终报告和工具帮助暴露最终生效的 profile、strategy、stages、scope 与 limits。

### 文件落点与步骤

- 新增 Smart Assistant 后处理请求解析/执行切片，避免继续扩张已接近 500 行的 `tool_proofreader.py`。
- 将 Smart Assistant LLM 运行时提炼为可指定 workflow 的共享构造器，保留 `start_polish` 兼容包装。
- 最小修改 `tool_proofreader.py` 的 controller 委托与工具 Schema；复用 `AiExecutionProfile`、自定义配置仓库和 application 后处理阶段。
- 更新参数、默认 combined、旧 phases 迁移、配置来源、日志/额度和报告回归测试。

### 风险与回退

- 旧的无参数调用会从六阶段 strict 改为一次 combined，这是用户明确要求的默认行为变化；需要旧行为时传 `strategy="strict"` 或沿用显式 `phases`。
- 具名配置损坏时不得影响内置预设；未知配置、非法额度、空阶段和参数冲突在启动任务前失败。
- 回退路径是显式 strict，不删除旧 PostProcessor 或历史报告读取能力。

### 验证

- 聚焦 application、AI post-process、config repository、UI slice、worker、Smart Assistant 与日志测试。
- 对涉及文件运行 Ruff lint/format check 和 `git diff --check`；审查未提交差异，保留无关用户改动。

## 依赖、风险与回退

Story 1 和 Story 3 的数据仓库可并行，Story 2 在两者接口稳定后集成，Story 4 最后统一验证。当前工作区已有并发、术语和运行面板相关未提交改动；实现必须以新增小模块为主，避免覆盖这些改动或继续扩张超过门禁的旧模块。

严格策略是标准阶段的即时回退路径。配置导入失败时保持原库不变；标准候选技术校验失败时保留原译文，不自动追加网络调用。

## Story 1～5 完成证据

- Story 1～4 已全部实现；默认入口使用一次校对润色，严格多阶段链保留为显式兼容策略。
- 具名自定义配置已支持完整 CRUD、版本化 JSON 导入导出及基础模式复用，服务端点和凭据不进入配置文件。
- Smart Assistant 默认校改、GUI 进度和工作流日志均复用共享请求额度；日志窗口使用手动刷新和选中文件懒加载。
- 最终聚焦回归 162 项通过；项目 `uv` 环境抽样 20 项通过；相关大范围回归 863/865 通过，余下 2 项为既有测试对本机 LLM API 配置/可选客户端可用性的环境假设，与本 Epic 改动无生产代码关联。
- 本 Epic 涉及的生产和测试文件通过 Ruff lint、格式检查及 `git diff --check`（仅行尾转换提示）。
- Story 5 已将 Smart Assistant 默认入口切换为内置润色预设的 `combined`，支持具名配置、作用域、强度和共享额度覆盖，并保留显式 strict/旧 phases 兼容路径。
- Story 5 扩展回归 205 项通过、1 项跳过；Smart Assistant 全量 485 项通过，余下 2 项仍是既有测试对本机 LLM API 配置和可选客户端可用性的环境假设。
