# AI 请求 Token 分批与共享并发计划

- **状态**：Story 1～7 已完成；相关 QA 通过
- **日期**：2026-08-26
- **需求来源**：用户确认的参数语义；FR5.11.4、FR5.13.8
- **关联架构**：ADR-003、ADR-019、ADR-026
- **依赖**：现有 `LLMConfig.max_concurrent`、`LLMConfig.max_tokens_per_batch`、`AiRunSpec`、三轮翻译策略和候选式校改流水线

## 目标

在 AI 翻译工具的翻译、术语抽取、检测、修复、润色、裁决和混合/批量入口中统一两项配置的含义：

- `max_concurrent` 只表示一次运行内允许同时在途的 LLM 请求最大数；混合并行的多个分支共享同一个额度。
- `max_tokens_per_batch` 只表示单个请求中待模型处理的业务内容 Token 上限，不再被并发数改变。

正式翻译按原文计算业务内容 Token；已有译文术语抽取按原文与现译文之和计算；校改各阶段按该阶段实际提交给模型的原文、候选译文、问题或裁决候选计算。系统 Prompt、指令、JSON 键、术语注入等请求开销不计入用户配置的“业务内容 Token”，但在发送前仍须用独立的完整上下文安全校验防止超过模型上下文窗口。

## 非目标

- 不改变 ADR-003 的三轮顺序、Round 2 的 quest 顺序屏障、动态术语传播或候选提交语义。
- 不改变 `max_output_tokens`；它仍只控制模型输出上限。
- 不把智能助手、MCP 对话、Embedding 或 ParaTranz 网络请求纳入本次 AI 翻译工作流配额。
- 不在本计划中引入跨进程或跨任务的账户级限流；配额首先以单次 `AiRunSpec` 为所有者。
- 不静默拆分并重组一个超预算的单条翻译记录；单条自身超过上限时在预检中明确报告并阻止该条请求，用户可提高预算。后续若需要长文分段翻译，单独设计可恢复的分段/合并协议。

## 当前实现事实与关键约束

- `BatchPlanner` 以 `max_tokens_per_batch * 3` 转成字符上限，只统计原文与 key；它还接收 `max_workers`，以“批次数至少为并发数两倍”为目标缩小批次，因此 Token 预算和并发调度发生耦合。
- `AutoTranslator._split_last_batch_to_fill_workers()` 会继续拆分最后一批来填满线程池；这不突破旧字符上限，但让并发配置改变请求数量和单请求规模。
- `ExistingTermSeeder` 固定 20 条一批并串行调用 `NounExtractor`，既不使用 Token 预算，也不使用 `max_concurrent`。
- `PostProcessor` 的 QualityGate、Refiner、Polisher、Arbiter 使用各自的条目数批大小和独立线程池；混合并行时翻译和校改分支各自可创建 `max_concurrent` 个请求，尚未共享 FR5.11.4 要求的额度。
- `tiktoken` 已是项目依赖，`infra.prompt_cache` 已有模型编码选择和未知模型保守回退经验；新实现应提取通用 Token 计数能力，不把请求分批依赖到缓存模块。
- 日志包装器、真实 Provider 客户端和取消监控可能多层组合；并发槽必须覆盖一次完整 `chat/chat_stream` 调用并在所有异常路径释放，不能按流式 chunk 或 SDK 内部重试重复计数。
- 配置和运行开始后必须冻结；等待并发槽的任务需要响应暂停/取消，取消后不得获得槽并发起新的外部请求。

## 统一契约

一次 AI 运行创建一个请求预算对象，所有该运行派生的 LLM 客户端共享它：

```text
ContentTokenBatchPlanner
  输入：有序业务项、内容投影、max_tokens_per_batch、可选条目数上限/分组屏障
  输出：稳定有序批次 + 每批 content_tokens + 超预算项诊断

AiRequestBudget(max_in_flight=max_concurrent)
  acquire(cancel/pause) -> lease
  lease 覆盖一次 chat/chat_stream，finally 释放
  snapshot -> in_flight / waiting / peak
```

配置关系固定为：

```text
业务内容 --按 max_tokens_per_batch 切批--> 请求队列
请求队列 --按 max_concurrent 取得共享 lease--> Provider
Provider --按 max_output_tokens--> 响应
```

