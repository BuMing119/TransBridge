# Agent 工具系统全面扩展 (agent-tool-expansion) — 测试报告

**日期**: 2026-05-11
**对应方案**: `plans/agent-tool-expansion/plan.md`（v2，14 Story）
**审查模式**: 多实例并行（功能测试 + 安全审查 + 代码质量审查）

---

## 测试覆盖

### 已有测试执行结果

| 测试类 | 用例数 | 状态 | 备注 |
|--------|--------|------|------|
| TestToolResultV2 | 8 | ✅ 全部通过 | ToolResult 数据类 + 字典兼容 |
| TestExecutionContext | 4 | ✅ 全部通过 | ExecutionContext __getattr__ 代理 |
| TestFilterEntries | 6 | ✅ 全部通过 | _filter_entries 基础筛选 |
| TestRequireCollection | 3 | ✅ 全部通过 | @require_collection 装饰器 |
| TestValidateParams | 3 | ✅ 全部通过 | @validate_params 装饰器 |
| TestGuardChain | 5 | ✅ 全部通过 | execute_with_guardrails 中间件链 |
| TestTaskManager | 8 | ✅ 全部通过 | TaskManager 注册/取消/状态/清理 |
| TestEditorTools | 12 | ✅ 全部通过 | 筛选/搜索/编辑/选择/Stage 工具 |
| TestLabelTools | 8 | ✅ 全部通过 | 标签 CRUD/分配/移除/批量 |
| TestTranslatorTools | 8 | ✅ 全部通过 | 翻译配置/作用域/任务控制 |
| TestParaTranzTools | 5 | ✅ 全部通过 | 项目列表/对比/下载确认 |
| TestIntegration | 19 | ✅ 全部通过 | 全链路：筛选→选择→翻译→标记 |
| **总计** | **89** | **✅ 全部通过** | 0 失败，0 错误 |

### 计划验收标准覆盖

| Story | 覆盖状态 | 说明 |
|-------|---------|------|
| S01 基础设施 (ToolResult/ExecutionContext/HITL/GuardChain/装饰器) | ✅ 覆盖 | 8+4+3+3+5 测试 |
| S02 TaskManager | ✅ 覆盖 | 8 测试（含线程安全） |
| S03 AppContext ViewModel | ✅ 覆盖 | 集成测试中 filter_state/label_library |
| S04 P0 筛选编辑工具 | ✅ 覆盖 | 12 测试 |
| S06 P0 翻译控制 | ✅ 覆盖 | 8 测试 |
| S07 P0 状态查询 | ✅ 覆盖 | 集成测试 |
| S08 P1 标签工具 | ✅ 覆盖 | 8 测试 |
| S09 P1 翻译配置 | ✅ 覆盖 | 8 测试 |
| S10 P1 后处理工具 | ❌ 未覆盖 | 工具存在但工厂函数崩溃（见 M3） |
| S11 P1 ParaTranz | ✅ 覆盖 | 5 测试 |
| S12 P2 解析写回 | ⚠ 部分覆盖 | 集成测试中基础路径覆盖 |
| S13 Agent 集成 | ⚠ 部分覆盖 | ExecutionContext 包装未验证 |
| S14 集成测试 | ✅ 覆盖 | 19 条全链路测试 |

---

## 发现的问题

### 🔴 Blocker (2)

| # | 问题 | 位置 | 影响 |
|---|------|------|------|
| B1 | **双重护栏执行路径互相绕过** — `execute_with_guardrails()` 和 `ExecutionEngine._run_single()` 各有独立的中间件链，配置可能分歧 | `tools/base.py:145-196` / `execution_engine.py:49-167` | 护栏防护可能因路径不同而被绕过 |
| B2 | **护栏模块导入失败时静默丢弃所有安全检查** — `except ImportError` 分支直接执行工具，无日志告警 | `tools/base.py:151-158` | 护栏模块损坏时所有安全防护消失 |

### 🟠 Critical (6)

| # | 问题 | 位置 | 影响 |
|---|------|------|------|
| C1 | **`set_translation_config` 允许直接覆盖 `base_url`** — 未在白名单中排除，可重定向 LLM API 请求到攻击者服务器 | `tools/tool_translator.py:269-273` | API Key 泄露 + 费用欺诈 |
| C2 | **大部分 write 工具缺少确认** — `require_confirmation=False` 且 `write_require_confirm=False`（默认），包括 `start_translation`/`set_translation_config`/`upload_entries` 等 | `guardrails/permission.py:11-33` | 大量写操作/API 调用静默执行 |
| C3 | **`start_translation` 可无限制翻译全部条目** — `entry_ids=None` 时翻译整个集合，无上限/无费用预估/无确认 | `tools/tool_translator.py:19-91` | LLM API 费用失控 |
| C4 | **SST 解析器绕过扩展名白名单** — `_tool_parse_sst` 不调用 `_validate_path()`，仅检查 `os.path.exists()` | `tools/tool_parser.py:73-86` | 任意文件可被当作 SST 解析 |
| C5 | **注入检测模式严重不完整** — SQL 注入仅 5 个关键字，命令注入仅 7 个命令，XSS 仅 `<script` 和 `onerror=` | `guardrails/input_validator.py:9-16` | 大量注入路径未被检测 |
| C6 | **写回工具接受任意输出路径** — `path` 参数未经验证直接传给 `writer.write()` | `tools/tool_writer.py:25-27` | admin 确认后可写入磁盘任意位置 |

