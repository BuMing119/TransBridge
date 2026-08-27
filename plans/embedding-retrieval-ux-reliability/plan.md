# Embedding 语义检索体验与可靠性整改

**状态**: 已完成（2026-08-27，相关 QA 通过）
**对应需求**: FR5.3、FR5.12、FR5.13.3、FR21.8、FR21.9
**相关 ADR**: ADR-010、ADR-013
**业务域**: AI 翻译 / 术语检索 / 配置与预检

## 目标

- 让 UI 中可见的 Embedding 选择真实映射到运行时启停状态。
- 在开始 AI 任务前用低成本检查暴露缺失配置和不可用依赖，避免“显示可用、运行时静默退化”。
- 为主 LLM 与 Embedding 提供语义明确、互不混淆的连接检查。
- 将 provider、模型、端点和维度纳入索引身份，切换模型后不复用不兼容旧索引。

## 非目标

- 本轮不增加模型下载器、索引构建进度或取消协议。
- 本轮不在结果报告中展示逐条语义召回来源与分数。
- 本轮不开放相似度阈值、Top-K 和 BM25 权重的专家调参 UI。
- 不改变现有精确、BM25、向量融合算法和术语优先级。

## 当前实现事实与约束

- `EmbeddingConfig.mode` 默认是 `disabled`，运行时仅在 mode 非 disabled 时初始化向量索引。
- AI 配置表单当前只写 `embedding.provider`，没有写 `embedding.mode`，且下拉框没有显式“关闭”选项。
- 现有“测试连接”只测试主 LLM；`preflight_ai_run` 不检查 Embedding 配置或 FAISS/本地模型依赖。
- 现有索引只按术语内容 hash 判定可复用；保存的 dimension 未参与加载校验，也没有 provider/model/base URL 指纹。
- 当前工作区另有 AI 翻译版本快照改动；本计划避免修改其新增文件，并尽量不触碰已重叠的窗口/报告文件。

## Story 1：真实三态配置与独立连接检查

### 验收标准

- UI 明确提供“关闭语义检索 / 本地模型 / API 服务”三态。
- 三态分别持久化为 `disabled`、`local`、`api`，provider 与 mode 保持一致。
- 加载旧配置时，`mode` 是事实来源；对历史非 disabled 模式保持兼容。
- 主 LLM 的“测试连接”文案不再暗示已检查 Embedding。
- Embedding 提供独立检查动作：disabled 返回提示；local 检查依赖和模型可用性；api 检查必要凭据并执行最小编码请求。

### 文件落点

- 修改 `src/transbridge/ui/tools/ai_translator/config_view.py`
- 修改 `src/transbridge/ui/tools/ai_translator/view_state.py`
- 修改 `src/transbridge/ui/tools/ai_translator/config_presenter.py`
- 修改 `src/transbridge/ui/tools/ai_translator/ai_translator_window.py`（仅新增独立回调，保留现有版本快照改动）
- 修改或新增 `tests/ui/tools/` 下聚焦配置测试

### 实施步骤

1. 为 Embedding 下拉框使用稳定 item data，而不是以显示文本或二态索引表达运行语义。
2. 调整表单显隐：disabled 隐藏所有后端字段；local 只显示本地模型；api 显示模型、Key、Base URL。
3. 在 ViewPort 映射中同时读写 mode/provider，并保留旧配置兼容。
4. 将连接测试拆为主 LLM 与 Embedding 两个用户动作，共享现有消息呈现边界。

### 测试策略

- Qt 表单加载/保存 round-trip：三态逐一验证 mode/provider。
- disabled/local/api 显隐测试。
- Embedding 检查的 disabled、缺配置、成功和异常分类测试，外部客户端使用 stub。

## Story 2：Embedding 运行前预检与明确降级

### 验收标准

- mode=api 且启用语义检索时，缺少有效 Key、模型或 Base URL 会在启动前给出可定位原因。
- mode=local 时，缺少 `sentence_transformers` 或 `faiss` 会在启动前提示。
- mode=api 时缺少 `faiss` 会在启动前提示。
- disabled 模式不探测、不导入向量后端，并继续允许不依赖语义检索的翻译工作流。
- BM25 缺失不阻断主流程；本轮保留运行时降级，后续报告 Story 再展示非阻断 warning。

### 文件落点

