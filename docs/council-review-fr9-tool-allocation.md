# 评审委员会讨论纪要 — FR9 Agent 工具系统全面扩展（分组评审）

**日期**: 2026-05-11
**评审对象**: `plans/agent-tool-expansion/plan.md` + `docs/requirements.md` FR9 章节
**评审模式**: 分组评审（独立评测 → 小组会谈 → 组间交流 → 最终结论）
**参与角色**: 9 位评审员，分 3 个专题组

---

## 一、分组方案

| 组别 | 专题 | 负责 Story | 成员 | 组长 |
|------|------|------------|------|------|
| G1 基础设施与集成 | tools/ 子包架构、ToolResult/TaskManager/ViewModel、Agent注册、ExecutionEngine适配 | 01, 02, 03, 13 | 架构师、开发者、QA | 架构师 |
| G2 核心工作流 | P0 筛选/搜索/编辑/选择/翻译执行控制/状态查询 + 校对工具 | 04, 05, 06, 07 | 架构师、开发者、产品 | 产品 |
| G3 扩展工具与安全 | P1 标签/翻译配置/后处理/ParaTranz、P2 解析/写回/项目查询、全局安全 | 08, 09, 10, 11, 12 | 架构师、开发者、安全专家 | 安全专家 |

---

## 二、第一步：独立评测（9 位成员）

> 各成员的完整独立意见已在对话中逐条展示。以下第三节为组长汇总的小组正式会谈纪要，其中引用了各成员的核心观点。

---

## 三、第二步：小组会谈（组长正式报告）

> 以下为三位组长在汇总本组成员独立意见后，经过分析、共识识别、分歧标注、优先级排序形成的**小组正式结论**。这些结论将作为第四步组间交流的基础。

### G1 小组会谈纪要 — 基础设施与集成组

**组长**: 架构师
**参与**: 架构师、开发者、QA

#### 组长总结

**总体评价**: 保留意见。四个 Story 的核心方向正确（ToolResult 标准化、TaskManager 单例、AppContext 信号驱动筛选、ExecutionContext 封装与元工具描述），但 Story 01 与 Story 13 之间存在**一个关键的接口契约断裂点**，若不正视将在实施阶段导致执行引擎无法统一调度新旧工具。

#### 共识项（三人一致认同）

1. **ToolResult 与 execution_engine.py 的兼容断裂** — 涉及角色: 架构师/开发者/QA — 优先级: 高
   - **共同分析**: Story 01 将 v1 工具返回从 `dict` 改为 `ToolResult` dataclass，但 `execution_engine.py:_run_single()` 第 149 行用 `raw_result.get("success", True)` 访问返回值。`ToolResult` 没有 `.get()` 方法，必然 `AttributeError`。Story 01 文件变更清单不含 `execution_engine.py`，Story 01→Story 13 之间存在破坏性变更窗口。
   - **小组建议**:
     - 方案 A（推荐）: 为 `ToolResult` 添加 `get(key, default=None)` + `__getitem__` 字典兼容方法，Story 13 时再去掉
     - 方案 B: Story 01 同步修改 `execution_engine.py` 的结果消费逻辑，用 `isinstance(result, ToolResult)` 兼容新旧
     - 方案 C: 为 v1 工具创建薄适配器包装（QA 建议）

2. **ToolResult.success 三态语义与 StepResult.success(bool) 冲突** — 涉及角色: 架构师/开发者/QA — 优先级: 高
   - **共同分析**: `success: Literal[True, False, "partial"]` 中 `"partial"` 是 truthy 字符串，`if result.success` 会误判。所有下游消费方（execution_engine、orchestrator、UI）都是二态判断。方案未提及此项全链路改造。
   - **小组建议**:
     - 选项 1（QA 推荐）: `success: bool` + 新增 `partial: bool = False` 独立字段
     - 选项 2（架构师推荐）: `StepResult` 新增 `status: Literal["success", "failed", "partial"]`，`success` 保留为派生 bool
     - 选项 3: 保留三态但全代码库强制使用 `result.success is True` 检查

