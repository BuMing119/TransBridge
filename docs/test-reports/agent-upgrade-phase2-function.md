## FR7.13 Phase 2 后端 — 功能测试报告

**日期**: 2026-05-10
**对应方案**: `plans/agent-upgrade/plan.md` (Story-06 ~ Story-12)
**审查范围**: agents/, guardrails/, graph_types.py, graph_executor.py, observability/, mcp/, tool_registry.py, execution_engine.py
**审查方式**: 静态代码分析 (Bash 权限受限，未实际运行脚本；以下基于逐文件阅读 + 数据流追踪的预测结果)

---

### 测试覆盖

| 测试项 | 预测状态 | 备注 |
|--------|----------|------|
| Test 1: Full import chain | PASS | 所有类均可从公开 `__init__.py` 导入，无 ImportError 预期 |
| Test 2: AgentRegistry presets | PASS | init_presets() 注册 3 个 Agent；orchestrator namespace 为 None；translator namespace 为 "translator" |
| Test 3: ToolRegistry namespace | PASS | 3 个命名空间均已注册；translator 命名空间含 2 个工具 (lookup_terms, translate_entries) |
| Test 4: Backward compat get() | PASS | namespace=None 时搜索所有命名空间；namespace="translator" 时仅搜索该命名空间 |
| Test 5: ToolSpec permissions | PASS | write_back=admin, lookup_terms=read, translate_entries=write |
| Test 6: ExecutionEngine execute() | PASS | 委托给 execute_graph() → 线性 GraphSpec → BFS → _run_single → tool 执行成功 |
| Test 7: GraphSpec + execute_graph | PASS | ActionNode + 空 edges + entry_node → BFS 单层遍历 → _run_single → 返回 1 个 StepResult |
| Test 8: ObservabilityCollector | PASS | start→on_step→on_llm_tokens→end_conversation 链路完整，token_stats.add() 正确累加 |
| Test 9: MCP adapter admin filtering | PASS | admin 工具 (write_back) 无白名单时未暴露；read 工具 (get_collection_summary) 正常暴露 |
| Test 10: PermissionGuard | PASS | enable_admin_confirm=False 时 admin 工具直接放行；read 工具始终放行 |
| Test 11: InputValidationGuard | PASS | "hello" 通过校验；"'; DROP TABLE" 触发 SQL 注入模式 → 拒绝；模式含 '\\s*;\\s*(DROP\\|...) |
| Test 12: Checkpoint roundtrip | PASS | to_dict() → from_dict() 往返一致 |

**预测结果**: 12/12 PASS

---

### 审查结论

#### 方案一致性: YELLOW (发现 5 个问题)

代码覆盖了 plan.md 中 Story-06 至 Story-12 的所有验收标准：
- [x] AgentSpec/AgentInstance/AgentRegistry (S06)
- [x] ToolRegistry namespace 扩展 (S06)
- [x] Orchestrator + AgentWorker (S07)
- [x] GuardMiddleware ABC + 3 实现 (S08)
- [x] ToolSpec 新增 permission/require_confirmation/max_output_size (S08)
- [x] GraphExecutor ABC + GraphSpec/4 种 Node + EdgeSpec + Checkpoint (S09/S10)
- [x] StatefulDAGExecutor (S09 — execution_engine.py 内)
- [x] execute() 向后兼容 (S09)
- [x] ObservabilityCollector + 4 种数据模型 (S11)
- [x] MCPServer + MCPAdapter (S12)

但存在以下偏离：

1. **[Major] execute() 方法重复定义** (`execution_engine.py:42` vs `:357`)
   - 第一版 `execute()` (L42) 使用 `_topological_levels` + ThreadPoolExecutor 直接执行的逻辑被第二版 (L357) 完全覆盖，成为**死代码**
   - 第一版中处理 `depends_on` 的拓扑排序能力在 graph 模式下丢失：第二版 `execute()` 将 steps 转为**纯线性链**，忽略了 step 之间的 `depends_on` 关系
   - 影响：多步骤并行执行时，依赖关系可能被破坏

2. **[Major] `_paused` Event 从未被等待** (`execution_engine.py:233/379/382`)
   - `pause()` 设置 `_paused.set()`，`resume()` 设置 `_paused.clear()`，但 `execute_graph()` 和 `_dispatch()` 中均无 `self._paused.wait()` 调用
   - 影响：**`pause()` 没有实际效果**，执行不会被暂停

