# 评审委员会讨论纪要 — 路线 A（扁平合并）实施方法设计

**日期**: 2026-05-15
**评审对象**: 工具架构策略路线 A 实施方法
**前置决议**: [第一轮纪要](council-review-tool-architecture.md) — 路线 A 全票通过
**参与角色**: 架构师 / 开发者 / QA / 产品经理

## 讨论背景

第一轮评审已确定路线 A（扁平合并）：放弃 Agent 分治，合并同类工具（56→25-30），保留 AgentRegistry 作为基础设施。第二轮聚焦**如何实施**——参数设计、文件改动、测试策略、迭代规划。

---

## 各角色独立意见

### 架构师

- **总体评价**: 路线 A 正确，但第一轮纪要中 `create_label + remove_label → manage_label` 存在正交性误判，需纠偏。
- **发现的问题/建议**:
  1. **扩大筛选合并范围**：不仅合并 filter_by_stage/category/label，还应吞并 `search_entries` + `clear_all_filters`（5→1）。五个工具操作同一对象 `ctx.filter_state` 的不同 key，合并到单入口是自然内聚 — 优先级: 高
  2. **标签合并应拆分**：`remove_label` 操作条目-标签关联，不是标签定义，与 `create_label` 不同目标对象。建议 `assign_label + remove_label + batch_assign_label → manage_entry_labels`（3→1），`create_label` 独立保留 — 优先级: 高
  3. **write_back 使用 dispatch 表**：`_WRITE_HANDLERS = {"esp": ..., "eet": ..., "xt": ..., "strings": ...}`，四个现有实现重命名为 `_write_to_*_impl`，外层统一处理 `@require_collection` — 优先级: 高
  4. **不需要修改 ConversationOrchestrator 或 prompts.py**：`build_tool_schema_for_prompt(None)` 自动排除 deprecated 工具 — 优先级: 中
  5. Phase 1 四个 Story 修改不同文件，完全可并行实施 — 优先级: 低
  6. 不合并的合理情况：Parser（格式独立）、Proofreader（规则检查/LLM 分离）、Paratranz（API 端点独立） — 优先级: 低

**合并后工具数：56 → 46（-10）**

| 合并 | 变化 | 净减 |
|------|------|------|
| `set_filters` | 5→1 | -4 |
| `stop_task` | 2→1 | -1 |
| `write_back` | 4→1 | -3 |
| `manage_entry_labels` | 3→1 | -2 |

### 开发者

- **总体评价**: Agent 死代码从未被导入，合并目标 56→45 合理，加上 v1 清理可到 30 附近。
- **发现的问题/建议**:
  1. **set_filters 参数**：`stages/categories/labels/search_query/search_field/clear_first` 全部可选，None=不修改该维度，[]=清除 — 优先级: 高
  2. **每个合并保留旧 wrapper**：旧函数添加 DeprecationWarning，不注册到 ToolRegistry，大版本后删除 — 优先级: 高
  3. **精确文件改动清单**：tool_editor.py +80/-80、tool_translator.py +15/-15、tool_writer.py +50/-80、agents 删除 -198 行、7 模块注册改造 -35 行，合计 +180/-360 行 — 优先级: 中
  4. **实施顺序**：Phase 1 死代码清理+样板消除 → Phase 2 Writer+Translator 合并 → Phase 3 Editor 合并 → Phase 4 v1 清理 — 优先级: 中
  5. **注册样板改造**：`ToolRegistry.register_tools("editor", [{"name": "...", ...}, ...])` 替代手动元组循环 — 优先级: 中
  6. **最可能出错的步骤**：Editor set_filters（clear_first+叠加语义边界）、Writer write_back（不同 target 不同校验）、stop_task（空字符串/None 映射） — 优先级: 中

### QA