`max_concurrent` 不参与批次规划，`max_tokens_per_batch` 不参与线程池大小计算。各校改阶段原有 `pp_*_batch_size` 暂时保留为额外的条目数上限；有效批次在“Token 上限”和“条目数上限”任一先达到时结束，以保持旧配置兼容且不绕过新 Token 硬限制。

## Story 1：通用业务内容 Token 计数与稳定分批（已完成）

### 验收标准

- 同一组业务项、模型和 Token 上限无论 `max_concurrent` 为 1、5 或 50，都生成完全相同的批次边界和批次指纹。
- 每个正常批次的 `content_tokens <= max_tokens_per_batch`；Token 数来自业务内容投影，不把 system/user 指令、JSON 键或术语表计入该配置。
- 正式翻译只统计原文；术语抽取统计原文和已有译文；检测/修复/润色/裁决分别统计其实际业务字段。
- OpenAI 已知模型使用对应 `tiktoken` 编码；兼容/未知模型使用固定保守编码和安全系数，结果标记为 estimate，且不访问网络下载编码资源。
- 单条业务项自身超预算时不发请求，生成包含稳定 EntryKey、估算 Token 和配置上限的诊断；空文本、Unicode、超长 CJK、占位符和多行文本均有测试。
- 分批保持输入顺序、分类边界、翻译轮次和 quest 分组；不会把不同 Round 或不同 quest 的上下文屏障合并。

### 文件落点与实施步骤

- 新增 `src/transbridge/application/translation/token_batching.py`：定义 `ContentTokenCounter` 协议、`ContentBatch`、超预算诊断和不依赖 Qt/Provider SDK 的稳定贪心分批器。
- 新增或从缓存逻辑提取 `src/transbridge/infra/token_counting.py`：封装 `tiktoken` 编码选择、未知模型回退、安全系数和离线缓存；`prompt_cache.py` 改为复用公共能力而不改变缓存阈值语义。
- 修改 `src/transbridge/ai_translator/batch_planner.py`：使用内容 Token 分批，移除 `max_workers` 参数、字符上限自适应和并发驱动的批次数目标；保留 ContextPlanner 的 Round/category/quest 分类结果。
- 修改 `src/transbridge/application/translation/planning.py`：让上下文分组接受显式批次规划结果或 Token 权重，避免重复保留旧字符切分责任。
- 修改 `src/transbridge/ui/tools/ai_translator/scope_presenter.py`：预估与正式执行调用同一 Token 分批器，显示批次数、预计 Token 范围和超预算项数量。

### 测试策略

- 新增 `tests/application/translation/test_token_batching.py`，覆盖硬上限、稳定顺序、模型回退、Unicode、超预算项和并发值不影响批次。
- 修改 `tests/contracts/translation/test_planning.py` 与 AI 翻译 BatchPlanner 测试，覆盖三轮/quest 屏障和预估执行一致性。
- 复验 prompt cache Token 阈值测试，证明公共计数提取没有改变缓存 A/B 结构。

## Story 2：单次运行共享的 LLM 请求并发预算（已完成）

### 验收标准

- `max_concurrent=N` 时，同一运行内所有 `chat` 和 `chat_stream` 调用观测到的峰值在途请求数不超过 N；N 小于等于 0 或异常类型值在启动前被拒绝。
- 混合并行的翻译、术语抽取和校改分支共享同一个预算；不得出现两个分支各自达到 N、合计达到 2N 的情况。
- 等待槽位不算在途请求；取得 lease 后到请求成功、Provider 异常、日志异常、流式回调异常或取消为止算一个在途请求，并在 `finally` 可靠释放。
- SDK 内部的顺序重试继续占用同一个逻辑 lease；不会因为一次重试临时突破运行上限。
- 暂停时不发起新的请求；取消时所有等待者退出，已取消运行释放槽位且不会迟到启动。
- 同一运行能报告 `in_flight`、`waiting` 和 `peak_in_flight`，供日志和测试使用，不把 semaphore 暴露给业务层。

### 文件落点与实施步骤