3. **v1 工具与新 ExecutionContext 签名兼容** — 涉及角色: 架构师/开发者/QA — 优先级: 高
   - **共同分析**: v1 工具签名 `func(args, ctx)` 接收裸 `AppContext`，新工具接收 `ExecutionContext` 包装。用参数数量区分不可行（都是 2 参数）。try/except 不优雅。
   - **小组建议**:
     - 方案 1（架构师推荐）: `ExecutionContext` 添加 `__getattr__` 代理，未命中属性自动转发到 `app_context`
     - 方案 2（开发者推荐）: `ToolSpec` 新增 `use_execution_context: bool = False` 字段
     - 方案 3（QA 推荐）: Story 01 中为 v1 工具添加薄适配器包装
     - **组长裁决**: 支持 __getattr__ 代理方案，零改动兼容 v1 工具，实施最简单。

4. **Story 01 与 Story 13 应合并 `ExecutionContext` 定义** — 涉及角色: 架构师/开发者 — 优先级: 中
   - **小组建议**: 将 `ExecutionContext` 从 Story 13 提前至 Story 01 实现，在 Story 01 迁移 v1 工具时同步适配，避免 Story 13 的向后兼容分支逻辑。

5. **四个 Story 均缺少测试策略** — 涉及角色: QA（开发者/架构师附议）— 优先级: 中
   - **小组建议**: 每个 Story 增加"测试策略"章节，覆盖单元测试清单 + 集成测试场景。建议增加 Story 14 跨 Story 集成测试。

#### 补充项（组长认可）

- **装饰器堆叠顺序未规范** (开发者+QA): 应在 `base.py` 文档字符串中明确推荐顺序（`@require_collection` 在最外层，`@validate_params` 在内层），或提供 `@tool_pipeline(schema)` 组合装饰器。
- **TaskManager.get_status() 线程安全** (QA): `progress` dict 需深拷贝后返回，防止并发遍历 `RuntimeError`。
- **Orchestrator map_to_steps() 始终取 tools[0]** (QA): 需改为从 LLM action 字段映射到具体 tool_name。
- **TaskManager 单例竞态条件** (架构师): 使用模块级双重检查锁。
- **update_progress() 未列入验收标准** (开发者): 补充到 Story 02 AC。
- **ToolSpec/ToolRegistry 应移入 tools/ 子包** (开发者+架构师): `tool_registry.py` 变为重导出壳。

#### G1 小组结论与建议清单

**高优先级**:
- [ ] **G1-H1**: 解决 ToolResult 与 execution_engine.py 兼容断裂 —— 添加 `get()`/`__getitem__` 字典兼容方法，或 Story 01 同步修改 engine
- [ ] **G1-H2**: 统一 ToolResult.success 和 StepResult.success 的语义模型 —— 推荐 `success: bool` + `partial: bool` 方案
- [ ] **G1-H3**: 解决 v1 工具与 ExecutionContext 签名兼容 —— 推荐 `ExecutionContext.__getattr__` 代理方案

**中优先级**:
- [ ] **G1-M1**: 将 ExecutionContext 从 Story 13 提前至 Story 01 实现
- [ ] **G1-M2**: 每个 Story 增加测试策略章节，新增 Story 14 集成测试
- [ ] **G1-M3**: 明确装饰器堆叠顺序（文档 + 代码示例）
- [ ] **G1-M4**: TaskManager.get_status() progress 深拷贝
- [ ] **G1-M5**: Orchestrator map_to_steps() 增加 tool_name 映射逻辑
- [ ] **G1-M6**: TaskManager 单例双重检查锁

**低优先级**:
- [ ] **G1-L1**: ToolSpec/ToolRegistry 移入 tools/ 子包
- [ ] **G1-L2**: update_progress() 补充到 Story 02 AC
- [ ] **G1-L3**: Story 13 拆分为 13a/13b

#### 带到组间交流的核心议题

