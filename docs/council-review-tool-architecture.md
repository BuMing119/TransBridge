# 评审委员会讨论纪要 — Smart Assistant 工具架构路线

**日期**: 2026-05-15
**评审对象**: 工具架构策略（路线 A 扁平合并 vs 路线 B Agent 分治）
**参与角色**: 架构师 / 开发者 / 产品经理

## 讨论背景

### 触发点

用户在评估"AI 助手有了比较全面的 editor 筛选工具后，以前一些老的筛选工具是否可以弃用删除"时，引发了两个关联发现：

1. **5 个 v1 废弃工具**（`tool_v1.py`）已标记 `deprecated=True`，从 LLM prompt 中排除，但完整实现仍保留在代码库中——属于可清理的死代码。
2. **56 个非废弃工具**全量注入 LLM system prompt（~3880 tokens），而 `build_system_prompt(context, namespace=...)` 虽然设计了 namespace 过滤参数，但调用时并未使用。

### 深入挖掘的发现

进一步审查代码后发现了一个更根本的问题：

- **Agent 系统已被设计和注册**：`AgentRegistry` 定义了 7 个 Agent（orchestrator / editor / translator / proofreader / parser / paratranz / writer），各自绑定对应 namespace 的工具（如 editor Agent 只看 `editor:*` 的 14 个工具）。
- **但核心调度组件从未接入**：`Orchestrator`（agents/orchestrator.py，负责将用户请求分解为子任务并路由到对应 Agent）和 `AgentWorker`（agents/agent_worker.py，负责在 Agent 的 namespace 范围内查找并执行工具）从未被 `ConversationOrchestrator` 调用。
- **实际运行路径是扁平的**：`ConversationOrchestrator.start_round()` → `build_system_prompt(context)` 不传 namespace → 56 个工具全量注入 → LLM 直接选工具 → `ExecutionEngine` 执行。

简言之：**Agent 分治架构完成了 80%（定义 + 注册 + namespace 隔离），但缺失最后一环（调度接入），导致名义架构与实际行为不一致。**

### 两条路线

由此衍生出两条架构路线：

| | 路线 A（Claude Code 扁平化） | 路线 B（继续 Agent 路线） |
|---|---|---|
| **思路** | 放弃 Agent 分治，合并同类工具，保持工具正交清晰 | 接入 Orchestrator + AgentWorker，实现按 namespace 分治 |
| **工具数** | 56 → 25-30（合并同类项） | 56 不变，但每个 Agent 只看 ~10 个 |
| **LLM 调用** | 无额外调用 | 每次请求多 1-2 次编排 LLM 调用 |
| **代码量** | +60/-450 行 | +250/-80 行 + 持续调优 |
| **风险** | LLM 选错工具（可缓解） | 编排 LLM 误判 → 全链路失败 |

### 参考：Claude Code 的做法

Claude Code 自身采用扁平化策略：~26 个工具全量注入 system prompt，无 namespace 过滤，无 Agent 分治。其工具数量可控的关键不是数量少，而是**功能正交性高**——Read/Write/Bash/Grep 各司其职，边界清晰，LLM 不可能搞混。

## 各角色独立意见

### 架构师

- **总体评价**: 推荐路线 A（Claude Code 扁平化），保留 AgentRegistry 作为工具命名空间的基础设施层
- **发现的问题/建议**:
  1. 现有 plan/react 模式已是任务分解机制，Orchestrator.decompose_task() 是冗余双层编排 — 优先级: 高
  2. Agent 系统存在结构缺陷：map_to_steps() 用 LLM 输出的 action 字符串直接当 tool_name，无映射层 — 优先级: 高
  3. 路线 B 的 Agent 间状态传递、失败回滚、并行协调均未设计 — 优先级: 高
  4. 保留 namespace 过滤能力和 AgentRegistry 作为基础设施，未来工具数超过 80 时可按用户操作域过滤 — 优先级: 低

### 开发者

- **总体评价**: 强烈推荐路线 A（扁平合并）
- **发现的问题/建议**:
  1. Agent 层是 356 行死后代码（orchestrator.py + agent_worker.py），运行时不接入 — 优先级: 高
  2. 路线 B 有 2 个已确认 bug（fallback 必然失败、namespace 查找与跨 Agent 协作矛盾）+ 3 个架构缺口 — 优先级: 高
  3. 工具注册样板代码重复（7 个模块各 ~30 行相同模式），应迁移到 register_tools() 批量方法 — 优先级: 中
  4. 路线 A 改动量可控：+60/-450 行，12 文件 — 优先级: 低

### 产品经理/业务

- **总体评价**: 强烈推荐路线 A，将名义架构对齐到实际行为
- **发现的问题/建议**:
  1. 路线 B 每次请求多 1-2 次 LLM 调用（+500ms~2s），聊天产品响应延迟是最敏感的用户感知指标 — 优先级: 高
  2. 路线 B 为无人抱怨的问题（LLM 选错工具）引入新 bug 风险 — 优先级: 高
  3. 快速迭代阶段应缩短反馈循环、减少变更半径，路线 A 更匹配当前产品节奏 — 优先级: 中
  4. 保留 AgentRegistry 和 namespace 系统作为工具逻辑分组标签，几乎零维护成本 — 优先级: 低

## 共识汇总

以下建议获得**多个角色一致认同**：
- [x] 推荐路线 A（扁平合并），放弃路线 B（Agent 分治）—— 三票全票
- [x] 路线 B 的 Orchestrator 和 AgentWorker 存在已知 bug 且从未在生产环境运行
- [x] 路线 B 引入额外 LLM 调用是净负面（延迟 + 成本 + 故障点）
- [x] 工具合并是独立高价值操作，不管选哪条路线都应该做
- [x] 保留 AgentRegistry/AgentSpec 作为基础设施（低维护成本，未来有价值）

## 分歧与冲突

无分歧。三个角色在所有关键判断上意见一致。

## 综合建议清单

### 高优先级
- [ ] 合并 editor 筛选三函数：filter_by_stage + filter_by_category + filter_by_label → set_filters
- [ ] 合并 translator 控制函数：stop_task + stop_all_tasks → stop_task(task_id=None)
- [ ] 合并 writer 四函数：write_to_esp + write_to_eet + write_to_xt + write_to_strings → write_back
- [ ] 合并标签管理 CRUD：create_label + remove_label → manage_label

### 中优先级
- [ ] 删除 agents/orchestrator.py 和 agents/agent_worker.py（192 行死代码）
- [ ] 从 agents/__init__.py 移除 Orchestrator/AgentWorker 导出
- [ ] 注册样板消除：7 个工具模块改用 ToolRegistry.register_tools()
- [ ] 强化工具描述：确保每个工具描述包含与其他相似工具的区分关键词

### 低优先级
- [ ] 清理 tool_v1.py 废弃工具（观察 1-2 迭代确认无外部依赖后）
- [ ] 监控工具选择准确率
- [ ] 未来工具数超过 80 时重新评估 namespace 过滤需求

## 纪要不构成决议

本文件仅为各角色独立意见的客观汇总，**不强制要求采纳任何建议**。最终决策权归用户所有。
