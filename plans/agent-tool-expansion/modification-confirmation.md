# FR9 方案修改确认书

**基于**: `docs/council-review-fr9-tool-allocation.md`（9 人评审综合纪要） + `docs/council-review-fr9-g3-tools-safety.md`（G3 安全组纪要）
**确认日期**: 2026-05-11
**确认方式**: 逐项确认（38 项），含用户方案调整
**状态**: 已确认，待据此更新 plan.md 及各 Story 文档

---

## 一、确认结果总览

| 优先级 | 总数 | 确认 | 跳过 | 用户修改方案 |
|--------|------|------|------|-------------|
| P0 阻塞级 (B) | 6 | 6 | 0 | 0 |
| P0 高优先级 (H) | 9 | 9 | 0 | 1 (H7) |
| P1 增强 (E) | 12 | 10 | 2 (E2, E11) | 1 (E11) |
| P2 优化 (O) | 11 | 10 | 1 (O4→撤回确认) | 1 (O5) |
| **合计** | **38** | **35** | **3** | **4 项调整** |

---

## 二、与委员会建议的差异项

| 编号 | 项目 | 委员会建议 | 用户决定 | 理由 |
|------|------|-----------|---------|------|
| **H7** | set_translation_config 的 base_url 安全 | 从白名单移除，或升为 admin + 域名白名单 | **预设端点方案切换** — 用户在 INI 中预配置多套 API 端点方案，AI 只能通过 `profile` 参数切换预设方案，不能自由输入 URL | 比委员会方案更灵活——既保留 Agent 切换端点的能力（如翻译用 OpenAI、校对用 Anthropic），又堵住任意 URL 注入风险 |
| **E2** | API Key/Token 加密存储 | 使用 keyring 库加密存储 | **不加密** — 保持 INI 明文 | 桌面个人应用，物理安全边界足够；用户自行负责机器安全 |
| **E11** | 确认超时 300s→60s | 改为 60s 对齐 ADR-012 | **可配置** — 某些操作需设为无限等待，而非一律 60s | 桌面单用户场景，用户可能离开较久；应支持按操作类型配置超时 |
| **O4** | ParaTranz API 请求频率限制 | 低优先级，后续迭代 | **确认需要** — 加上令牌桶限流 | 防止误操作触发 API 封禁 |
| **O5** | Reflexion 对非幂等写工具禁用重试 | 写工具标记 non_retryable | **允许但限制** — 允许重试但需条件控制 | 不应一刀切禁止，应在具体流程中判断是否允许重试 |

---

## 三、逐项确认详情

### P0 阻塞级（编码前必须解决）

#### B1 — 标签数据上移至 AppContext

- **做什么**: 将 `label_library`（标签库）和 `entry_labels`（条目-标签映射）从 UI 层 `Step2PreviewWidget` 提升到 `AppContext` 作为一等属性，附带 `label_data_changed` pyqtSignal
- **现状**: VariantStore 已有标签持久化（`variant_store.py:28-29`），但运行时数据仅在 UI 层（`step2.py:227-228`），Agent 工具无法访问
- **优劣**: 👍 标签成为全局可访问数据，MCP 模式可用，UI 与数据解耦 / 👎 AppContext 职责增加，Step2 标签 UI 需适配重构，额外工作量 1-2h
- **涉及 Story**: Story 03 范围扩大
- **决定**: ✅ 确认

#### B2 — ToolResult 字典兼容

- **做什么**: 给 `ToolResult` dataclass 添加 `get(key, default)` 和 `__getitem__` 方法，兼容旧代码 `raw_result.get("success", True)` 调用模式
- **现状**: `execution_engine.py:149` 用 `raw_result.get("success", True)` 访问返回值，Story 01 把返回值从 dict 改为 ToolResult 后必然 AttributeError
- **优劣**: 👍 零改动兼容所有旧代码，Story 01→13 无破坏性窗口 / 👎 字典兼容是过渡方案，Story 13 后应移除
- **涉及 Story**: Story 01
- **决定**: ✅ 确认（方案 A：添加 get()/__getitem__）

#### B3 — ToolResult.success 语义修正