- **总体评价**: 现有 ~95 条测试，但 Writer/Paratranz 完全无测试，全部用 MockAppContext 直接调用不经过 ExecutionEngine。工具合并后的集成行为变更无法被现有测试捕获。
- **发现的问题/建议**:
  1. **每个合并任务需参数矩阵测试**：set_filters 10 个 case、write_back 9 个 case、manage_entry_labels 6 个 case、stop_task 5 个 case — 优先级: 高
  2. **LLM 工具选择回归测试**：构建典型用户意图 prompt，验证合并后新工具名在 schema 中、旧工具名不在 — 优先级: 高
  3. **建议创建 tests/smart_assistant/ 子目录**，提供 conftest.py 共享 fixtures — 优先级: 中
  4. **量化验收门槛**：合并后工具数 25-30、旧工具名残留 0、合并函数参数组合覆盖率 100%、现有回归测试 100% 通过、register_tools() 使用率 7/7 — 优先级: 中
  5. **Writer 测试需 Mock PluginWriter**（当前覆盖率为零）— 优先级: 中
  6. **建议 ToolRegistry 增加 reset() 方法**用于测试隔离 — 优先级: 低

### 产品经理

- **总体评价**: 合并不是目的，减少 LLM 认知负担才是目的。如果合并后工具描述变长、参数变复杂，可能让 LLM 更困惑。
- **发现的问题/建议**:
  1. **writer 四合一风险最高**：用户说"写回翻译"时 LLM 不知道目标格式，选错格式后果严重 — 优先级: 高
  2. **不建议合并 create_label + remove_label**：创建和删除是不同语义，合并让 LLM 多一步 action 推断 — 优先级: 高
  3. **工具描述强化比合并更紧急**：60% 工具描述少于 20 中文字符，缺少"使用 X 而非 Y"的区分信号 — 优先级: 高
  4. **描述设计三原则**：① 一句话说清"何时用我，不用别的工具" ② 参数 enum 值前置 ③ 避免万能工具，用示例说明典型组合 — 优先级: 中
  5. **迭代规划**：迭代 1 死代码清理（1-2 天）→ 迭代 2 set_filters+stop_task（1-2 天）→ 迭代 3 write_back（1 天）→ 迭代 4 v1 清理（0.5 天） — 优先级: 中
  6. **Scope Creep 停止规则**：只合并"同语义不同参数化"的工具，不合并"不同语义"或"不同实现路径"的工具 — 优先级: 中

---

## 共识汇总

以下建议获得**多个角色一致认同**：

- [x] **筛选合并范围扩大**（四角色一致）：filter_by_stage/category/label + search_entries + clear_all_filters → set_filters（5→1）
- [x] **stop_task 合并无争议**（四角色一致）：task_id 非必传，None=停止所有
- [x] **write_back 合并需审慎**（四角色均标记为最高/次高风险）：需 dispatch 表路由 + 描述中明确推断规则 + 回显确认写入类型
- [x] **Agent 死代码应立即删除**（四角色一致）：orchestrator.py + agent_worker.py 从未被引用，-194 行零风险
- [x] **注册样板消除**（四角色一致）：7 模块改用已存在的 `register_tools()`，-35 行重复代码
- [x] **旧工具保留 DeprecationWarning wrapper**（架构师+开发者+产品）：不直接删除，观察 1-2 迭代后清理
- [x] **不需要改 ConversationOrchestrator/prompts.py**（架构师+开发者）：`build_tool_schema_for_prompt(None)` 自动排除 deprecated

---

## 分歧与冲突

### 冲突 1：标签管理合并策略

| 角色 | 方案 |
|------|------|
| **架构师** | `assign_label + remove_label + batch_assign_label → manage_entry_labels`，`create_label` 独立保留 |
| **产品经理** | 不建议合并 create_label + remove_label，两者语义不同 |
| **QA** | `action` 支持 `create\|assign\|unassign\|delete_definition` 全塞入 |
| **开发者** | 按第一轮纪要合并为 `manage_label(action=create/delete/remove_from_entries)` |

**架构师与产品的观点本质一致**：`create_label`（标签定义）和 `remove_label`（条目关联）操作不同对象，不应合并。QA 和开发者倾向于全量合并。

### 冲突 2：实施并行度