- 新增 `src/transbridge/application/translation/ai_request_budget.py`：定义 run-scoped、取消感知的预算和 lease，不依赖 PyQt。
- 新增 `src/transbridge/infra/limited_llm_client.py`：以透明 `LLMClient` decorator 在 `chat/chat_stream` 边界获取 lease，并保证异常释放和取消透传。
- 修改 `src/transbridge/ui/tools/ai_translator/run_controller.py` 与 `run_spec.py`：从冻结配置为每次运行创建唯一预算，通过组合根传给 worker/translator/pipeline；配置摘要继续不包含密钥。
- 修改 `workflow_logging_client.py` 的包装顺序和诊断字段：记录 call id、content_tokens、等待时长和峰值并发；并发术语调用改用每调用独立日志文件，避免响应内容交错。
- 为 headless/直接构造的 `AutoTranslator` 和 `ProofreadPipeline` 提供显式预算参数；未提供时按冻结配置创建本运行私有预算，禁止退化为无限并发。

### 测试策略

- 新增 `tests/application/translation/test_ai_request_budget.py`：用 barrier fake client 验证峰值 N、等待、异常释放、取消等待和流式异常。
- 新增组合测试：翻译与校改两个线程池同时提交时全局峰值仍为 N；两个不同 run 各有独立预算。
- 复验 LLM 日志包装、暂停/停止和 Qt worker 生命周期测试。

## Story 3：正式翻译与并发术语抽取迁移（已完成）

### 验收标准

- `AutoTranslator` 不再把 `max_concurrent` 传给 BatchPlanner，也不再为了填满线程池拆分最后一批；三轮执行和 Round 2 quest 屏障保持不变。
- 正式翻译批次数只由 Token 预算、上下文分组和可选条目数上限决定；线程池可以预提交任务，但实际 Provider 在途请求受共享预算约束。
- `ExistingTermSeeder` 不再固定 20 条；它使用同一 `max_tokens_per_batch` 按“原文 + 已有译文”切批，并使用同一 `max_concurrent` 并发调用 `NounExtractor`。
- 术语批次可乱序完成，但候选合并、冲突裁决和持久化按原始批次顺序稳定执行；相同输入重复运行得到相同术语结果和指纹。
- 首个术语批次失败后停止发起新请求、取消尚未取得 lease 的任务、等待已在途调用退出，并保持现有“不保存不完整 existing_text 初始化结果”的语义。
- 暂停、停止和失败进度使用结构化 outcome，不再靠中文日志正则推算；完成、失败、剩余和候选术语统计在乱序完成时仍正确。

### 文件落点与实施步骤

- 修改 `src/transbridge/ai_translator/translator.py`：删除并发驱动拆批，注入共享请求预算，将 Token/批次元数据传入日志与进度。
- 修改 `src/transbridge/ai_translator/existing_term_extractor.py`：以通用 Token 分批器生成术语批次，通过受控 executor 并发执行，按 batch index 汇总结果和错误。
- 修改 `src/transbridge/ai_translator/noun_extractor.py`：接受批次元数据并保持无共享可变 Prompt 状态；输出 Token 上限仍由术语抽取策略单独控制。
- 修改 `_translation_worker.py`、`_mixed_worker.py`、`workflow_progress.py` 和日志查看器：消费结构化术语批次 outcome，显示真实 queued/in-flight/completed/failed 和逐调用对话日志。
- 修改 `_batch_translation_worker.py`：一个批量运行的所有插件复用同一请求预算；插件仍按现有顺序处理并共享动态术语。

### 测试策略

- 扩展 ExistingTermSeeder 测试，覆盖按 Token 切批、最大并发 N、乱序结果稳定合并、首错停止、暂停/取消和单条超预算。
- 扩展 AutoTranslator 三轮测试，断言不同并发数生成相同批次指纹，并验证 Round 2 同 quest 串行、quest 间可并发。
- 扩展 UI 进度测试，覆盖术语并发下计数单调、日志不交错和停止后可重新运行。

## Story 4：校改阶段及所有 AI 翻译入口统一接入（已完成）

### 验收标准

- QualityGate、Refiner、Polisher、Arbiter 均使用同一业务内容 Token 上限；原 `pp_*_batch_size` 仅作为额外条目数上限，不能产生超 Token 请求。
- 独立翻译、独立润色、自定义阶段组合、混合串行、混合并行和批量翻译均创建且只创建一个 run-scoped 请求预算。
- 混合并行在压力测试中总峰值不超过 `max_concurrent`；术语阶段与校改重叠时也共享相同上限。
- 各阶段执行顺序、候选输入链、裁决结果、唯一提交边界、checkpoint 指纹和报告内容保持兼容。
- 不需要 LLM 的本地检测/执行阶段不获取并发槽，也不被 Token 分批器错误拆分。