- **做什么**: 把 `success` 从三态 `Literal[True, False, "partial"]` 改为 `success: bool` + 新增独立字段 `partial: bool = False`
- **现状**: `"partial"` 是 truthy 字符串，`if result.success` 会把部分成功误判为完全成功；所有下游消费方都是二态判断
- **优劣**: 👍 消除误判 bug，下游代码零改动 / 👎 需修改所有创建 ToolResult 的地方（把 `success="partial"` 改为 `success=True, partial=True`）
- **涉及 Story**: Story 01
- **决定**: ✅ 确认（方案 1：bool + partial 独立字段）

#### B4 — ExecutionContext 提前至 Story 01

- **做什么**: 把 `ExecutionContext`（含 `__getattr__` 代理，未命中属性自动转发到内部 `AppContext`）从 Story 13 提前到 Story 01 实现
- **现状**: v1 工具签名 `func(args, ctx)` 接收裸 AppContext，新工具接收 ExecutionContext。用参数数量区分不可行（都是 2 参数）
- **优劣**: 👍 v1 工具零改动兼容新 ExecutionContext；Story 13 范围缩减 / 👎 Story 01 工作量增加
- **涉及 Story**: Story 01 范围扩大，Story 13 范围缩减
- **决定**: ✅ 确认

#### B5 — 移除 pause_task 工具

- **做什么**: 从 Story 06 删除 `pause_task` 工具。TaskHandle 预留 `pause_event` 字段但不实现。真实暂停/恢复列入 P2 后续迭代（O11）
- **现状**: `pause_task` 仅修改状态标记为 "paused"，实际翻译线程仍在运行——status 和实际行为不同步，API 费用持续消耗
- **优劣**: 👍 消除"假暂停"误导用户风险 / 👎 用户暂时失去暂停翻译功能
- **涉及 Story**: Story 06 范围缩减
- **决定**: ✅ 确认（方案 A：P0 移除 pause_task）

#### B6 — MCP 护栏中间件注入点预留

- **做什么**: 在 Story 01 `tools/base.py` 中提供 `execute_with_guardrails()` 统一入口，将 PermissionGuard → InputValidationGuard → 工具执行 → OutputValidationGuard 中间件链提取为独立组件
- **现状**: MCP 通道 `adapter.py:40` 直接调用 `spec.execute()`，完全绕过安全护栏。GUI 和 MCP 存在安全分叉。G3 安全审查评为 **Critical**
- **优劣**: 👍 消除安全分叉，MCP 和 GUI 走同一套安全校验 / 👎 Story 01 工作量增加；MCP 无 UI 通道，admin/write 确认需设计降级策略
- **涉及 Story**: Story 01 范围扩大，Story 13 MCP 护栏接入
- **决定**: ✅ 确认

---

### P0 高优先级（应在编码阶段解决）

#### H1 — Story 05 合并至 Story 04

- **做什么**: 将原 Story 05（select_entries、edit_translation 编辑工具）合并到 Story 04（筛选与搜索工具），保持编号 Story 04。合并后统一负责「筛选→选择→编辑→标记」完整操作链
- **优劣**: 👍 减少跨 Story 依赖和等待，形成完整闭环 / 👎 Story 04 内容变多，单次实现时间增加
- **涉及 Story**: Story 04 合并 Story 05，原 Story 05 编号废弃
- **决定**: ✅ 确认

#### H2 — select_entries 改用独立 _selected_ids 集合

- **做什么**: Agent 的条目选择从"打 `__agent_selected__` 标签"改为维护独立的 `_selected_ids: set[str]` 存储在 AppContext 上，与用户标签系统完全隔离
- **现状**: 用标签做选择会导致：① `__agent_selected__` 污染 UI 标签筛选栏；② 持久化到 VariantStore 导致下次加载残留；③ 用户删除此标签导致 Agent 功能失效
- **优劣**: 👍 Agent 选择和用户标签完全隔离互不污染 / 👎 AppContext 多维护一个集合
- **涉及 Story**: Story 04（合并后）
- **决定**: ✅ 确认

#### H3 — 新增 set_stage 工具