1. **跨组共识——标签数据归属问题**: 标签数据（`_label_library`/`_entry_labels`）当前仅在 UI 层，G1 的 Story 03（AppContext 扩展）未规划标签数据上移，但 G2 和 G3 的 Story 04/05/08 都依赖此数据。需要在 Story 03 统一解决，还是各 Story 各自处理？
2. **ExecutionContext 的最终设计**: 建议将 ExecutionContext（含 `__getattr__` 代理）从 Story 13 提前至 Story 01，这会影响 G2/G3 的工具函数签名设计。
3. **HITL 协议统一**: G3 提出的 parser HITL 文件选择机制，与 G1 的 ExecutionEngine 确认机制，需要统一协议。

---

### G2 小组会谈纪要 — 核心工作流组

**组长**: 产品经理
**参与**: 产品经理、架构师、开发者

#### 组长总结

**总体评价**: 保留意见（有条件通过）。P0 工具集的筛选→翻译→检查链路覆盖充分，`start_translation` 的异步模式解决长运行任务的 UX 问题，ROI 高。但存在**三个阻塞性缺陷**：缺失批量 stage 设置工具导致核心闭环断裂、pause_task 是假暂停、标签数据归属未解决。

#### 共识项（多人共同发现）

1. **标签数据未在 AppContext 中，label 相关工具无法实现纯数据操作** — 涉及角色: 架构师/开发者/产品经理 — 优先级: 高
   - **共同分析**: `_label_library` 和 `_entry_labels` 在 UI 层 `Step2PreviewWidget` 中。Story 04 的 `filter_by_label`、Story 05 的 `select_entries` 都需要 label 数据，但 Story 03 仅规划了 `filter_state` 扩展，未覆盖标签数据上移。如果不解决，工具要么访问 UI 层数据（违反 ADR-008），要么无法实现。
   - **小组建议**: 在 Story 03 中追加 AppContext 标签数据管理（`label_library`/`entry_labels` + `label_data_changed` 信号）。这是 Story 04/05/08 的硬前置依赖。

2. **`pause_task` 仅修改状态标记，不实际暂停线程** — 涉及角色: 产品经理/架构师/开发者 — 优先级: 高
   - **共同分析**: TaskHandle 仅有 `stop_event`，缺少 `pause_event`。AutoTranslator 的暂停依赖 `pause_event: threading.Event`。设置 status 为 "paused" 后翻译线程实际仍在运行——status 和实际行为不同步。用户看到"已暂停"但 API 费用持续消耗。
   - **小组建议**:
     - 方案 A（产品推荐）: P0 中移除 `pause_task`，仅保留 `stop_task`（语义清晰无歧义）
     - 方案 B（架构师推荐）: TaskHandle 增加 `pause_event`，`pause_task` 操作真实暂停信号，AutoTranslator 同步改造

3. **`select_entries` 使用标签系统标记选中存在副作用** — 涉及角色: 架构师/开发者 — 优先级: 高
   - **共同分析**: `__agent_selected__` 标签会出现在 UI 筛选栏中污染用户视图，被持久化到 VariantStore 导致下次加载时残留。用户手动删除此标签会导致 Agent 功能失效。
   - **小组建议**: 使用独立的 `_selected_ids: set[str]` 存储在 AppContext 上，与用户标签系统完全隔离。Step2 标签管理 UI 不显示此集合。

4. **缺少 `set_stage` / `batch_set_stage` 工具** — 涉及角色: 产品经理（开发者附议）— 优先级: 高
   - **共同分析**: 核心工作流「筛选→翻译→检查→标记」中"标记"环节缺失。Agent 翻译 500 条后要批量设置为 stage=1，唯一途径是逐条调用 `edit_translation`（还需传入 `new_translation`），在性能和语义上都不可接受。
   - **小组建议**: 在 Story 05 或 07 中新增 `set_stage` 工具（参数: `entry_ids: list[str]`, `stage: int`），registry 到 `editor` namespace。

5. **`edit_translation` 自动设 stage=2 的语义过于单向** — 涉及角色: 产品经理/架构师 — 优先级: 高
   - **共同分析**: Agent 翻译空条目时 stage 应为 1（已翻译），不应硬编码为 2（有疑问）。三种场景需要不同 stage 语义。
   - **小组建议**: `edit_translation` 增加可选参数 `new_stage: int | None = None`。不传时保持现有 stage 不变（无副作用），传入时显式设定。

