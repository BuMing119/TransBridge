# G3 小组会谈纪要 -- 扩展工具与安全组

**日期**: 2026-05-11
**评审对象**: FR9 Agent 工具扩展方案 -- Stories 08, 09, 10, 11, 12 (P1 标签/翻译配置/后处理/ParaTranz + P2 解析/写回/项目查询) + 全局权限安全审查
**参与角色**: 安全专家（组长）/ 架构师 / 开发者

## 各角色独立意见摘要

### 安全专家（组长）
- **总体评价**: 保留意见（有条件通过）。FR9 工具扩展方案在权限分级上有 ADR-012 框架支撑，方向正确，但 MCP 通道安全隔离、密钥存储、输入校验覆盖度三个关键安全缺口需在编码前修复。
- **发现的问题/建议**:
  1. MCP 通道完全绕过安全护栏中间件链 -- 优先级: 高
  2. API Key/Token 明文存储于 INI 文件 -- 优先级: 高
  3. set_translation_config 权限不足且缺 URL 校验（base_url 可被注入指向恶意代理）-- 优先级: 高
  4. InputValidationGuard 缺乏路径遍历检测（parser/writer 大量接收路径参数，ADR-012 设计但未实现）-- 优先级: 高
  5. LLM 长运行工具 API 费用防护不足（translate_entries 无确认，start_translation 无费用预估）-- 优先级: 中
  6. 确认超时 300s 与 ADR-012 不一致（ADR 设计 60s，实际代码 300s）-- 优先级: 中
  7. OutputValidationGuard 嵌套 list 中敏感信息脱敏不完整（仅处理 dict 嵌套，未处理 list 内字符串）-- 优先级: 中
  8. ParaTranz API 无限流机制 -- 优先级: 低
  9. Reflexion 重试对非幂等写工具的幂等风险 -- 优先级: 低

### 架构师
- **总体评价**: 保留意见。Story 08/09 存在数据模型前置依赖断裂，Story 10/11 的数据源和语义设计需要澄清，Story 12 的 HITL 协议需完整定义。
- **发现的问题/建议**:
  1. ctx.label_library / ctx.entry_labels 不在 AppContext 上（Story 08 硬依赖，实际数据在 Step2PreviewWidget 上）-- 优先级: 高
  2. _translation_scope 临时属性设计缺乏接口契约 -- 优先级: 高
  3. parser 工具 HITL 机制协议闭环不完整 -- 优先级: 高
  4. get_quality_report 数据源缺失 -- 优先级: 中
  5. download_entries 两阶段设计语义裂痕（对比摘要后缺少实际下载触发路径）-- 优先级: 中
  6. sync_terms 未标记为 long_running -- 优先级: 低
  7. writer admin confirm 粒度太粗（4 个 writer 工具共用同一确认逻辑）-- 优先级: 低

### 开发者
- **总体评价**: 保留意见。实现层面存在数据访问路径断裂、代码重复、封装复杂度低估三个主要问题。
- **发现的问题/建议**:
  1. Story 08 标签数据访问路径缺失（与架构师一致）-- 优先级: 高
  2. 筛选逻辑在三处 Story 中重复实现（_filter_entries 公共函数缺失）-- 优先级: 高
  3. PostProcessor 封装复杂度被低估（初始化依赖多、方法签名不统一）-- 优先级: 中
  4. ParaTranz 工具需确认 API surface（可能需封装多个 API 子类，而非单薄包装）-- 优先级: 中
  5. parser 工具 HITL 机制缺乏实现细节 -- 优先级: 中
  6. write_back deprecated 转发参数兼容性 -- 优先级: 低
- **代码重复度评估**:
  - 高重复: 筛选逻辑（Story 04/08/10 中 _filter_entries 模式反复出现）+ PostProcessor 胶水代码
  - 低重复: Parser 6 工具（同构，可模板化生成）

---

## 组长总结