- **做什么**: 新增 `set_stage` 工具（参数：`entry_ids: list[str]`, `stage: int`），注册到 `editor` namespace，支持批量设置翻译阶段
- **现状**: Agent 翻译 500 条后要批量设为 stage=1，唯一途径是逐条调用 `edit_translation`（还要传入 `new_translation` 全文），性能/语义均不可接受
- **优劣**: 👍 填补批量标记缺口，工作流形成闭环 / 👎 新增一个工具，需配套权限和测试
- **涉及 Story**: Story 04（合并后）
- **决定**: ✅ 确认

#### H4 — edit_translation 增加可选 new_stage 参数

- **做什么**: `edit_translation` 工具增加可选参数 `new_stage: int | None = None`。不传时保持现有 stage 不变（无副作用），传入时显式设定
- **现状**: 硬编码自动设 stage=2（有疑问），但 Agent 翻译空条目的场景下 stage 应为 1（已翻译），硬编码语义错误
- **优劣**: 👍 灵活适配多种场景，与 H3 的 set_stage 互补 / 👎 调用方需了解 stage 值含义
- **涉及 Story**: Story 04（合并后）
- **决定**: ✅ 确认

#### H5 — 统一 HITL 协议

- **做什么**: 在 Story 01 `tools/base.py` 中定义轻量级 `HITLRequest`/`HITLResponse` 数据类，覆盖三种场景：① confirm（确认弹窗）、② file_select（parser 文件选择）、③ compare_confirm（下载对比确认）
- **现状**: 两套互不兼容的 HITL 机制——ExecutionEngine 的 `step_requires_confirmation`（仅支持继续/跳过）和 parser 工具的隐式文件选择，MCP 降级行为未定义
- **优劣**: 👍 统一协议，所有 HITL 走同一交互模型；MCP 降级行为统一定义 / 👎 Story 01 工作量增加
- **涉及 Story**: Story 01 范围扩大
- **决定**: ✅ 确认

#### H6 — Parser 6 工具权限改为 read

- **做什么**: 将 `parse_esp`/`parse_eet`/`parse_xt`/`parse_sst`/`import_json`/`import_strings` 六个 parser 工具的权限从 `write` 改为 `read`
- **判定标准**: `write` = 对持久化数据或外部系统产生不可逆副作用。解析本质是读取文件系统，加载到 ctx 不改变外部文件
- **优劣**: 👍 权限分级更精准，降低 Agent 使用摩擦 / 👎 需同步审查 `clear_all_filters` 权限
- **涉及 Story**: Story 12
- **决定**: ✅ 确认（改为 read）

#### H7 — set_translation_config 的 base_url 安全处理

- **做什么**: 用户可在 INI 配置文件中预设多套 API 端点方案（profile），Agent 通过 `profile` 参数切换预设方案，**不能自由输入 URL**
- **攻击场景**: 攻击者可通过 Agent 将 `base_url` 指向恶意代理 `http://evil-proxy.com/v1`，截获所有 LLM API Key 和翻译原文
- **方案设计**:
  ```
  [llm_profiles]
  openai = https://api.openai.com/v1
  anthropic = https://api.anthropic.com
  local_proxy = https://my-proxy.local:8080/v1

  Agent 工具 set_translation_config：
    ✅ profile="anthropic" → 切换到预设端点
    ❌ base_url="http://黑客.com/v1" → 不在预设列表，拒绝
  ```
- **优劣**: 👍 比完全移除更灵活（Agent 可切换端点），比 admin 确认更安全（无法指向未授权地址） / 👎 需新增 `[llm_profiles]` 配置节
- **涉及 Story**: Story 09（新增 profile 切换逻辑，移除 base_url 自由输入）
- **决定**: ✅ 确认（**用户方案：预设端点方案切换**，替代委员会建议）

#### H8 — 提取 _filter_entries() 公共函数

- **做什么**: 提取 `_filter_entries(collection, filter_state) -> list[TranslationEntry]` 公共函数放在 `tools/base.py`，供 Story 04/08/10 三处复用
- **现状**: 筛选逻辑在 `get_visible_entries`、`batch_assign_label`、`get_scope_preview`/`set_scope` 三处重复实现
- **优劣**: 👍 消除代码重复，筛选行为统一 / 👎 需先定义接口，后续 Story 依赖此接口
- **涉及 Story**: Story 01（定义）+ Story 04/08/09（使用）
- **决定**: ✅ 确认

#### H9 — v1 工具签名兼容（ExecutionContext.__getattr__ 代理）