6. **filter_state 与 Step2 现有筛选格式不一致** — 涉及角色: 架构师/开发者 — 优先级: 中
   - **小组建议**: 在 Story 03 中明确映射契约文档（统一 `search_field` ↔ 三个独立搜索框的映射）。

#### 补充项（组长认可）

- **stop_task 不传 task_id 全部停止** (架构师): 建议去掉隐式语义，新增独立 `stop_all_tasks` 工具。
- **get_visible_entries 200 上限** (产品): 当 `truncated=True` 时在 `message` 中增加建议性提示。
- **标签系统 P0/P1 碎片化** (产品): `select_entries` 首次使用时自动创建标签条目。
- **get_collection_summary deprecated** (产品+开发者): 合并到 `get_statistics`。
- **filter_by_stage/category 不需要 @require_collection** (开发者): stage 值范围为常量，category 校验可降级为可选警告。
- **线程模型** (开发者): TaskManager 跟踪线程引用，cleanup 时 join，daemon=True。

#### G2 小组结论与建议清单

**高优先级**:
- [ ] **G2-H1**: Story 03 追加标签数据（label_library/entry_labels）上移到 AppContext
- [ ] **G2-H2**: 解决 pause_task 假暂停 —— 推荐移除或增加 pause_event
- [ ] **G2-H3**: select_entries 改用独立 `_selected_ids` 集合，解耦标签系统
- [ ] **G2-H4**: 新增 `set_stage` 工具填补批量标记缺口
- [ ] **G2-H5**: edit_translation 增加可选 `new_stage` 参数

**中优先级**:
- [ ] **G2-M1**: filter_state 与 Step2 格式映射契约文档
- [ ] **G2-M2**: stop_task 去掉全部停止隐式语义
- [ ] **G2-M3**: get_visible_entries truncated 时 message 加提示
- [ ] **G2-M4**: filter_by_stage/category 移除 @require_collection
- [ ] **G2-M5**: TaskManager 增加线程引用跟踪

**低优先级**:
- [ ] **G2-L1**: get_collection_summary 标记 deprecated
- [ ] **G2-L2**: switch_collection 参数明确搜索策略
- [ ] **G2-L3**: filter_by_stage 空列表语义明确

#### 带到组间交流的核心议题

1. **标签数据的统一归属方案**: G1 的 Story 03（AppContext 扩展）需要追加 label 数据。是 Story 03 自行解决，还是由 G3 的 Story 08（标签管理）反向推动 Story 03 改造？
2. **pause_event 的跨 Story 影响**: TaskHandle 增加 pause_event 需要 G1 的 Story 02 和 G2 的 Story 06 联动。
3. **`set_stage` 归属**: 新工具应放在 `editor` namespace（Story 05 区域）还是 `default` namespace（Story 07 区域）？

---

### G3 小组会谈纪要 — 扩展工具与安全组

**组长**: 安全专家
**参与**: 安全专家、架构师、开发者

#### 组长总结

**总体评价**: 保留意见。Stories 08-12 方向正确，权限分级体系总体合理。但存在**两个跨组数据基础问题**（标签数据归属 + HITL 协议缺失）和**四个安全高风险项**（MCP 绕过护栏、API Key 明文、base_url 注入、路径遍历检测缺失）。安全问题的严重性不容忽视。

#### 共识项（多人共同发现）

1. **标签数据访问路径缺失 — Story 08 的硬依赖未满足** — 涉及角色: 架构师/开发者/安全专家 — 优先级: 高
   - **共同分析**: Story 08 所有标签工具需通过 `ctx.label_library`/`ctx.entry_labels` 操作标签数据，但这两个属性仅在 UI 层 Step2 中。AppContext 上不存在。持久化通道存在（VariantStore）但运行时上下文未打通。这与 G1-G2 发现的问题完全一致，属于**三组共同识别的跨组基础设施缺口**。
   - **小组建议**: 必须由 Story 03（G1 负责）在 AppContext 上新增标签相关属性和信号。Story 08 将此作为硬性前置依赖声明。