- 修改 `src/transbridge/ui/tools/ai_translator/run_spec.py`
- 修改 `tests/ui/tools/test_ai_translator_story08.py`
- 必要时修改 `src/transbridge/dependency_capabilities.py`，使 FAISS/本地 Embedding 能力进入统一基线

### 实施步骤

1. 扩展预检 code，区分 Embedding 配置缺失与依赖缺失。
2. 仅在翻译路径、术语检索启用且 mode 非 disabled 时执行 Embedding 预检。
3. 保持检查纯函数和无网络副作用，避免预检加载模型或调用 API。

### 测试策略

- 参数化覆盖 disabled、local、api 三态及依赖组合。
- 验证 disabled 不调用 dependency probe。
- 保持现有 LLM、作用域、源文件和 tiktoken 预检用例通过。

## Story 3：索引身份与模型切换失效

### 验收标准

- 索引 metadata 包含版本化 embedding fingerprint，至少覆盖 provider、mode、model/local model、归一化 Base URL 和 dimension。
- 加载索引时术语 hash 或 embedding fingerprint 任一不匹配都拒绝复用并重建。
- 旧 metadata 没有 fingerprint 时安全重建一次，不崩溃、不静默复用。
- 同一配置跨会话仍能复用索引。

### 文件落点

- 修改 `src/transbridge/ai_translator/term_vector_index.py`
- 新增 `src/transbridge/ai_translator/term_vector_manifest.py`，隔离版本化 metadata 与兼容校验职责
- 修改 `src/transbridge/infra/embedding_client.py`，为客户端暴露不含秘密的稳定身份信息
- 修改 `tests/ai_translator/test_term_vector_index.py`
- 修改 `tests/infra/test_embedding_client.py`

### 实施步骤

1. 在 EmbeddingClient 层提供稳定、无凭据的 index identity；避免 TermVectorIndex 反向读取具体客户端私有字段。
2. 对 identity 规范化并计算稳定 fingerprint，连同 schema version 写入 metadata。
3. 加载时先校验 schema、术语 hash、fingerprint 和维度，再读取并启用索引。

### 测试策略

- 同模型复用、模型变化、provider 变化、endpoint 变化、维度变化和旧 metadata 六类回归。
- 验证 metadata 与日志不包含 API Key。

## 依赖顺序

1. Story 1 先修复 UI 到配置的断连。
2. Story 2 复用 Story 1 的稳定 mode 语义补预检。
3. Story 3 独立于 UI，可与 Story 2 同轮完成，但最终以三态端到端测试收口。

## 风险与回退

- 将旧索引视为 stale 会触发一次重建；这是有意迁移行为，避免跨模型复用错误向量。
- 本地 Embedding 的真实连接检查可能触发首次模型下载；按钮文案和确认信息必须明确，自动预检不得加载模型。
- API 检查会产生一次最小 embedding 请求；不得在自动保存或预检期间隐式执行。
- 若当前窗口文件的版本快照改动与新增回调冲突，优先把动作编排下沉到现有 presenter/view port，避免覆盖用户工作。

## 后续轮次候选

- 模型下载与索引构建的可取消进度阶段。
- 索引状态卡、术语数量、更新时间、显式重建/清理动作。
- 运行报告中的检索模式、降级原因、召回来源和命中数量。
- 专家参数区：阈值、Top-K、BM25 权重及安全默认恢复。

## 明确假设

- “关闭”是默认安全状态，不因用户只选择 provider 而隐式开启。
- 缺少向量必需依赖时，本轮将其作为启用配置的启动阻断；用户可切换为“关闭”继续翻译。
- 独立点击 Embedding 检查属于用户明确授权的网络/本地模型初始化动作。

## 首轮实施结果

- Story 1 已完成：Embedding UI 采用 disabled/local/api 三态，并提供与主 LLM 分离的实际编码检查。
- Story 2 已完成：仅在启用语义检索的翻译路径执行无网络副作用的配置与依赖预检。
- Story 3 已完成：索引 metadata 升级到 schema v2，以无凭据 fingerprint 校验后端、模式、模型、端点和维度。
- 兼容行为：旧 metadata 或 Embedding 身份变化时安全重建一次；同一配置继续跨会话复用。
- QA：相关 138 项 pytest 通过；本次涉及的 17 个文件通过 Ruff check 与 format check。
- 仓库级 Ruff 基线仍有与本计划无关的既有错误及格式差异，本轮未扩大范围修复。