**总体评价**: G3 组对 FR9 Stories 08-12 持**保留意见（有条件通过）**。方案方向正确，ADR-012 安全护栏框架为权限分级提供了良好基础，但存在 **两大数据依赖断裂**（标签数据路径、translation_scope 契约）、**两项安全实现缺口**（MCP 中间件绕过、路径遍历检测）、和 **三处设计语义不完整**（HITL 协议、download_entries 两阶段、PostProcessor 封装）需在编码前解决。安全审查发现 MCP 通道存在独立的安全逻辑分叉，需统一到中间件链框架下。

---

## 共识项（多人共同发现）

### 1. Story 08 标签数据访问路径缺失
- **涉及角色**: 架构师（高优1）+ 开发者（高优1）
- **优先级**: 高
- **共同分析**: Story 08 的 `list_labels`/`create_label`/`assign_label` 等工具直接引用 `ctx._label_library` 和 `ctx._entry_labels`，但这两个属性当前仅存在于 `Step2PreviewWidget`（UI 层），不在 `AppContext` 上。Story 08 实施前必须先完成标签数据从 UI 层到 AppContext 的搬迁，否则工具函数在 MCP headless 模式下完全无法工作，即使在 GUI 模式下也需要通过 UI 组件间接访问。
- **代码验证**: `grep` 确认 `AppContext` 类（`src/transbridge/ui/context.py`）中无 `_label_library` 或 `_entry_labels` 属性；实际数据在 `src/transbridge/ui/workbench/step2.py:227-228`。
- **小组建议**: 将 `label_library` 和 `entry_labels` 提升为 `AppContext` 的一等属性（附带 `label_changed` pyqtSignal），Story 08 作为消费者、Story 03 的 ViewModel 扩展中追加此属性。标签数据的读写操作统一通过 AppContext 接口完成，UI 层订阅信号更新。

### 2. Parser 工具 HITL 机制协议不完整
- **涉及角色**: 架构师（高优3）+ 开发者（中优3）
- **优先级**: 高（采纳架构师评级）
- **共同分析**: Story 12 的 6 个 parser 工具均设计 `path` 参数为可选 -- 不传时触发 HITL 文件选择。但 Story 文档中未定义：(a) Agent 收到"请提供路径"响应后如何继续；(b) HITL 与 ReAct 循环的交互方式（阻塞等待 vs 异步回调 vs 新轮次）；(c) HITL 超时和取消策略；(d) headless/MCP 模式下 HITL 的降级行为（MCP 无 UI 通道）。
- **架构师补充**: 这是协议闭环问题 -- 工具的输入缺口通过"人工介入"填补，但人工介入的协议未被建模为工具系统的一等概念。
- **开发者补充**: 两种路径（有 path 直接执行 / 无 path HITL）的返回值格式需保持一致，但目前未定义统一格式。
- **小组建议**: 将"文件选择 HITL"建模为独立机制：`_request_user_input(request_type="file_selection", context={...})` 返回 `ToolResult` 或通过 ExecutionEngine 的确认管道发送。MCP 模式下自动降级为错误提示（"此工具需要 UI 环境选择文件"）。

### 3. ParaTranz 工具设计存在两处缺口
- **涉及角色**: 架构师（中优2）+ 开发者（中优2）
- **优先级**: 中
- **共同分析**:
  - **架构师**: `download_entries` 设计为"先执行对比，将对比摘要作为 message 返回"的两阶段模式，但对比摘要返回后实际下载动作的触发路径不明确 -- Agent 拿到摘要后应调用什么来执行下载？文档暗示框架层触发确认弹窗，但 Story 11 未定义确认弹窗如何回调到下载逻辑。
  - **开发者**: ParaTranz API surface 可能比预期复杂 -- `ParatranzClient` 当前封装相对简单，将 8 个工具全部映射到单一客户端类可能导致方法签名不匹配。需先做 API surface 审查，确认是否需要封装多个 API 子类（如 `ParatranzProjectAPI` / `ParatranzTermAPI` / `ParatranzExportAPI`）。
- **小组建议**: (a) `download_entries` 重构为单阶段：执行下载（后台线程 + TaskManager），返回前自动附加对比摘要到 `ToolResult.data`；(b) 实施前完成 Paratranz API surface 审查，产出 API-工具映射表。

---