2. **parser 工具 HITL 机制缺乏协议闭环** — 涉及角色: 架构师/开发者 — 优先级: 高
   - **共同分析**: path 可选参数设计为"不传触发 HITL"，但现有 `step_requires_confirmation` 只支持"继续/跳过"二选一，不支持文件选择对话框。两阶段执行中第二次调用的 path 参数由谁填充未定义。与 writer 的 require_confirmation 走的是不同路径，两套机制不统一。
   - **小组建议**: 在 G1 的 `tools/base.py` 或 `ExecutionContext` 中定义统一的 HITL 协议（`HITLRequest`/`HITLResponse` 数据类），parser 文件选择、writer 确认、download_entries 对比确认三种 HITL 场景使用统一机制。

3. **MCP 通道完全绕过安全护栏中间件链** — 涉及角色: 安全专家（架构师附议）— 优先级: **Critical**
   - **共同分析**: `MCPServer._handle_request()` 直接调用 `spec.execute()`，不经过 PermissionGuard、InputValidationGuard、OutputValidationGuard。write 级工具直接执行，admin 级工具一旦白名单暴露也绕过确认。输入注入检测、输出脱敏和大小截断对 MCP 调用全部不生效。
   - **小组建议**: 在 MCPAdapter 或 MCPServer 中注入完整的中间件链调用。MCP 无 UI 交互能力时，write_confirmation 和 admin_confirm 直接拒绝。

4. **API Key/Token 明文存储于 INI 文件** — 涉及角色: 安全专家 — 优先级: 高
   - **小组建议**: 短期使用 base64+盐值编码防无意泄露，中期迁移到 Windows DPAPI 或 `keyring` 库。

5. **`set_translation_config` 的 base_url 可被注入** — 涉及角色: 安全专家 — 优先级: 高
   - **共同分析**: 攻击者可通过 Agent function calling 将 `base_url` 指向恶意 MITM 代理截获所有 API Key 和翻译原文。当前 `write` 级别无确认放行。
   - **小组建议**: 将 `base_url` 从 allowed_keys 白名单移除，或升为 `admin` 级 + 添加 URL 域名白名单校验。

6. **路径遍历检测在输入校验中缺失** — 涉及角色: 安全专家 — 优先级: 高
   - **共同分析**: `InputValidationGuard` 代码完全未实现 `../`、`..\\`、绝对路径注入检测。parser/writer 工具大量接收文件路径参数。
   - **小组建议**: P0 优先级补全路径遍历检测（含 NTFS 流名注入 `::$DATA`），并对 parser/writer 工具的文件扩展名添加白名单（.esp/.esm/.esl/.xml/.json/.strings）。

7. **筛选逻辑在三处 Story 中重复实现** — 涉及角色: 架构师/开发者 — 优先级: 高
   - **共同分析**: Story 04 `get_visible_entries`、Story 08 `batch_assign_label`、Story 09 `get_scope_preview`/`set_scope` 三处都需要遍历 collection 按 filter_state 过滤。各自实现导致代码重复和潜在行为不一致。
   - **小组建议**: 提取 `_filter_entries(collection, filter_state) -> list[TranslationEntry]` 公共函数放在 `tools/base.py`。

#### 补充项（组长认可）

- **_translation_scope 临时属性缺乏接口契约** (架构师): 建议纳入 AppContext 正式属性或 ExecutionContext。
- **PostProcessor 封装复杂度** (开发者): 提取 `_run_postprocess_phase()` 工厂函数，减少 3 个 long_running 工具的胶水代码重复。
- **ParaTranz API surface 确认** (开发者): 实现前确认 ParatranzClient 完整 API，列出每个工具的确切方法调用路径。
- **download_entries 两阶段设计** (架构师): 建议拆分为 `compare_with_remote` + `apply_download`。
- **LLM 后处理工具费用防护** (安全专家): 设置 `require_confirmation=true`，确认提示显示预估条目数和费用。
- **确认超时 300s→60s** (安全专家): 对齐 ADR-012。
- **输出脱敏嵌套 list 盲区** (安全专家): `_redact_dict` 增加对 list 类型的递归处理。