- **做什么**: `ExecutionContext` 添加 `__getattr__` 方法，未命中属性自动转发到内部 `AppContext` 查找。v1 工具访问 `ctx.esp_path` → ExecutionContext 没有 → 自动转发到 AppContext.esp_path
- **优劣**: 👍 v1 工具零改动兼容新 ExecutionContext / 👎 代理可能掩盖属性查找错误（拼写错误无法立即发现）
- **涉及 Story**: Story 01（与 B4 一起实现）
- **决定**: ✅ 确认

---

### P1 增强（可在实施过程中逐步解决）

#### E1 — 路径遍历检测

- **做什么**: 在 `InputValidationGuard` 中补全 `_detect_path_traversal()` 方法，检测 `../`、`..\\`、绝对路径注入。对 parser/writer 工具的文件扩展名添加白名单（`.esp`/`.esm`/`.esl`/`.xml`/`.json`/`.strings`）
- **现状**: ADR-012 设计了此检测（第 100 行）但代码未实现。parser/writer 大量接收文件路径参数
- **优劣**: 👍 堵住路径遍历攻击面 / 👎 Story 01 仅做基础版，Story 12 强化
- **涉及 Story**: Story 01（基础版）+ Story 12（强化）
- **决定**: ✅ 确认

#### E2 — API Key/Token 加密存储

- **做什么**: ~~使用 keyring 加密存储 API Key~~
- **现状**: `config/llm.py:95` INI 明文存储
- **决定**: ❌ **跳过** — 桌面个人应用，用户自行负责机器安全，保持 INI 明文

#### E3 — TaskManager 线程安全强化

- **做什么**: `get_status()` 返回 progress dict 时深拷贝后再返回；单例使用模块级双重检查锁防止竞态条件
- **优劣**: 👍 消除并发崩溃风险 / 👎 深拷贝对大进度数据有微小性能开销
- **涉及 Story**: Story 02
- **决定**: ✅ 确认

#### E4 — Orchestrator map_to_steps() 修复

- **做什么**: `Orchestrator.map_to_steps()` 当前始终取 `tools[0]`，改为从 LLM action 字段映射到具体 tool_name
- **现状**: LLM 决定调用 `edit_translation` 时，如果 `tools[0]` 是 `filter_by_stage`，会错误执行 filter 而非 edit
- **优劣**: 👍 修复多工具场景下的工具路由错误
- **涉及 Story**: Story 13
- **决定**: ✅ 确认

#### E5 — 装饰器堆叠顺序文档化

- **做什么**: 在 `base.py` 文档字符串中明确推荐装饰器顺序：`@require_collection` 在最外层，`@validate_params` 在内层。或提供 `@tool_pipeline(schema)` 组合装饰器
- **优劣**: 👍 统一规范，避免装饰器顺序 bug / 👎 纯文档工作
- **涉及 Story**: Story 01
- **决定**: ✅ 确认

#### E6 — filter_state 与 Step2 格式映射契约

- **做什么**: 在 Story 03 中明确 `search_field`（Agent 统一搜索字段）与 Step2 三个独立搜索框（ID/Key/Text）的映射契约
- **优劣**: 👍 避免 Agent 搜索请求与 UI 搜索框格式不一致 / 👎 纯契约文档
- **涉及 Story**: Story 03
- **决定**: ✅ 确认

#### E7 — stop_task 去掉全部停止隐式语义

- **做什么**: `stop_task` 不传 `task_id` 时不再隐式停止所有任务。新增独立 `stop_all_tasks` 工具
- **现状**: 不传参数 = 全部停止是隐式行为，容易被 Agent 误触发
- **优劣**: 👍 防止误操作，语义显式清晰 / 👎 多一个工具
- **涉及 Story**: Story 06
- **决定**: ✅ 确认

#### E8 — _translation_scope 纳入正式属性

- **做什么**: 将 `_translation_scope` 纳入 AppContext 或 ExecutionContext 正式属性，添加 property getter/setter + 类型校验（stages 为 list[int]、action 为枚举值）
- **优劣**: 👍 类型安全，防止 Agent 写入格式错误的作用域数据 / 👎 AppContext 多一个非持久化属性
- **涉及 Story**: Story 03 或 Story 09
- **决定**: ✅ 确认