### 文件落点与实施步骤

- 修改 `src/transbridge/ai_translator/post_processor/post_processor.py` 与 `proofread_pipeline.py`：以通用分批器替换单纯按条目数切片，按阶段提供内容投影，并复用共享请求预算。
- 修改 QualityGate/Refiner/Polisher/Arbiter 的 batch 接口：接收稳定 batch metadata；Prompt 和输出协议保持不变。
- 修改 `src/transbridge/ui/tools/ai_translator/polish_runtime.py`、`_polish_worker.py`、`_mixed_worker.py`、`run_controller.py` 和批量入口组合代码：传递同一个预算，不在分支内各自创建配额。
- 修改 checkpoint 批次指纹：以稳定 EntryKey 集合、阶段、Token 规划版本和内容摘要生成；旧 checkpoint 版本明确拒绝或只读迁移，不能错误跳过新批次。

### 测试策略

- 扩展 ProofreadPipeline 测试，逐阶段验证 Token 硬上限、条目数兼容上限和候选链不变。
- 增加所有模式参数化测试，验证相同运行预算在各 worker/client 间复用。
- 使用可控 fake LLM 同时阻塞多个阶段，观测混合并行全局峰值和取消后的零迟到请求。

## Story 5：配置语义、可观测性和兼容迁移门禁（已完成）

### 验收标准

- UI 将字段明确标为“最大并发请求数（本次 AI 工作流共享）”和“每请求业务内容 Token 上限”；帮助文本说明每请求条目数不固定。
- 运行前预估显示总业务条目、预计请求数、最大/平均 content tokens、超预算条目和所选并发；预估与实际首轮批次计划使用同一不可变结果。
- 运行日志记录每个请求的 stage、batch id、entry count、content_tokens、等待时间和运行时峰值；不得记录 API Key 或凭据。
- 旧配置无需字段迁移：`max_concurrent` 和 `max_tokens_per_batch` 原值保留，只修正执行语义；首次加载不自动改写用户配置。
- 对旧 `pp_*_batch_size` 提供兼容说明；它们继续是条目数上限，后续是否弃用另行决定。
- 文档、配置往返、GUI/批量/headless 入口和性能基准全部通过后，才能移除旧字符切批及固定 20 条术语切批代码。

### 文件落点与实施步骤

- 修改 AI 翻译配置视图、`config_presenter.py`、`scope_presenter.py` 和相关帮助文本；不新增配置字段。
- 修改 `run_spec.py`：冻结 Token 规划版本、模型、Token 上限、并发上限和批次计划摘要，确保运行中改配置不影响当前任务。
- 扩展 `workflow_logging_client.py`、详细运行面板和报告诊断，显示请求级 Token/并发事实，但不改变翻译结果报告的正式数据结构。
- 更新相关需求/用户文档和测试 fixture；配置 UI 测试继续使用隔离仓库，验证真实 `data/transbridge.ini` 摘要不变。

### 测试与完成门禁

- 聚焦测试：Token 计数/分批、请求预算、AutoTranslator、ExistingTermSeeder、PostProcessor、各 worker 和配置 UI。
- 并发属性测试：至少覆盖 N=1/3/50、两个混合分支、异常/取消/暂停、SDK 重试和流式响应。
- 性能基准：10,000 条短文本规划不阻塞 UI；100 个 fake LLM 请求的峰值严格等于或低于 N，取消后不再开始新请求。
- 完成前运行相关 pytest、`uv run ruff check src tests`、`uv run ruff format --check src tests` 和 `git diff --check`；项目既有无关门禁失败需单独列证据。

## Story 6：并发准入后刷新术语并构建 Prompt（已完成）

### 验收标准