#### 安全专项汇总（组长负责）

| # | 风险 | 严重性 | 影响范围 | 建议处理批次 |
|---|------|--------|---------|-------------|
| 1 | MCP 绕过护栏中间件链 | Critical | 所有 MCP 暴露工具 | P0 - 编码前修复 |
| 2 | API Key/Token 明文存储 | High | LLM/ParaTranz 凭证 | P0 - 编码前修复 |
| 3 | set_translation_config base_url 注入 | High | LLM 流量劫持 | P0 - 编码前修复 |
| 4 | 路径遍历检测缺失 | High | parser/writer 路径参数 | P0 - 编码前修复 |
| 5 | LLM 后处理工具无费用确认 | Medium | API 费用 | P1 - 实现时处理 |
| 6 | 确认超时 300s→60s | Medium | 并发任务处理 | P1 |
| 7 | 嵌套 list 敏感信息漏脱敏 | Medium | 输出泄露 | P1 |
| 8 | ParaTranz API 无限流 | Low | API 配额/封禁 | P2 |
| 9 | Reflexion 非幂等写工具重试 | Low | 数据重复 | P2 |
| 10 | 护栏审计日志缺失 | Low | 安全审计 | P2 |

#### G3 小组结论与建议清单

**高优先级**:
- [ ] **G3-H1**: Story 08 前置依赖——AppContext 标签数据上移（与 G1/G2 联动）
- [ ] **G3-H2**: 定义统一 HITL 协议（HITLRequest/HITLResponse），覆盖 parser/writer/download 三种场景
- [ ] **G3-H3**: MCP 通道接入安全护栏中间件链
- [ ] **G3-H4**: API Key/Token 加密存储（至少 base64+盐值，推荐 DPAPI/keyring）
- [ ] **G3-H5**: 从 set_translation_config allowed_keys 中移除 base_url，或升为 admin+域名白名单
- [ ] **G3-H6**: InputValidationGuard 补全路径遍历检测 + 文件扩展名白名单
- [ ] **G3-H7**: 提取 `_filter_entries()` 公共函数，避免三处重复实现

**中优先级**:
- [ ] **G3-M1**: _translation_scope 纳入 AppContext/ExecutionContext 正式属性
- [ ] **G3-M2**: 提取 PostProcessor 胶水代码工厂函数
- [ ] **G3-M3**: ParaTranz 工具实现前确认 API surface
- [ ] **G3-M4**: download_entries 两阶段拆分或增加 confirmed 参数
- [ ] **G3-M5**: LLM 后处理工具 require_confirmation=true
- [ ] **G3-M6**: 确认超时对齐 ADR-012（300s→60s）
- [ ] **G3-M7**: 输出脱敏 list 递归处理

**低优先级**:
- [ ] **G3-L1**: ParaTranz API 请求频率限制（令牌桶）
- [ ] **G3-L2**: Reflexion 对非幂等写工具禁用重试
- [ ] **G3-L3**: 结构化护栏审计日志
- [ ] **G3-L4**: Story 10 拆分为 10a/10b，Story 11 拆分为 11a/11b

#### 带到组间交流的核心议题

1. **标签数据归属——三组共识的跨组基础设施缺口**: Story 03 需要统一解决 AppContext 标签数据上移。G1 负责 Story 03 但当前方案未规划标签数据。需要三组确认责任归属和接口契约。
2. **HITL 协议统一设计**: G3 的 parser HITL（文件选择）和 G1 的 ExecutionEngine HITL（确认弹窗）需要统一为同一套协议。
3. **安全 P0 项的优先级与资源分配**: MCP 护栏、API Key 加密、base_url 防护、路径遍历检测——这 4 个高优安全项的修复是否应在 FR9 编码前完成，还是可以在实施 Story 的过程中并行修复？
4. **_filter_entries() 公共函数的归属**: 放在 `tools/base.py`（G1 负责）还是新建 `tools/_filters.py`？

---

## 四、第三步：组间交流

> 三位组长带着各自小组结论进行跨组讨论。以下为组间交流正式纪要。

### 参与组长