## 补充项（单一角色发现，组长认可）

以下为安全专家独立发现的核心安全风险，虽未获得其他角色交叉验证，但经代码审计确认，组长认可为高优先级：

### S1. MCP 通道完全绕过安全护栏中间件链
- **发现者**: 安全专家（高优1）
- **代码确认**: `src/transbridge/smart_assistant/mcp/adapter.py:40` 中 `call_tool()` 直接调用 `spec.execute(arguments, self._ctx)`，完全绕过 `ExecutionEngine._run_single()` 中的中间件链（PermissionGuard / InputValidationGuard / OutputValidationGuard）。
- **当前缓解**: MCP adapter 有自己的 `_is_exposed()` 过滤（admin 需白名单、write 默认 deny），但这是粗粒度暴露控制，不是执行时安全校验。若用户将 `write_tool_policy` 设为 `allow`，则所有 write 工具在 MCP 通道中零护栏执行。
- **组长认可理由**: 安全架构中同一工具在 GUI 和 MCP 两个通道中经受的安全校验应当一致。当前两条路径的安全逻辑是独立的、不等价的，构成安全分叉（security fork），违反纵深防御原则。
- **建议**: MCP adapter 的 `call_tool()` 复用 ExecutionEngine 的中间件链，或将中间件链提取为独立组件（`GuardChain`），GUI 和 MCP 两条路径共享同一实例。MCP 模式下 admin 确认/ write 确认自动拒绝（无 UI 通道），由 `_is_exposed()` 做好事前过滤。

### S2. API Key/Token 明文存储于 INI 文件
- **发现者**: 安全专家（高优2）
- **代码确认**: `src/transbridge/config/llm.py:95` -- `c.set("llm", "api_key", self.api_key)` 直接将 API key 写入 configparser INI 文件，存储在 `data/paratranz_config.ini`。Embedding API key 同样处理（line 116）。
- **影响**: 任何能读取文件系统的进程/用户均可获取 API key。桌面应用场景下虽降低了网络攻击面，但恶意软件/物理访问/日志泄露仍是威胁向量。
- **建议**: 使用操作系统原生凭据存储（Windows Credential Manager `keyring` 库，零新依赖），或至少使用 DPAPI 加密。降级方案：使用 base64 混淆 + 文件权限限制（仅当前用户可读），并在首次读取后仅保留在内存中。

### S3. set_translation_config 权限不足且缺 URL 校验
- **发现者**: 安全专家（高优3）
- **分析**: Story 09 中 `set_translation_config` 标记为 `write` 权限，白名单中包含 `base_url`。攻击者可通过 Agent 将 `base_url` 修改为恶意代理地址（如 `http://evil-proxy.com/v1`），所有后续 LLM API 调用将经过该代理，导致 API key 和翻译数据泄露。
- **建议**: (a) 权限升级为 `admin`；(b) `base_url` 添加 URL 格式校验（仅允许 `https://` 协议、禁止 IP 地址、禁止 localhost）；(c) 修改 `base_url` 时强制 `require_confirmation=True` 并在确认弹窗中显示新旧 URL 对比。