- 等待共享请求预算的翻译任务不得在排队前固化术语快照；必须在取得 lease 后读取最新有效术语和 in-flight 缓存，再构建实际发送的 Prompt。
- 新入口与现有 `chat` / `chat_stream` 兼容，Provider 异常、消息构建异常、取消和流式回调异常均只获取一次 lease 并在 `finally` 释放。
- 日志可在任务提交时稳定分配调用编号，在实际准入后记录最终请求；不得因延迟构建造成请求与响应串台。
- 所有 LLM API 异常均写入对应调用日志。除异常类型和消息外，在 SDK 提供时记录 HTTP 状态、错误代码、响应体和 request id；凭据、Authorization、API Key 和敏感请求头必须脱敏。
- Provider 成功返回空字符串时明确记录为空响应错误诊断，不得只留下空白响应区。

### 文件落点与实施步骤

- 扩展 `src/transbridge/infra/limited_llm_client.py`，提供在 lease 内执行消息工厂的延迟调用路径，保留透明客户端旧接口。
- 扩展 `src/transbridge/ui/tools/ai_translator/workflow_logging_client.py`，支持延迟请求日志、结构化 Provider 错误字段和空响应诊断。
- 修改 `src/transbridge/ai_translator/translator.py` 的正式翻译和术语冲突重翻调用，在准入后刷新术语快照并构建 Prompt；其他无动态术语依赖的调用保持兼容。

### 测试策略

- 用受控 barrier 证明等待者取得 lease 后才能执行消息工厂，并能读取前一请求刚发布的术语。
- 覆盖消息工厂异常、Provider 结构化异常、空响应、敏感字段脱敏、流式异常和取消，断言日志归属及 lease 零泄漏。

## Story 7：权威术语冲突的定向单条重翻（已完成）

### 验收标准

- 存量名称直提和文本 LLM 抽取均保留产生候选的稳定 `EntryKey`；同词同译继续折叠，新增候选内部同词多译继续整组跳过。
- 若候选术语已存在且译法相同，只计为已有术语；若当前有效术语库给出不同译法，则保留库中权威值、不写入冲突候选，并生成包含条目、候选译法和权威译法的结构化冲突证据。
- 只对存在权威库译法的冲突条目创建修复任务；多个冲突按条目聚合，每条任务单独请求，但任务之间可共享运行并发执行。
- 修复请求把全部冲突术语作为该条目的强制术语，在取得 lease 后重新解析当前权威译法并构建 Prompt；不受普通术语裁剪顺序影响。
- 每个条目每次运行最多修复一次。响应为空、缺项、格式损坏、未采用权威术语或 Provider 失败时保留原译文并记录失败，不递归入队。
- 修复候选通过既有候选/提交边界写回；隐藏、锁定、版本已变化的条目继续由现有策略拒绝，不能绕过提交保护。
- 进度和日志分别报告冲突数、入队条目数、成功数和失败数，并能定位对应 EntryKey 和术语差异。

### 文件落点与实施步骤

- 扩展 `src/transbridge/ai_translator/existing_term_extractor.py` 的候选聚合结果，保留来源证据并区分内部冲突、同译已有和权威库冲突。
- 为 `src/transbridge/ai_translator/term_database.py` 增加按现有来源优先级解析有效 `TermEntry` 的窄查询接口，避免业务层访问私有合并列表。
- 在 `src/transbridge/ai_translator/translator.py` 的术语初始化之后、正常三轮翻译之前构建并执行单条修复队列；无冲突时不增加请求。
- 复用现有候选会话、共享请求预算、Token 上限、暂停/取消和日志通道，不新增第二套提交机制。

### 测试策略

- 覆盖候选不存在、同词同译、候选内部冲突、权威库冲突、多个冲突合并到同一条目和同一冲突涉及多条目的场景。
- 覆盖成功修复、模型仍返回旧译法、空响应、连接错误、取消、条目 revision 变化、锁定条目和单条 Token 超限；失败不得覆盖原译文。
- 集成测试断言冲突修复先于正常 Round 1，修复与正常翻译共享峰值上限，且同一条目不会形成修复循环。

## 完成证据（2026-08-26）