| 角色 | 策略 |
|------|------|
| **架构师** | Phase 1 四个合并 Story 完全可并行（修改不同文件） |
| **开发者** | 分阶段串行（先清理→再简单合并→最后复杂合并） |
| **产品经理** | 迭代 1→2→3→4 串行（先热身→再上难度） |
| **QA** | 先写黄金回归测试，再逐步合并 |

两种策略不矛盾，可折中：先串行热身（死代码+样板），再并行合并（四个独立模块）。

---

## 综合建议清单

### 高优先级

- [ ] **set_filters（5→1）**：合并 filter_by_stage/category/label + search_entries + clear_all_filters
  - 参数：`stages/categories/labels/search_query/search_field/clear`，全部可选，None=不修改该维度
  - 旧工具保留 deprecated wrapper，system prompt 提示 "使用 set_filters 统一管理筛选"
- [ ] **write_back（4→1）**：合并 write_to_esp/eet/xt/strings
  - 使用 dispatch 表路由，保留 `require_confirmation=True` + `permission="admin"`
  - 回显确认实际写入的目标类型，系统 prompt 增加 writer 使用指南
- [ ] **stop_task（2→1）**：task_id 改为可选，None/空="" → 停止所有
- [ ] **manage_entry_labels（3→1）**：合并 assign_label + remove_label + batch_assign_label（`create_label` 独立保留 — 架构师修正方案）
- [ ] **死代码清理**：删除 agents/orchestrator.py（-121 行）、agents/agent_worker.py（-73 行）
- [ ] **工具描述强化**：遵循三原则重写合并后工具的 description

### 中优先级

- [ ] **注册样板消除**：7 个工具模块改用 `ToolRegistry.register_tools()`，减少 ~35 行重复代码
- [ ] **测试体系重构**：创建 `tests/smart_assistant/` 子目录，提供 conftest.py
- [ ] **ToolRegistry.reset()**：增加测试隔离方法
- [ ] **Writer 测试补全**：使用 Mock PluginWriter（当前覆盖率为零）
- [ ] **系统 prompt 增加工具选择指南**：常见用户场景 → 对应工具 + 易混淆工具对 → 如何区分

### 低优先级

- [ ] 清理 tool_v1.py 废弃工具（确认无外部依赖后）
- [ ] 监控 LLM 工具选择准确率（ObservabilityCollector 增加选择日志）
- [ ] 未来工具数超 80 时重新评估 namespace 过滤
- [ ] 确认弹窗 display_name 使用用户友好名称

---

## 合并前后工具数变化

| Namespace | 当前 | 合并后 |
|-----------|------|--------|
| editor | 14 | 8（-6：5→1 筛选 + 3→1 标签关联） |
| translator | 9 | 8（-1：2→1 stop） |
| writer | 4 | 1（-3：4→1 write_back） |
| proofreader | 6 | 6（不合并） |
| paratranz | 10 | 10（不合并） |
| parser | 6 | 6（不合并） |
| default | 7 | 7（不合并） |
| **总计** | **56** | **46**（-10） |

> 后续 v1 废弃清理（-5）可到 41，进一步可选合并可接近 30。

---

## 实施阶段建议

```
Phase 1a（热身）: Agent 死代码删除 + 注册样板消除  ← 零风险，1-2 天
Phase 1b（并行合并）:                               ← 修改不同文件，可并行
  ├── Story A: set_filters（tool_editor.py）
  ├── Story B: stop_task（tool_translator.py）
  ├── Story C: write_back（tool_writer.py）
  └── Story D: manage_entry_labels（tool_editor.py，与 A 同文件需串行）
Phase 2（加固）: 工具描述强化 + 系统 prompt 工具选择指南 + 测试补全
Phase 3（清理）: tool_v1.py 废弃工具删除 + 监控接入
```

## 纪要不构成决议

本文件仅为各角色独立意见的客观汇总，**不强制要求采纳任何建议**。最终决策权归用户所有。分歧点（标签合并策略、实施并行度）需用户裁决后确定最终方案。
