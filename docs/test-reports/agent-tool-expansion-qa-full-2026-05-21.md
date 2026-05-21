# Agent 工具系统全面 QA 审查报告

**日期**: 2026-05-21
**审查类型**: 全面审查 (4维度并行)
**对应方案**: `plans/agent-tool-expansion/plan.md`
**审查范围**: `src/transbridge/smart_assistant/tools/` (11文件, ~3000行, 41工具)
**测试基线**: 187/189 通过 (2预存失败)

---

## 审查维度与方法

4 Agent 并行审查，各负责一个维度：

| 维度 | Agent | 重点 |
|------|-------|------|
| **功能测试** | Function QA | Plan验收标准、边界用例、工具计数、参数校验 |
| **安全审查** | Security Audit | 路径遍历、注入、权限模型、敏感数据暴露、线程安全 |
| **性能审查** | Performance | O(n²)模式、内存、线程池、锁竞争、API效率 |
| **代码质量** | Code Quality | DRY、函数长度、命名一致性、错误处理、测试覆盖 |

---

## 问题汇总 (去重后)

### BLOCKER (1项)

| ID | 问题 | 来源 | 文件:行号 |
|----|------|------|----------|
| **B1** | `_tool_stop_task` 对不存在的 task_id 调用 `ToolResult.fail(..., data={...})` 导致 **TypeError 崩溃**。`ToolResult.fail()` 不接受 `data=` 参数。同样 `ToolResult.ok()` 被传入 `partial=True` 也不接受。 | Function, Code Quality | `tool_translator.py:352-353`, `tool_translator.py:337-338` |

**复现**:
```python
# tool_translator.py:352
return ToolResult.fail(f"任务不存在或已结束: {task_id}",
                       data={"task_id": task_id, "action": action})
# ↑ TypeError: ToolResult.fail() got an unexpected keyword argument 'data'

# tool_translator.py:337
return ToolResult.ok(f"已{_action_label(action)} {len(affected)} 个任务，{len(failed)} 失败",
                    data=data, partial=True)
# ↑ TypeError: ToolResult.ok() got an unexpected keyword argument 'partial'
```

**修复建议**: 移除 `data=` 参数（将信息并入 message），或将 `data`/`partial` 参数支持加入 `ToolResult.fail()`/`ToolResult.ok()` 工厂方法。

---

### CRITICAL (4项)

| ID | 问题 | 来源 | 文件:行号 |
|----|------|------|----------|
| **C1** | **ParaTranz 工具零测试覆盖** — 9个函数 (`list_projects`, `get_project_info`, `compare_with_remote`, `upload_entries`, `download_entries`, `export_artifact`, `get_upload_history`, `get_paratranz_project`, `switch_paratranz_project`) 没有任何测试 | Code Quality | `tool_paratranz.py` |
| **C2** | **`_tool_run_postprocess` 零执行测试** — 最复杂的工具函数 (208行) 没有测试覆盖 | Code Quality | `tool_proofreader.py:30-245` |
| **C3** | **TaskManager `_listeners` 无锁访问** — `on_completed()`/`on_failed()`/`remove_listener()` 修改 `_listeners` 时未持有 `_lock`，`notify_completed()`/`notify_failed()` 遍历时也未加锁。并发调用可导致 `RuntimeError: dictionary changed size during iteration` | Security | `task_manager.py:99-113, 221-235` |
| **C4** | **`export_artifact` 缺少 `require_confirmation`** — 该工具触发服务端导出任务消耗资源，但注册时未设 `require_confirmation: True`，与 `upload_entries`/`download_entries` 不一致 | Security | `tool_paratranz.py:269-271` |

---

### MAJOR (10项)