### S4. InputValidationGuard 缺乏路径遍历检测
- **发现者**: 安全专家（高优4）
- **代码确认**: `src/transbridge/smart_assistant/guardrails/input_validator.py` 中的 `_INJECTION_PATTERNS` 仅覆盖 SQL 注入、XSS、命令注入，**未包含** `../` 路径遍历或绝对路径检测。ADR-012 设计了 `_detect_path_traversal(args)`（ADR 文档第 100 行），但实际代码中**未实现**。
- **影响**: Story 12 的 parser/writer 工具接收文件路径参数，若路径遍历检测缺失，恶意输入可能读写工作目录外的文件。
- **建议**: 在 `InputValidationGuard` 中追加 `_detect_path_traversal()` 方法，检测 `../`、`..\\`、绝对路径（Unix `/etc/`、Windows `C:\`），并按工具类型分级：read 工具拒绝遍历路径，write/admin 工具拒绝所有非项目目录内的路径。

其他补充项：

### S5. _translation_scope 临时属性缺乏接口契约
- **发现者**: 架构师（高优2）
- **组长评级**: 中（Story 09 明确标注为"会话内临时数据，不持久化"，影响范围可控）
- **建议**: 至少添加 property getter/setter，在 setter 中做基本类型校验（stages 为 list[int]、action 为枚举值），防止 Agent 写入格式错误的作用域数据。

### S6. 筛选逻辑重复实现
- **发现者**: 开发者（高优2）
- **组长评级**: 中（属于代码质量/可维护性问题，非功能阻塞项）
- **建议**: 在 Story 04 实施时提取 `_apply_filter(collection, filter_state) -> list[entry_id]` 公共函数，后续 Story 08/10 复用。

---

## 安全专项汇总

作为安全组长，对 FR9 工具扩展方案及现有实现做全局安全审查：

### 一、MCP 通道安全审计

| 检查项 | 状态 | 详情 |
|--------|------|------|
| 工具暴露控制 | 部分通过 | `_is_exposed()` 有 admin 白名单和 write 策略过滤，默认保守 |
| 执行时权限校验 | **失败** | 绕过 PermissionGuard，无 per-invocation 权限检查 |
| 输入校验 | **失败** | 绕过 InputValidationGuard，无注入检测/路径遍历/长度限制 |
| 输出脱敏 | **失败** | 绕过 OutputValidationGuard，API key 可能从 MCP 通道泄露 |
| 用户确认机制 | **N/A** | MCP 无 UI 通道，需设计降级策略 |
| 审计日志 | **缺失** | MCP 调用无日志记录，不可追溯 |

**结论**: MCP 通道存在独立的安全逻辑分叉。GUI 路径经过完整中间件链（PermissionGuard -> InputValidationGuard -> 工具执行 -> OutputValidationGuard），MCP 路径仅经过粗粒度暴露过滤后直达工具执行。**建议**: 将中间件链抽取为 `GuardChain` 独立组件，GUI 和 MCP 两条路径共享。

### 二、密钥与凭证安全

| 检查项 | 状态 | 详情 |
|--------|------|------|
| API Key 存储 | **失败** | INI 明文存储（`config/llm.py:95`）|
| Embedding Key 存储 | **失败** | INI 明文存储（`config/llm.py:116`）|
| Paratranz Token 存储 | 待确认 | ParatranzConfig 共享同一 INI 文件 |
| Key 在日志中泄露 | 部分通过 | OutputValidationGuard 有 `_redact_sensitive()` 但仅覆盖 Top-level 和 dict 嵌套 |
| Key 在 MCP 通道泄露 | **失败** | MCP adapter 未经 OutputValidationGuard |

### 三、输入校验覆盖度

| 校验类型 | ADR-012 设计 | 实际实现 | 差距 |
|----------|-------------|---------|------|
| SQL 注入检测 | 是 | 已实现 | 无 |
| XSS 检测 | 是 | 已实现 | 无 |
| 命令注入检测 | 是 | 已实现 | 无 |
| 路径遍历检测 | **是（ADR 第100行）** | **未实现** | 高 |
| 类型校验 | 是 | 已实现 | 无 |
| 长度限制 | 是 | 已实现（100KB 全局上限）| 无 |
| 列表项数限制 | 是（500 项） | 未实现 | 低 |
| URL 格式校验 | 未设计 | 未实现 | 新增需求 |

### 四、权限分级一致性

| 检查项 | 状态 | 详情 |
|--------|------|------|
| story-08: 标签工具权限 | 通过 | list_labels=read, create/assign/remove=write, batch_assign=write+confirm |
| story-09: set_translation_config | **问题** | 应为 admin（可改 base_url），当前标记 write |
| story-09: set_scope | 通过 | write，无确认（影响范围预览已提供）|
| story-10: run_llm_arbitration | 通过 | 已修正为 write（产生 LLM 费用）|
| story-10: run_consistency_check | 通过 | read（规则检查，无 LLM 调用）|
| story-11: upload/download | 通过 | write + long_running，download 额外 require_confirmation |
| story-11: sync_terms | **疑问** | 标记 write 但未标记 long_running，术语同步可能慢 |
| story-12: parser 工具 | **问题** | 标记 write，但解析操作是纯读取文件系统，应为 read；加载结果到 ctx 的副作用不改变外部状态 |
| story-12: writer 工具 | 通过 | admin + require_confirmation，最高安全级别 |

### 五、运行时可观测性安全

| 检查项 | 状态 | 详情 |
|--------|------|------|
| 敏感信息记录到遥测 | 部分通过 | ConversationTrace 记录 tool input/output summary（截断至 500 字符），需确保脱敏后再记录 |
| 遥测文件访问控制 | 待实现 | `data/projects/{project}/{variant}/observability/` 下的 JSON 文件未设权限限制 |
| 令牌用量统计 | 通过 | TokenStats 记录用量，无密钥泄露风险 |

### 六、攻击面汇总

| 攻击向量 | 风险等级 | 当前缓解 | 建议 |
|----------|---------|---------|------|
| MCP 通道工具滥用 | 高 | write_policy 默认 deny | 统一中间件链 |
| INI 文件密钥窃取 | 高 | 桌面应用降低网络攻击面 | keyring 加密 |
| base_url 注入 | 高 | 无 | admin 权限 + URL 校验 |
| 路径遍历文件读写 | 高 | 无（设计但未实现）| 实现 _detect_path_traversal |
| LLM 费用滥用 | 中 | translate_entries 无确认 | 添加费用预估 + 可选确认 |
| 确认超时线程耗尽 | 中 | 300s 过长 | 降为 60s（与 ADR-012 一致）|
| API 无限流调用 | 低 | 桌面应用单用户 | 添加速率限制 |
| Reflexion 重复写 | 低 | 重试上限 3 次 | 写工具标记 non_retryable |

---

## G3 小组结论与建议清单

### 高优先级（必须在 FR9 编码前解决）

- [ ] **MCP 中间件链统一**（安全专家发现）: 提取 GuardChain 独立组件，MCP adapter.call_tool() 与 ExecutionEngine._run_single() 共享同一中间件链实例。MCP 模式下 admin/write 确认自动拒绝（无 UI 通道），由 _is_exposed() 做好事前过滤。
- [ ] **标签数据迁移到 AppContext**（架构师+开发者共识）: 将 `label_library` 和 `entry_labels` 从 Step2PreviewWidget 提升到 AppContext，附带 `label_changed` pyqtSignal。Story 03 的 ViewModel 扩展中追加此属性。
- [ ] **API Key 安全存储**（安全专家发现）: 使用 `keyring` 库（零新依赖）存储 API key，替代 INI 明文。降级方案：首次加载后仅保留在内存中。
- [ ] **set_translation_config 权限升级 + URL 校验**（安全专家发现）: (a) 权限 write -> admin；(b) base_url 添加格式校验（仅 https://、禁 IP/localhost）；(c) 修改 base_url 时 require_confirmation=true 并展示新旧对比。
- [ ] **InputValidationGuard 路径遍历检测**（安全专家发现）: 实现 ADR-012 设计的 `_detect_path_traversal()` 方法，检测 `../`、`..\\`、绝对路径；write/admin 工具拒绝所有非项目目录内路径。
- [ ] **Parser HITL 协议完整定义**（架构师+开发者共识）: 将文件选择 HITL 建模为独立机制，定义 ReAct 循环交互方式、超时策略、MCP 降级行为。产出 HITL 协议规格文档。
- [ ] **调整 parser 工具权限分级**（安全专家发现）: `parse_esp/eet/xt/sst/import_json/import_strings` 当前标记 write，但解析操作本质是读取文件系统，应为 read。加载结果到 ctx 的副作用不改变外部文件状态。

### 中优先级（建议在 Story 实施阶段解决）

- [ ] **_translation_scope 接口契约化**（架构师发现）: 添加 property getter/setter，在 setter 中做类型校验（stages 为 list[int]、action 为枚举值）。
- [ ] **确认超时修正**（安全专家发现）: ExecutionEngine 确认等待超时从 300s 修正为 60s（与 ADR-012 一致），防止 ThreadPoolExecutor 线程池耗尽。
- [ ] **OutputValidationGuard 嵌套脱敏增强**（安全专家发现）: `_redact_dict()` 追加对 list 类型值的递归处理，覆盖 `data.items: [str, str, ...]` 中的敏感信息。
- [ ] **筛选逻辑公共函数提取**（开发者发现）: 在 Story 04 实施时提取 `_apply_filter(collection, filter_state) -> list[entry_id]`，Story 08/10 复用。
- [ ] **download_entries 流程重构**（架构师发现）: 单阶段执行（下载 + 自动附加对比摘要），消除两阶段语义裂痕。
- [ ] **PostProcessor 封装前审查**（开发者发现）: 实施 Story 10 前完成 PostProcessor 类的初始化依赖和方法签名审查，评估是否需要适配层。
- [ ] **ParaTranz API surface 审查**（开发者发现）: 产出 API-工具映射表，确认单一 ParatranzClient 能否覆盖 8 个工具。

### 低优先级（可在后续迭代中处理）

- [ ] **ParaTranz API 速率限制**（安全专家发现）: 添加简单令牌桶限流器（如每秒最多 10 请求），防止误操作触发 API 限流封禁。
- [ ] **Reflexion 写工具幂等标记**（安全专家发现）: 在 ToolSpec 添加 `non_retryable: bool` 字段，writer/admin 工具默认 True，Reflexion 重试时跳过。
- [ ] **writer admin confirm 粒度细化**（架构师发现）: 4 个 writer 工具各自独立确认提示（显示目标文件路径），而非共用一个通用提示。
- [ ] **sync_terms 耗时评估**（架构师发现）: 确认术语同步的实际耗时，决定是否需要标记为 long_running。
- [ ] **write_back deprecated 参数兼容**（开发者发现）: 确认转发参数映射（`mode` vs 原 `target`），添加兼容映射表。
- [ ] **遥测文件权限限制**（安全专家发现）: observability JSON 文件设置仅当前用户可读写。

---

## 带到组间交流的核心议题

1. **标签数据归属问题**: `label_library` / `entry_labels` 当前在 UI 层（Step2PreviewWidget），G3 建议提升到 AppContext。G1（基础设施组）的 Story 03 负责 AppContext ViewModel 扩展，需协调：G1 是否愿意在 Story 03 中追加标签数据搬迁任务？还是由 G3（或 G2）单独处理？

2. **MCP 安全统一**: G3 发现 MCP 通道存在独立的安全逻辑分叉，绕过 ExecutionEngine 的中间件链。G1 的 Story 01（ToolResult + 装饰器）+ Story 13（ExecutionEngine 适配）是解决此问题的基础设施。建议 G1 将 GuardChain 提取纳入 Story 01 或 Story 13 范围。

3. **parser 工具权限分级争议**: G3 认为 parser 6 工具（parse_esp/eet/xt/sst/import_json/import_strings）当前标记 write 不合理，解析操作是纯读取文件系统。G2（翻译与后处理组）可能有不同意见（parse 操作会修改 ctx 状态）。需组间对齐权限分级的判断标准：**以"是否改变 AppContext 之外的系统状态"还是"是否改变 AppContext 内部状态"为 write 的分界线**？

4. **InputValidationGuard 路径遍历检测的实现归属**: ADR-012 设计了路径遍历检测但未实现。G3 可在安全审查中实现，但 G1（基础设施组）的 Story 01 涉及 `@validate_params` 装饰器，可能需要协调输入校验层的职责划分。

5. **ParaTranz API surface 全景**: G3 开发者提出 ParaTranz 工具可能需要对现有 ParatranzClient 进行扩展或拆分为多个 API 子类。建议与 ParaTranz 集成经验的开发者（可能在 G2 或其他组）确认当前 API 封装的实际覆盖度。

---

## 纪要不构成决议

本文件仅为 G3 小组（扩展工具与安全组）三位成员独立意见的客观汇总与组长综合分析记录。最终决策需用户裁决并纳入 FR9 方案 v2。