| 组长 | 组别 | 角色 |
|------|------|------|
| G1 组长 | 基础设施与集成组 | 架构师 |
| G2 组长 | 核心工作流组 | 产品经理 |
| G3 组长 | 扩展工具与安全组 | 安全专家 |

### 组间交流纪要

#### 交叉验证：三组共同发现的核心问题

| # | 问题 | 发现组 | 置信度 |
|---|------|--------|--------|
| 1 | 标签数据归属缺失（AppContext 无 label 属性） | G1/G2/G3 三组 | 最高 |
| 2 | ExecutionContext 设计位置与兼容策略 | G1/G3 | 高 |
| 3 | HITL 协议碎片化（两套互不兼容机制） | G1/G3 | 高 |
| 4 | 筛选逻辑重复实现 | G2/G3 | 高 |

#### 逐议题讨论与决议

**议题 1: 标签数据归属** → **决议**: 由 G1 Story 03 统一解决。追加 `label_library`/`entry_labels`/`label_data_changed` 信号。G2 Story 04/05 和 G3 Story 08 声明前置依赖。额外工作量 1-2h。

**议题 2: ExecutionContext 最终设计** → **决议**: 采纳选项 A，提前至 Story 01。`__getattr__` 代理方案零改动兼容 v1 工具。Story 13 范围缩减（移除 ExecutionContext 定义）。

**议题 3: pause_task 去留** → **决议**: 采纳方案 A，P0 移除 pause_task。真实暂停/恢复机制列入 P2 后续迭代。TaskHandle 预留 `pause_event` 字段但不实现。

**议题 4: HITL 协议统一** → **决议**: 在 Story 01 `base.py` 中定义轻量级 `HITLRequest`/`HITLResponse` 数据类，覆盖 confirm/file_select/compare_confirm 三种场景。

**议题 5: parser 权限分级** → **决议**: parser 6 工具从 `write` 改为 `read`。同步审查 `clear_all_filters` 权限。权限判定标准：`write` = 对持久化数据或外部系统产生不可逆副作用。

**议题 6: 安全修复策略** → **决议**: 分层处理——API Key 加密编码前独立修复；MCP 护栏注入点 Story 01 预留 + Story 13 强制接入；base_url 移除 Story 09 处理；路径遍历 Story 01 基础版 + Story 12 强化。

**议题 7: Story 粒度调整** → **决议**: 采纳 Story 05→04 合并（保持原编号）。Story 13/10/11 暂不拆分。ExecutionContext 提前后 Story 13 工作量已合理。

#### 冲突裁决

| 冲突点 | 裁决结果 | 理由 |
|--------|---------|------|
| parser 权限 write vs read | **改为 read** | 解析不产生持久化副作用；需同步审查 clear_all_filters |
| Story 13 是否拆分 | **暂不拆分** | ExecutionContext 提前后剩余工作量合理 |
| Story 10/11 是否拆分 | **暂不拆分** | 通过提取公共函数解决复杂度 |

---

## 五、综合结论

### 总体评审结论

**评审结果**: **保留意见（有条件通过）**

FR9 Agent 工具系统全面扩展方案在架构方向、模块划分、权限体系方面设计合理，与 ADR-008/011/012 体系一致。经 9 位评审员独立评测、3 组小组会谈、1 轮组间交流，识别出 **6 个阻塞项**、**9 个高优项**、**12 个增强项**、**11 个优化项**。阻塞项必须在编码前解决，其余可在各 Story 实施阶段并行处理。

### 最终建议清单

#### P0 阻塞级（编码前必须解决）

- [ ] **B1**: **标签数据上移至 AppContext** — Story 03 追加 `label_library`/`entry_labels`/`label_data_changed`。三组共识。
- [ ] **B2**: **ToolResult 字典兼容** — 添加 `get()`/`__getitem__`，修复 `execution_engine.py` 的 `raw_result.get("success")` 兼容断裂。
- [ ] **B3**: **ToolResult.success 语义修正** — 改为 `success: bool` + `partial: bool = False`。
- [ ] **B4**: **ExecutionContext 提前至 Story 01** — 含 `__getattr__` 代理。Story 13 范围相应缩减。
- [ ] **B5**: **移除 pause_task 工具** — 从 Story 06 删除。TaskHandle 预留 pause_event 字段但不实现。
- [ ] **B6**: **MCP 护栏中间件注入点预留** — Story 01 提供 `execute_with_guardrails()` 统一入口。