| ID | 问题 | 来源 | 文件:行号 |
|----|------|------|----------|
| **M1** | **5个Parser函数大量重复代码** — `_tool_parse_esp/eet/xt/sst` + `_tool_import_json` 模式95%相同 (~100行重复)，应提取工厂函数+dispatch表 | Code Quality | `tool_parser.py:125-241` |
| **M2** | **线程创建样板重复** — `_tool_start_translation`/`_tool_start_polish`/`_tool_run_postprocess` 共享~25行相同的线程创建/注册/错误处理样板 (~75行重复) | Code Quality | `tool_translator.py`, `tool_proofreader.py` |
| **M3** | **`scope→entry_ids` 解析逻辑重复** — `_tool_start_translation` 和 `_tool_run_postprocess` 中有相同的12行 scope解析代码（从 `ctx.translation_scope` 转为 `filter_state` → `filter_entries`）| Code Quality, Performance | `tool_translator.py:68-89`, `tool_proofreader.py:56-67` |
| **M4** | **ParaTranz 工具无日志** — `tool_paratranz.py` 全部9个函数捕获 `Exception` 返回 `ToolResult.fail()` 但 `logging` 模块未导入，API失败零日志输出，排查困难 | Code Quality | `tool_paratranz.py` |
| **M5** | **`list_quality_reports` 暴露完整目录路径** — 返回的 `directory` 字段含绝对路径，泄露用户文件系统结构到LLM上下文 | Security | `tool_proofreader.py:307` |
| **M6** | **`_tool_upload_entries` 逐条API调用无批量** — 500条目=500次HTTP请求，码中已标注已知限制(M10)但无缓解措施（如并发+限流） | Performance | `tool_paratranz.py:93-97` |
| **M7** | **`filter_entries()` 分页时重复全量扫描** — 每次 `get_visible_entries` 调用都从零过滤整个collection，LLM翻20页会重新扫描20次 | Performance | `tool_editor.py:107-150` |
| **M8** | **跨模块 `_last_report` 全局变量耦合** — `tool_translator.py:271` 直接写入 `tool_proofreader._last_report`，且无锁保护，后台线程与Agent线程竞态 | Code Quality, Performance | `tool_translator.py:271`, `tool_proofreader.py:25` |
| **M9** | **Parser工具权限与Plan不一致** — Plan(H6)规定parser工具 `permission: "read"`，代码中5个parser工具均注册为 `permission: "write"`（Story 24副作用补全所致），需记录ADR修正 | Security | `tool_parser.py:274-283` |
| **M10** | **`tool_proofreader.py` `_tool_run_postprocess` 过长** — 函数体208行，内部`_run()`闭包147行，违反单一职责，应拆分为~5个辅助函数 | Code Quality | `tool_proofreader.py:30-245` |

---

### MINOR (11项)

| ID | 问题 | 来源 | 文件:行号 |
|----|------|------|----------|
| **m1** | `set_filters` `clear=True` 单独使用时返回 `{unchanged: true}` 但实际已执行清除 | Function | `tool_editor.py:94-95` |
| **m2** | `test_tool_count_is_45` 期望值过期 — 实际41工具，测试注释中的计数是Story 17-25合并前的 | Function | `test_tool_consolidation.py:256` |
| **m3** | `copy.deepcopy(handle.progress)` 在锁内执行 — progress为扁平dict，`dict()`浅拷贝即可，一行修复 | Performance | `task_manager.py:184` |
| **m4** | `cleanup_all()` 在锁内调用 `thread.join(timeout=2)` — 阻塞其他TaskManager操作最多2秒/线程 | Performance, Security | `task_manager.py:255-267` |
| **m5** | `execute_with_guardrails` 中 after中间件链代码重复 — `ToolResult`和`dict`两个分支的after链逻辑相同 | Code Quality | `base.py:408-445` |
| **m6** | 无线程池复用 — 3处长运行工具每次创建新 `threading.Thread`，无并发上限 | Performance | `tool_translator.py:159,294`, `tool_proofreader.py:236` |
| **m7** | `_tool_compare_with_remote` 只获取500条远程条目 — 大项目对比结果不准确 | Performance | `tool_paratranz.py:53` |
| **m8** | `_tool_download_entries` 2000条硬限制无可配置分页 | Performance | `tool_paratranz.py:119` |
| **m9** | `_tool_export_artifact` 30秒阻塞轮询占用Agent工作线程 | Performance | `tool_paratranz.py:156-166` |
| **m10** | 多个translator工具绕过 `@validate_params` 装饰器，用手动校验代替 — 不一致的校验模式 | Security | `tool_translator.py` |
| **m11** | `_PARAM_SCHEMAS` 位置不一致 — `tool_editor.py` 在文件顶部（因装饰器引用），其他模块在底部 | Code Quality | 各工具文件 |

---

## 问题严重级别分布

| 级别 | 数量 | 说明 |
|------|------|------|
| Blocker | 1 | TypeError崩溃 |
| Critical | 4 | 零测试覆盖 + 线程安全 + 缺少确认 |
| Major | 10 | 代码重复、日志缺失、路径泄露、性能瓶颈 |
| Minor | 11 | 消息不准确、过期测试、小优化 |
| **合计** | **26** | |

---

## 测试覆盖现状