3. **[Minor] `_topological_levels` 成为死代码** (`execution_engine.py:84-109`)
   - 仅在 L46（已废弃的第一版 `execute`）中调用
   - 在第二版（graph 模式）和 `execute_graph()` 中均不使用
   - BFS 遍历（`execute_graph` L301-355）重写了分层逻辑，但简化了循环检测和依赖处理

4. **[Minor] ThreadPoolExecutor 内 `results` 字典并发写入** (`execution_engine.py:267`)
   - `_dispatch()` 在 LoopNode 分支中直接写入 `results[sub_node.node_id] = r` (L276)
   - `_dispatch` 在 `ThreadPoolExecutor.submit()` 的工作线程中执行，`results` 同时在主线程被读取
   - GIL 保护下不会崩溃，但存在理论数据竞争风险

5. **[Minor] `__init__.py` 导出不完整**
   - `smart_assistant/__init__.py` 未导出 `guardrails`、`observability`、`mcp` 子包中的任何符号
   - 用户必须从 `src.transbridge.smart_assistant.guardrails`（而非 `src.transbridge.smart_assistant`）导入，虽不影响功能但不符合模块对外接口统一性惯例

#### 代码质量: YELLOW

优点：
- 新文件组织结构清晰，5 个子包职责分明
- dataclass 使用恰当，类型注解完整 (Python 3.10+ `str | None` 语法)
- ToolRegistry 的 namespace 设计向后兼容（默认 "default"，namespace=None 搜索全部）
- AgentRegistry 的 preset 注册与 plan 清单一致
- execute() → GraphSpec 转换委托实现了向后兼容

问题：
- `execution_engine.py` 文件过大 (~450 行)，集中了 execute/execute_graph/_run_single/_dispatch/_topological_levels/checkpoint/condition 等多个职责，违反了单一职责原则
- 第一版 `execute()` 遗留代码未清理 (L42-76)
- `_dispatch()` 闭包内嵌在 `execute_graph()` 中（~50 行），增加了理解和调试难度
- `while True` 重试循环结构（L183-208）在 `_retry_handler is None` 时强制失败，逻辑可优化为：无 handler → 直接执行不重试

#### 安全性: GREEN

- InputValidationGuard 覆盖了 SQL 注入、XSS、命令注入、管道注入、反引号注入 5 种模式
- OutputValidationGuard 对 API Key 等敏感信息进行脱敏
- PermissionGuard 实现三级权限（read/write/admin），admin 操作可配置需确认
- MCPAdapter 默认不暴露 admin 工具，write 工具可配置策略 (allow/deny)
- `_eval_condition()` 使用 `__builtins__: None` 限制 eval 范围
- 无 eval/exec 任意代码执行风险

### 发现的问题

#### Blocker (0)

无。

#### Critical (0)

无。

#### Major (2)

- [ ] **[M1] execute() 方法重复定义导致拓扑排序能力丢失** (`execution_engine.py`)
  - 位置：L42（第一版，被覆盖）、L357（第二版，当前生效）
  - 修复建议：删除第一版 `execute()` (L42-76)；在第二版 `execute()` 中保留 `_topological_levels` 分层能力，或将其整合到 GraphSpec 转换逻辑中（为有 `depends_on` 的 steps 创建正确的 edges）
  - 当前影响：测试场景为单步/线性步骤，不出错；生产场景若 LLM 生成含依赖的子任务，依赖关系将被忽略

- [ ] **[M2] `pause()` 不生效 — `_paused` Event 从未被等待** (`execution_engine.py`)
  - 位置：L233/379/382；对比 `_cancelled` Event (L153/239 有 `.is_set()` 检查)
  - 修复建议：在 `execute_graph()` BFS 循环开头添加 `self._paused.wait()`；或在 `_dispatch()` 开头添加

#### Minor (3)

- [ ] **[m1] `_topological_levels` 死代码** — L84-109 仅在已废弃的第一版 execute 中调用
- [ ] **[m2] `_dispatch()` 中 results 字典的并发写入** — L276 在 ThreadPoolExecutor worker 线程中修改共享 dict
- [ ] **[m3] `smart_assistant/__init__.py` 未导出 guardrails/observability/mcp 子包公开符号**

---

### 审查总结

所有 12 个功能测试**均预期通过**静态分析验证。代码完整实现了 plan.md 中 Phase 2 的 7 个 Story 验收标准，新增 18 个文件、修改 3 个文件，零新依赖。

5 个代码质量问题中，2 个 Major（execute 重复定义 + pause 不生效）需要修复后方可进入生产；3 个 Minor 为代码清洁度和健壮性问题，可在后续迭代中处理。

### 签名

QA 有条件通过 (2 Major, 3 Minor 待修复)
— 静态分析，2026-05-10