#### P0 高优先级（P0 阶段应解决）

- [ ] **H1**: Story 05 合并至 Story 04（保持原编号）
- [ ] **H2**: `select_entries` 改用独立 `_selected_ids` 集合
- [ ] **H3**: 新增 `set_stage` 工具填补批量标记缺口
- [ ] **H4**: `edit_translation` 增加可选 `new_stage` 参数
- [ ] **H5**: 统一 HITL 协议（`HITLRequest`/`HITLResponse`）
- [ ] **H6**: parser 6 工具权限改为 `read`
- [ ] **H7**: `set_translation_config` 移除 `base_url` 或升为 admin
- [ ] **H8**: 提取 `_filter_entries()` 公共函数
- [ ] **H9**: v1 工具签名兼容（ExecutionContext.__getattr__ 代理）

#### P1 增强（可在 P1 阶段解决）

- [ ] **E1**: 路径遍历检测（Story 01 基础版 + Story 12 强化）
- [ ] **E2**: API Key/Token 加密存储（独立 PR）
- [ ] **E3**: TaskManager 线程安全强化
- [ ] **E4**: Orchestrator map_to_steps() 修复
- [ ] **E5**: 装饰器堆叠顺序文档化
- [ ] **E6**: filter_state 与 Step2 映射契约文档
- [ ] **E7**: stop_task 去掉全部停止隐式语义
- [ ] **E8**: _translation_scope 纳入正式属性
- [ ] **E9**: PostProcessor 胶水代码工厂函数
- [ ] **E10**: LLM 后处理工具 require_confirmation
- [ ] **E11**: 确认超时 300s→60s
- [ ] **E12**: 输出脱敏嵌套 list 递归处理

#### P2 优化（后续迭代）

- [ ] **O1**: 新增 Story 14 集成测试
- [ ] **O2**: ToolSpec/ToolRegistry 移入 tools/ 子包
- [ ] **O3**: AgentSpec 工具列表支持 `namespace:*` 通配符
- [ ] **O4**: ParaTranz API 请求频率限制
- [ ] **O5**: Reflexion 对非幂等写工具禁用重试
- [ ] **O6**: 结构化护栏审计日志
- [ ] **O7**: `download_entries` 两阶段拆分
- [ ] **O8**: `get_collection_summary` deprecated
- [ ] **O9**: TaskManager 线程引用跟踪
- [ ] **O10**: ParaTranz 工具 API surface 确认
- [ ] **O11**: 真实暂停/恢复机制

### 对 plan.md 的修改建议摘要

| 调整项 | 内容 |
|--------|------|
| **Story 01 范围扩大** | +ExecutionContext +HITL协议 +execute_with_guardrails +基础路径遍历 +execution_engine适配 |
| **Story 03 范围扩大** | +标签数据上移（label_library/entry_labels/label_data_changed） |
| **Story 04 范围扩大** | 合并原 Story 05 + set_stage + selected_ids 独立集合 + new_stage 参数 |
| **Story 06 范围缩减** | -pause_task |
| **Story 09 安全加固** | -base_url from allowed_keys 或 +域名白名单 |
| **Story 12 权限修正** | parser 6工具 write→read；+扩展名白名单 |
| **Story 13 范围缩减** | -ExecutionContext（已移至 Story 01）；+MCP 护栏接入 |
| **净工作量影响** | +2-4h，在原预估上限附近 |
| **关键路径** | Story 01 工作量增加可能使整体排期延后 0.5-1 天 |

---

## 纪要不构成决议

本文件为 9 位评审员独立意见 + 3 组小组会谈 + 1 轮组间交流的完整客观记录。最终决策权归用户所有。建议据此更新 `plans/agent-tool-expansion/plan.md` 的 Story 清单和验收标准后再启动编码。