| 模块 | 工具数 | 测试文件 | 测试用例 | 覆盖状态 |
|------|--------|---------|---------|---------|
| `tool_editor.py` | 7 | `test_tool_consolidation.py` + `test_agent_tool_integration.py` | ~55 | 较好 |
| `tool_translator.py` | 9 | `test_agent_tool_integration.py` (部分) | ~15 | 部分 |
| `tool_proofreader.py` | 3 | `postprocess/test_report_system.py` + `postprocess/test_param_validation.py` | ~45 | 较好(辅助函数), 差(核心工具) |
| `tool_paratranz.py` | 9 | — | 0 | **零覆盖** |
| `tool_parser.py` | 5 | `test_agent_tool_integration.py` (仅路径校验) | ~5 | 差 |
| `tool_writer.py` | 1 | `test_tool_consolidation.py` + `test_agent_tool_integration.py` | ~15 | 较好 |
| `tool_default.py` | 7 | `test_agent_tool_integration.py` (部分) | ~10 | 部分 |
| `task_manager.py` | — | `postprocess/test_task_manager.py` | ~30 | 较好 |
| `base.py` | — | 散布在各测试中 | ~20 | 部分 |
| **合计** | **41** | | **~195** | |

### 测试缺口重点
- **ParaTranz 9工具**: 零测试 — 最严重的覆盖缺口 (C1)
- **`_tool_run_postprocess`**: 208行核心逻辑无执行测试 (C2)
- **Parser 5工具**: 仅路径校验有测试，create_slot/append副作用未覆盖
- **Translator 配置工具**: `get_translation_config`/`set_translation_config`/`set_term_config` 仅1个浅测试

---

## Plan 合规性检查

| Story | 关键要求 | 状态 |
|-------|---------|------|
| 01 | ToolResult v2 (success: bool + partial) | ✅ |
| 01 | ExecutionContext + __getattr__ 代理 | ✅ |
| 01 | execute_with_guardrails 统一入口 | ✅ |
| 01 | filter_entries 公共函数 | ✅ |
| 01 | @require_collection / @validate_params | ✅ |
| 02 | TaskManager 单例 + 线程安全 | ⚠️ `_listeners` 无锁 (C3) |
| 04/17 | set_filters 合并 5→1 | ✅ (clear=True消息不准 m1) |
| 06/18 | stop_task 合并 2→1 + action参数 | ❌ TypeError崩溃 (B1) |
| 08/20 | manage_entry_labels 合并 4→1 | ✅ |
| 12/19 | write_back 合并 4→1 + dispatch表 | ✅ |
| 12/24 | Parser 副作用 (create_slot/append) | ✅ (权限改write待记录 M9) |
| 13 | Agent注册 + ExecutionEngine适配 | ⚠️ `_expand_wildcard` import错误(测试) |
| 15 | search_entries 6字段 + PT项目切换 | ✅ |
| 21 | 描述强化 + 测试补全 | ⚠️ 工具计数过期 (m2) |
| 22 | 工具描述重写 (Claude Code格式) | ✅ (前次QA已通过) |
| 25 | run_postprocess 统一 (6→3工具) | ✅ (计划说2工具但实际3) |
| 26 | 断点续传 + 暂停/恢复 | ✅ |

---

## 审查结论

- **方案一致性**: ⚠️ 基本一致。主要偏差: parser权限read→write(Story 24变更,需记录)、proofreader工具数3≠2(补充了list_quality_reports)
- **代码质量**: ⚠️ 中上。有显著DRY违规(parser/线程样板)、一个208行函数、跨模块全局变量耦合。结构骨架良好，细节需打磨
- **安全性**: ⚠️ 中等。路径遍历防御到位、护栏链正确。但TaskManager监听器线程不安全、export_artifact缺确认、list_quality_reports暴露路径
- **测试覆盖**: ❌ 不足。ParaTranz零覆盖、run_postprocess零执行测试、parser仅路径校验

**综合评分**: 45/60 (功能15/20 + 安全11/15 + 性能9/12 + 代码质量10/13)

---

## 修复优先级建议

### 立即修复 (P0)
1. **B1**: 修复 `_tool_stop_task` TypeError (1行修复，移除 `data=` 参数)
2. **C3**: 给 `_listeners` 访问加锁

### 本轮修复 (P1)
3. **C4**: `export_artifact` 加 `require_confirmation: True`
4. **C1**: ParaTranz 工具基础测试 (至少每个工具1个happy path)
5. **M5**: `list_quality_reports` 移除 `directory` 字段
6. **M4**: `tool_paratranz.py` 加 logging

### 下轮优化 (P2)
7. **M1-M3**: 消除代码重复 (线程样板、parser工厂、scope解析)
8. **M6-M7**: 性能优化 (批量上传、filter缓存)
9. **M10**: 拆分 `_tool_run_postprocess`

---

## 签名

**QA 审查完成 — 需修复** ⚠️
26项问题 (1B + 4C + 10M + 11m)，核心管线可用但存在1个Blocker崩溃和关键测试缺口