#### E9 — PostProcessor 胶水代码工厂函数

- **做什么**: 提取 `_run_postprocess_phase()` 工厂函数，减少 `run_llm_arbitration`、`run_consistency_check` 等 long_running 工具的 PostProcessor 胶水代码重复
- **优劣**: 👍 减少重复，统一后处理调用模式
- **涉及 Story**: Story 10
- **决定**: ✅ 确认

#### E10 — LLM 后处理工具 require_confirmation

- **做什么**: 为 `run_llm_arbitration`、`run_llm_refinement`、`run_llm_polish` 等 LLM 后处理工具设置 `require_confirmation=True`，确认提示显示预估条目数和费用
- **现状**: 无确认，用户在不知情的情况下可能触发大量 LLM API 费用
- **优劣**: 👍 用户知情并控制 API 费用 / 👎 增加一次交互确认
- **涉及 Story**: Story 10
- **决定**: ✅ 确认

#### E11 — 确认超时配置化

- **做什么**: ~~确认超时 300s→60s~~ → 改为**可配置**：不同操作类型支持不同超时策略，必要时可设为无限等待（不超时）
- **现状**: 300s 超时，线程池可能被长时间占用
- **决定**: ⚠️ **修改方案** — 不采用委员会的 60s 硬编码，改为按操作类型可配置。某些关键确认（如 admin 写回）可能需要用户长时间思考，不应强制超时

#### E12 — 输出脱敏嵌套 list 递归处理

- **做什么**: `OutputValidationGuard._redact_dict()` 增加对 list 类型的递归处理，覆盖 `data.items: [str, str, ...]` 中嵌套在列表内的敏感信息
- **现状**: 仅处理 dict 嵌套，list 内的字符串直接跳过
- **优劣**: 👍 堵住脱敏盲区 / 👎 对大型嵌套数据有轻微性能开销
- **涉及 Story**: Story 01 或独立安全 PR
- **决定**: ✅ 确认

---

### P2 优化（后续迭代处理）

#### O1 — 新增 Story 14 集成测试

- **做什么**: 新增跨 Story 集成测试 Story，覆盖完整链路（筛选→选择→翻译→标记）
- **决定**: ✅ 确认

#### O2 — ToolSpec/ToolRegistry 移入 tools/ 子包

- **做什么**: `tool_registry.py` 变为重导出壳，实际实现移到 `tools/` 子包
- **决定**: ✅ 确认

#### O3 — AgentSpec 工具列表支持 namespace:* 通配符

- **做什么**: Agent 配置支持 `tools: ["filter:*", "editor:set_stage"]` namespace 通配符
- **决定**: ✅ 确认

#### O4 — ParaTranz API 请求频率限制

- **做什么**: 添加令牌桶限流器（如每秒最多 10 请求），防止误操作触发 API 封禁
- **决定**: ✅ **确认**（用户认为需要，从"跳过"撤回）

#### O5 — Reflexion 对写工具的重试策略

- **做什么**: ~~写工具标记 non_retryable~~ → 改为**允许重试但需条件限制**：不在 ToolSpec 层一刀切禁止，在具体流程中判断是否允许重试
- **决定**: ⚠️ **修改方案** — 允许重试，实现时根据操作上下文和重试次数动态判断

#### O6 — 结构化护栏审计日志

- **做什么**: 记录每次护栏触发（权限拒绝、输入校验拦截、输出脱敏）的结构化日志，含时间戳、工具名、触发规则、输入摘要
- **决定**: ✅ 确认

#### O7 — download_entries 流程重构

- **做什么**: 从"先对比→返回摘要→等确认→再下载"两阶段隐式设计，拆分为 `compare_with_remote` + `apply_download` 或合并为单阶段
- **决定**: ✅ 确认

#### O8 — get_collection_summary 标记 deprecated

- **做什么**: `get_collection_summary` 标记废弃，功能合并到 `get_statistics`
- **决定**: ✅ 确认

#### O9 — TaskManager 线程引用跟踪

- **做什么**: TaskManager 跟踪所有线程引用，cleanup 时确保 join，daemon=True 防止阻止进程退出
- **决定**: ✅ 确认

#### O10 — ParaTranz 工具 API surface 确认