- 新增稳定业务内容 Token 分批器、已知/未知模型 Token 计数器、运行级 `AiRequestBudget` 和透明 LLM 限流客户端。
- 正式翻译、已有译文术语抽取、对话动态术语抽取、质量检测、修复、润色、裁决、混合串/并行及批量插件均已接入统一契约。
- Round/category/quest 屏障保持不变；Round 2 为 quest 间并发、同 quest 内串行，并发数不再参与批次边界计算。
- 配置界面明确区分共享并发与单请求业务内容 Token；预估显示请求数、平均/最大 Token、超限项和共享并发；逐调用日志记录完整请求/响应及预算等待、在途和峰值指标。
- 兼容配置不新增字段、不重写现有值；旧 `pp_*_batch_size` 继续作为 Token 上限之外的条目数附加上限。
- 验证通过：213 项聚焦回归、352 项扩大 AI/契约/UI 回归、变更文件 Ruff check/format check 和 `git diff --check`。项目全量 Ruff 仍有 379 个任务外既有问题，本次未批量改写。
- 完整 Prompt 的 Provider 上下文窗口校验维持现有客户端责任；本次硬上限按用户确认语义只统计业务内容，不把指令、JSON 外壳或术语注入计入 `max_tokens_per_batch`。
- Story 6：翻译请求支持在共享并发准入后构建最终 Prompt；逐调用日志覆盖 Provider 结构化错误、request id、空响应和敏感字段脱敏，并已接入独立翻译、混合、润色及批量入口。
- Story 7：存量候选冲突保留 EntryKey 证据；只对有效术语库冲突执行定向单条重翻，失败保留原译文，队列可随翻译断点恢复。
- Story 6～7 聚焦与 UI 回归 114 项全部通过；扩大 AI/应用翻译/infra/UI 回归 318 项通过，7 项因备用环境缺少 `xlrd`、`faiss`、`rank_bm25` 未运行成功；全部变更 Python 文件 Ruff check 与 format check 通过。

## 依赖顺序

1. Story 1 先固定 Token 语义和批次指纹。
2. Story 2 建立共享请求预算，可与 Story 1 的后半段测试并行开发，但集成前两者都必须完成。
3. Story 3 在 Story 1、2 上迁移正式翻译和术语抽取。
4. Story 4 再迁移校改及全部入口，验证混合并行共享额度。
5. Story 5 最后完成 UI 文案、预估一致性、迁移门禁和综合验证。
6. Story 6 先建立准入后构建请求的基础能力和完整错误日志。
7. Story 7 复用 Story 6 的调用边界执行权威术语冲突重翻，并完成综合回归。

## 风险与回退

- **Token 编码与 Provider 不完全一致**：已知模型用对应编码；未知/Anthropic 采用保守估算并标记 estimate。完整请求另做上下文安全检查，宁可提前拆批或报错，不发送可能超窗请求。
- **术语并发改变完成顺序**：所有候选按稳定 batch index 合并，数据库写入仍集中在抽取成功后的单一阶段；不得由工作线程直接写术语库。
- **共享客户端取消影响多个在途请求**：预算只管准入，客户端取消策略必须按 run 关闭所有在途请求并等待退出；测试覆盖取消竞态和 lease 释放。
- **线程池排队占用内存**：executor 只保留有界窗口，不一次提交全部大项目批次；请求预算的 waiting 指标用于验证积压。
- **旧 checkpoint 不兼容新批次边界**：提高规划 schema 版本并拒绝错误恢复，保留旧文件供用户回退旧版本，不静默覆盖。
- **回退策略**：可在发布前回退到旧执行 adapter，但配置文件不降级、不删除新诊断；Token 规划与共享预算必须整体回退，禁止新旧两套同时调度同一运行。

## 明确假设与未决问题

- 已确认：`max_concurrent` 是单次 AI 工作流中同时在途的 LLM 请求硬上限，不是单请求条目数。
- 已确认：`max_tokens_per_batch` 是单请求业务内容 Token 硬上限；每请求条目数由内容长度自然决定。
- 已确认：术语抽取使用相同 Token 上限和相同共享并发额度。
- 已确认：混合并行的翻译与校改分支共享额度，符合 FR5.11.4，而不是各自拥有 N。
- 假设：当前 `max_tokens_per_batch` 配置值原样保留；语义修正不自动换算旧“字符估算”下的等效值，因此升级后请求数可能变化，预估面板需在运行前明确展示。
- 未决：是否在未来引入长文本自动分段/重组；本计划采用“单条超预算即预检失败”以维护硬上限。
- 未决：是否最终弃用四个 `pp_*_batch_size` 条目数配置；本计划先作为附加上限保留兼容。