### 🟡 Major (8)

| # | 问题 | 位置 | 影响 |
|---|------|------|------|
| M1 | **`_filter_entries` 标签筛选完全失效** — `getattr(filter_state, ...)` 用于 dict 类型，总是返回默认值 {} | `tools/base.py:223-228` | 标签筛选始终返回 0 结果 |
| M2 | **`@validate_params` 从未应用于任何工具** — 装饰器已定义、schema 已定义，但所有 `_tool_*` 函数未使用 | 全局 | 运行时参数无校验 |
| M3 | **后处理工厂函数 `_run_postprocess_phase` 在所有路径崩溃** — `processor_class=None` 导致 `None()` TypeError | `tools/tool_proofreader.py:18-56` | 5 个后处理工具静默失败 |
| M4 | **`_validate_path` 不拒绝绝对路径** — 仅检测 `../`，未实现 `_detect_path_traversal` 的完整规范 | `tools/tool_parser.py:21` | 绝对路径注入未被阻止 |
| M5 | **TaskManager 私有成员被外部直接访问** — `tm._lock`/`tm._tasks` 在 translator/proofreader 中直接引用 | `tools/tool_translator.py:64-82` | 封装破坏 |
| M6 | **MCP ExecutionContext 缺少 task_manager** — `MCPAdapter.call_tool` 创建 ExecutionContext 时未传入 task_manager | `mcp/adapter.py:42` | MCP 通道长运行任务无法管理 |
| M7 | **ExecutionEngine 未包装 ExecutionContext** — 直接传递 `AppContext` 而非 `ExecutionContext(app_context=ctx, task_manager=...)` | `execution_engine.py:73` | 引擎路径缺少 TaskManager |
| M8 | **writer 工具 EET/XT 委托给 ESP 实现** — `_tool_write_to_eet` 和 `_tool_write_to_xt` 直接调用 `_tool_write_to_esp` | `tools/tool_writer.py:33-41` | 格式名不副实 |

### 🔵 Minor (12)

| # | 问题 | 位置 |
|---|------|------|
| m1 | 未使用导入：`validate_params` 在 `tool_editor.py:8` 导入但未使用 | `tools/tool_editor.py` |
| m2 | MCP schema 将所有参数标记为 required，即使可选参数亦然 | `mcp/adapter.py:66` |
| m3 | 输出脱敏不处理 tuple 类型（仅 str/dict/list） | `guardrails/output_validator.py:52-66` |
| m4 | `get_app_state` 泄露内部文件系统绝对路径 | `tools/tool_default.py:20-22` |
| m5 | `set_translation_config` 静默丢弃未知参数（无错误反馈） | `tools/tool_translator.py:269-273` |
| m6 | `get_translation_config` 不返回 term_db/post_process_stages | `tools/tool_translator.py:225-243` |
| m7 | `ctx.active_project.name` 可能因类型不匹配引发 AttributeError | `tools/tool_default.py:23` |
| m8 | `ctx.label_library` 可能为 None 时 `list_labels` 崩溃 | `tools/tool_editor.py:179` |
| m9 | `_tool_import_json` 存在 TOCTOU 竞态条件（文件在检查后删除） | `tools/tool_parser.py:89` |
| m10 | `writer` 工具注册元组缺少权限字段（与其他模块格式不一致） | `tools/tool_writer.py:73-78` |
| m11 | 输出脱敏缺少 AWS Key/GitHub Token/JWT/SSH Key/ParaTranz Token 模式 | `guardrails/output_validator.py:9-13` |
| m12 | MCP Server 无速率限制（可被用于 LLM API 费用 DoS） | `mcp/server.py:19-38` |

---

## 审查结论

### 方案一致性: ⚠ 部分一致
- 核心架构（ToolResult/ExecutionContext/ToolRegistry namespace/TaskManager/GuardChain）符合方案设计
- 但 `@validate_params` 未实际应用、后处理工厂未完成、`_filter_entries` 标签筛选逻辑错误，导致约 20 个注册工具功能异常

### 代码质量: ⚠ 需修复
- 代码结构清晰，命名空间隔离良好，无 UI 组件泄漏到 tools/ 包
- M1/M2/M3 为阻塞级代码缺陷，修复后方可认为代码质量达标

### 安全性: ❌ 需重大修复
- 2 Blocker + 6 Critical，其中最紧迫的是：
  - 双重护栏路径（B1）
  - 护栏静默降级（B2）
  - `base_url` 可被重定向（C1）
  - write 工具批量无确认（C2/C3）
- 在 C1/C2/C3 修复前不建议在生产环境启用 Agent 工具系统

---

## 签名

**QA 状态**: ⛔ **需修复** — 发现 2 Blocker + 6 Critical，必须修复后复验
**测试执行**: ✅ 89/89 通过（现有测试覆盖范围内）
**下一步**: 修复 B1/B2/C1/C2/C3 后重新调用 `/bm-qa agent-tool-expansion` 复验