- **做什么**: 实施 Story 11 前审查 `ParatranzClient` 完整 API，列出 8 个工具的确切方法调用路径
- **决定**: ✅ 确认

#### O11 — 真实暂停/恢复机制（P2 后续）

- **做什么**: 实现真正的翻译暂停/恢复（`pause_event: threading.Event`），让 AutoTranslator 在暂停时真正停止 API 调用
- **决定**: ✅ 确认（与 B5 配套，B5 移除了假暂停，O11 在未来实现真暂停）

---

## 四、Story 范围变更汇总

基于以上确认，需对 `plan.md` 各 Story 做如下调整：

| Story | 变更类型 | 变更内容 |
|-------|---------|---------|
| **Story 01** | 范围扩大 | +ToolResult 字典兼容 (B2) +success 语义修正 (B3) +ExecutionContext (B4) +HITL 协议 (H5) +execute_with_guardrails (B6) +__getattr__ 代理 (H9) +基础路径遍历 (E1) +装饰器顺序文档 (E5) +输出脱敏 list 递归 (E12) +_filter_entries 公共函数 (H8) |
| **Story 02** | 范围扩大 | +线程安全深拷贝 (E3) +单例双重检查锁 (E3) +线程引用跟踪 (O9) |
| **Story 03** | 范围扩大 | +标签数据上移: label_library/entry_labels/label_data_changed (B1) +_translation_scope 正式属性 (E8) +filter_state 映射契约 (E6) |
| **Story 04** | 合并+扩大 | 合并原 Story 05 +set_stage 工具 (H3) +selected_ids 独立集合 (H2) +new_stage 参数 (H4) +_filter_entries 复用 (H8) |
| **Story 05** | **废弃** | 合并至 Story 04，原编号保留但内容并入 04 |
| **Story 06** | 范围缩减 | -pause_task (B5)，TaskHandle 预留 pause_event；stop_task 拆分为 stop_task + stop_all_tasks (E7) |
| **Story 07** | 调整 | get_collection_summary deprecated→合并到 get_statistics (O8) |
| **Story 08** | 前置依赖 | 声明 Story 03 标签数据为硬依赖 (B1 联动)；复用 _filter_entries (H8) |
| **Story 09** | 安全改造 | 移除 base_url 自由输入，改为 profile 预设方案切换 (H7 用户方案)；_translation_scope 正式化 (E8) |
| **Story 10** | 安全+重构 | LLM 后处理 require_confirmation (E10)；PostProcessor 工厂函数 (E9) |
| **Story 11** | 设计调整 | download_entries 流程重构 (O7)；api surface 确认 (O10) |
| **Story 12** | 权限修正 | parser 6 工具 write→read (H6)；+文件扩展名白名单 (E1)；路径遍历强化 (E1) |
| **Story 13** | 范围缩减 | -ExecutionContext 定义（移至 Story 01）；+MCP 护栏接入 (B6 联动)；+map_to_steps 修复 (E4)；+namespace 通配符 (O3) |
| **新增 Story 14** | 新增 | 跨 Story 集成测试 (O1) |
| — | 独立 PR | ParaTranz API 限流 (O4)；护栏审计日志 (O6)；ToolSpec 移入 tools/ (O2) |
| — | P2 迭代 | 真实暂停/恢复 (O11)；Reflexion 写工具重试策略 (O5 用户方案) |

### 净工作量影响

- Story 01 工作量显著增加（从基础搭建升级为工具系统核心基础设施）
- Story 04 合并 Story 05 后内容增多但消除跨 Story 依赖，总体效率更高
- Story 06/13 工作量减少
- 总体净增 +2~4h，与原预估上限相近
- 关键路径：Story 01 可能使整体排期延后 0.5~1 天

---

## 五、后续步骤

1. **更新 plan.md**: 按本确认书调整 Story 清单、验收标准、涉及文件
2. **更新各 Story 文档**: 逐个修改 `plans/agent-tool-expansion/stories/story-*.md`
3. **废弃 Story 05**: 将原 story-05 内容合并到 story-04，原文件标记 deprecated 或归档
4. **新建 Story 14**: 编写集成测试 Story
5. **开始编码**: Story 01 优先（基础设施就绪后其他 Story 可并行）
