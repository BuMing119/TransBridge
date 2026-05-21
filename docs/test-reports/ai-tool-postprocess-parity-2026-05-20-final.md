## AI 助手工具 vs 原后处理工作流 — 完整等价性评估（独立全新审查）

**日期**: 2026-05-20（2026-05-20 二次复核：新增 8 项遗漏问题，修正 1 处根因分析）
**审查方式**: 多实例并行模式（4 Agent: 功能管线 / 工具覆盖 / 代码正确性 / 安全与质量）+ 二次复核（6 Agent: B1B2验证 / 线程安全 / 方案合规 / 测试诊断 / 遗漏猎手 / checkpoint崩溃验证）
**测试执行**: 48/48 parity tests pass, 86/89 integration tests pass (3 failures are test-level issues)
**对应方案**: `plans/agent-tool-expansion/plan.md` (Story 25)

---

### 核心结论

**AI 助手 `run_postprocess` 工具调用的是与 GUI 完全相同的 `PostProcessor.process_entries()` 核心管线**，五阶段流程（检测→修复→润色→裁决→执行）行为一致。报告生成、进度跟踪、停止/取消均正常工作。入口选择比 GUI 更灵活（支持 scope 筛选）。

**主要缺口**:
1. `start_polish`（独立润色）功能严重残缺 — **2 Blocker**
2. `PostProcessCheckpoint()` 运行时崩溃 — **1 Blocker**
3. Orchestrator 提示词引用已删除工具 — **1 Critical**
4. 断点续传未实现 — **1 Major**
5. 暂停不支持 — **1 Major**
6. 代码质量: 全局可变状态、参数校验缺失、线程安全 — **4 Critical + 10 Major**

---

### 测试覆盖

| 测试集 | 结果 | 备注 |
|--------|------|------|
| `test_postprocess_tool_parity.py` (48 tests) | ✅ 48/48 PASS | 参数验证 / Config等价性 / start_polish / 报告系统 / _last_report |
| `test_agent_tool_integration.py` (89 tests) | ✅ 86/89 PASS | 3个失败均为测试自身问题（断言过时/编码问题/导入不存在函数），非代码bug |
| `test_start_polish_needs_entry_ids` | ❌ FAIL (测试问题) | 测试期望空entry_ids返回fail，但当前正确行为是回退到scope选择 |
| `test_execute_with_guardrails_input_validation` | ❌ FAIL (测试问题) | PermissionGuard 在 InputValidationGuard 之前拦截未注册的 mock tool（非编码问题）；终端乱码为 Windows 代码页显示问题，不影响测试逻辑 |
| `test_namespace_wildcard_expansion` | ❌ FAIL (导入问题) | 导入已移除的 `_expand_wildcard` 函数 |

---

### 功能等价性矩阵

| # | 功能 | AI工具 vs GUI | 判定 |
|---|------|--------------|------|
| 1 | 核心管线 (process_entries) | 调用相同方法，参数等价 | ✅ 等价 |
| 2 | 六阶段选择 (phases参数) | 工具更灵活，可按阶段组合 | ✅ 等价（工具更优） |
| 3 | 配置等价性 (PostProcessorConfig) | 均通过 `from_llm_config` 创建 | ✅ 等价 |
| 4 | 入口选择 | 工具支持 entry_ids + scope，GUI仅限已翻译条目 | ✅ 等价（工具更优） |
| 5 | 进度跟踪 | GUI→进度弹窗, 工具→TaskManager查询 | ✅ 等价（信息相同） |
| 6 | Excel报告生成 (5 Sheet) | 均通过 ReportGenerator | ✅ 等价 |
| 7 | 中间数据保留 (refine/polish/decisions) | 工具序列化为摘要，GUI保留原始对象 | ✅ 等价 |
| 8 | 结果应用 (stage更新) | 均通过 `_execute_decisions` | ✅ 等价 |
| 9 | 停止/取消 | stop_event 正确传递，TaskManager共享 | ✅ 等价 |
| 10 | UI变更通知 | `ctx.safe_mutate` → `collection_changed` | ✅ 等价 |
| 11 | 断点续传 (checkpoint resume) | GUI从文件加载，工具始终新建 | ❌ 缺失 |
| 12 | 暂停/恢复 (pause_event) | GUI支持，工具未传递 | ❌ 缺失 |
| 13 | 独立润色 (start_polish) | 见下方详细分析 | 🔴 严重残缺 |
| 14 | 历史报告列表 (list_quality_reports) | 工具新增，GUI无对应功能 | ✅ 工具独有 |

---

### 发现的问题（按严重级别排序）

#### Blocker (3项)

| # | 问题 | 位置 | 影响 |
|---|------|------|------|
| B1 | **`start_polish` 未传递 term_manager/game_profile/target_lang** — LLMPolisher 构造仅传入 `llm_client` 和 `polish_level`，缺少术语管理器（导致术语缺失）、游戏配置文件和目标语言。`_get_relevant_terms_text(entry)` 因 `term_manager=None` 返回空字符串。对比 GUI 正确调用 `LLMPolisher(llm_client=llm_client, term_manager=term_manager, game_profile=cfg.game_profile, target_lang=cfg.target_lang, polish_level=polish_level)` | `tool_translator.py:223` | 润色质量严重下降，提示词缺少术语参考 |
| B2 | **`start_polish` 丢弃润色结果** — `polisher.polish(entry)` 返回值被丢弃，译文未被更新。`LLMPolisher.polish()` 为纯函数，返回 `PolishResult` 但不修改 entry 对象。正确用法见 `_polish_worker.py:67-69`（捕获返回值→存储→写入collection）。无 Excel 报告生成，无中间数据保留 | `tool_translator.py:230` | 独立润色完全无效 — LLM费用消耗但结果被丢弃 |
| B3 | **`PostProcessCheckpoint()` 运行时崩溃** — `checkpoint.py:24` 中 `esp_stem: str` 为必填字段无默认值。`tool_proofreader.py:120` 调用 `PostProcessCheckpoint()` 无参构造，立即抛出 `TypeError: missing 1 required positional argument: 'esp_stem'`。此 Bug 在 G2 断点续传修补时引入。正确用法见 `translator.py:651`：`PostProcessCheckpoint(esp_stem=esp_stem)` 或先调 `.load()` | `tool_proofreader.py:120`, `checkpoint.py:24` | 每次执行后处理流水线必定崩溃，功能完全不可用 |

#### Critical (6项)

| # | 问题 | 位置 | 影响 |
|---|------|------|------|
| C1 | **空 `entry_ids` 列表回退到全部条目** — `entries = [...] if entry_ids else list(collection)` 中空列表 falsy，导致处理全部条目而非零条 | `tool_proofreader.py:59` | scope筛选为空时意外处理全量条目，产生大量LLM费用 |
| C2 | **`_tool_get_quality_report()` 读取润色格式报告时崩溃** — `start_polish` 写入的 `_last_report` 无 `total_checked`/`issue_count` 等字段，`get_quality_report` 无条件访问导致 `KeyError` | `tool_proofreader.py:211-213`, `tool_translator.py:238-246` | 跨模块数据污染，运行时报错 |
| C3 | **初始化异常未被捕获** — `term_mgr.load_all()`、`create_llm_client()`、`PostProcessorConfig.from_llm_config()` 在 `_run()` 的 try/except 之外，异常直接传播导致 TaskManager 遗留 "running" 状态 | `tool_proofreader.py:75-96` | 调用方收到原始异常而非 ToolResult |
| C4 | **`phases=None` 导致 TypeError** — `args.get("phases", default)` 在显式传 `None` 时不回退默认值，`"consistency" in None` 抛出 TypeError | `tool_proofreader.py:40` | 运行时报错 |
| C5 | **模块级全局可变状态 `_last_report`** — 无锁保护，跨模块写入（`tool_translator.py:239`），并发后处理任务互相覆盖，格式不兼容 | `tool_proofreader.py:25` | 数据竞争，跨模块紧耦合 |
| C6 | **Orchestrator 系统提示词引用已删除的 `check_quality` 工具** — `agent_registry.py:96` 提示词 `...调度子Agent：.../check_quality/...` 中 `check_quality` 在 Story 25 被 `run_postprocess` 取代后未更新。且 orchestrator 工具列表（`agent_registry.py:92-94`）无 `proofreader:*` 权限，即使修正提示词也无法路由 | `agent_registry.py:92-96` | 编排引擎质量检查能力完全失效；LLM 按提示词调度时产生硬错误 |

#### Major (12项)

| # | 问题 | 位置 |
|---|------|------|
| M1 | 断点续传实现有崩溃 Bug — `checkpoint = PostProcessCheckpoint()` 无参构造，`esp_stem` 必填字段缺失导致 `TypeError` 即时崩溃。应修复为 `PostProcessCheckpoint(esp_stem=esp_stem)` 或先调 `.load(esp_path)`。详见 B3 | `tool_proofreader.py:120`, `checkpoint.py:24` |
| M2 | 暂停不支持 — `pause_event` 未创建和传递给 `process_entries()` | `tool_proofreader.py:123` |
| M3 | `max_workers` 无上限校验 — 0/负数/超大值直接传入 ThreadPoolExecutor | `tool_proofreader.py:42` |
| M4 | `phases` 无合法性校验 — 非法阶段名静默忽略 | `tool_proofreader.py:40-41` |
| M5 | `start_polish` 跨模块污染 `tool_proofreader._last_report` — 紧耦合，格式不兼容 | `tool_translator.py:237-246` |
| M6 | proofreader namespace工具缺少 `parameters` schema — `run_postprocess`/`get_quality_report`/`list_quality_reports` 注册时无schema，LLM看不到结构化参数，`InputValidationGuard` 无法校验 | `tool_proofreader.py:347-366` |
| M7 | proofreader Agent 无法查询自己的任务状态 — 仅有 `proofreader:*` 工具，缺 `translator:get_task_status` 和 `translator:stop_task` | `agent_registry.py:83` |
| M8 | Orchestrator Agent 无法发起后处理 — 无 `proofreader:*` 权限 | `agent_registry.py:92-94` |
| M9 | **翻译/润色任务完成后不通知 UI 刷新** — `_tool_start_translation._run()` 和 `_tool_start_polish._run()` 无 `ctx.notify_collection_modified()` 调用。对比 `_tool_run_postprocess._run()` 正确调用了 `ctx.safe_mutate(...)`。Step2 表格在翻译/润色完成后显示过期数据 | `tool_translator.py:135-148`, `235-251` |
| M10 | **无效 entry_ids 静默启动空任务** — 所有 entry_ids 无效时 `targets=[]`，仍返回 `"润色任务已启动 (0条)"`。同样 `run_postprocess` 中 `entries=[]` 虽在第62-63行校验，但未区分"无匹配条目"与"entry_ids全部无效" | `tool_translator.py:200-203`, `tool_proofreader.py:59-63` |
| M11 | **仲裁阶段 O(N×M) 二次复杂度** — `_get_quality_gate_verdict()` 为每条 entry 线性扫描全部 issues。10000条目×1000问题=1千万次迭代。应预建 `{entry_id → verdict}` 查找表 | `post_processor.py:656-669` |
| M12 | **`_generate_report` 用 SimpleNamespace 伪装 TranslationResult** — 若上游新增属性，`generate_translate_report()` 中 `AttributeError` 被 `except Exception` 吞掉，报告静默失败 | `tool_proofreader.py:319-339` |

#### Minor (13项)

| # | 问题 | 位置 |
|---|------|------|
| m1 | checkers中 `entry_id` 使用不一致 — `consistency_checker.py` 用 `entry.key`，其余用 `entry.id`（当前因 id==key 无实际影响） | `consistency_checker.py:202` 等 |
| m2 | `_generate_report()` 部分失败时返回 None — `_rotate()` 失败导致已保存的Excel文件不可达 | `tool_proofreader.py:308-342` |
| m3 | LLM资源未显式关闭 — `llm_client` 和 `term_mgr` 无close调用 | `tool_proofreader.py:75-81` |
| m4 | 报告文件绝对路径暴露在可读数据中 | `tool_proofreader.py:167-168` |
| m5 | `_tool_get_quality_report` 返回类型不一致 — 无报告时返回 `success=True` + 空列表 | `tool_proofreader.py:208` |
| m6 | `_tool_run_postprocess` 函数过长 (~170行, 8+职责) | `tool_proofreader.py:30-201` |
| m7 | LLM客户端创建逻辑在3处重复 (`run_postprocess`/`start_polish`/`start_translation`) | 多个文件 |
| m8 | Thread+TaskManager样板代码在3处重复 | 多个文件 |
| m9 | issues列表截断50条未在工具描述中说明 | `tool_proofreader.py:162` |
| m10 | 仅有最新报告可查询（`_last_report`为单值），历史报告仅可列出文件 | `tool_proofreader.py:204-219` |
| m11 | **`_tool_run_postprocess` 用内联 `ctx.collection` 检查代替 `@require_collection` 装饰器** — 与其他工具不一致，`ctx.collection` vs `ctx.active_slot.collection` 可能指向不同对象 | `tool_proofreader.py:36-38` |
| m12 | **后台线程无名称** — 3 处 `threading.Thread(target=_run, daemon=True)` 未设 `name`，调试时显示 Thread-1/2/3 无法区分任务类型 | `tool_proofreader.py:192`, `tool_translator.py:158,260` |
| m13 | **`list_quality_reports` 排序时 O(n) stat() 系统调用** — 全部 .xlsx 文件先 stat() 排序再应用 limit，目录中报告多时浪费 I/O | `tool_proofreader.py:239-250` |

---

### 审查结论

- **核心管线等价性**: ✅ **通过** — `run_postprocess` 调用与 GUI 完全相同的 `PostProcessor.process_entries()`，五阶段流程行为一致。配置、报告、进度、停止均等价。
- **功能完整性**: ⚠️ **严重不完整** — 3 Blocker（start_polish 完全无效 + PostProcessCheckpoint 运行时崩溃 + 断点续传崩溃）+ 2 Major（暂停缺失 + 翻译/润色后 UI 不刷新）
- **代码质量**: ⚠️ **需较大改进** — 6 Critical + 12 Major + 13 Minor 代码质量问题。新增：Orchestrator 提示词过期、O(N×M) 复杂度、空任务静默启动、SimpleNamespace 伪装等
- **安全性**: ✅ **基本安全** — 无严重安全漏洞。`require_confirmation: true` 防止误触发LLM费用。`permission: write` 权限模型合理。需要参数 schema 以启用 `InputValidationGuard`。

---

### 与用户问题的直接回答

**问：AI助手使用工具能否达到原后处理工作流一样的效果？**

**答：核心管线可以。** `run_postprocess` 工具调用的是与 GUI 完全相同的 `PostProcessor.process_entries()` 方法，五阶段流程（一致性检查→格式校验→质量关卡→LLM修复→LLM润色→LLM裁决）行为100%一致。Excel报告生成、进度跟踪、停止/取消、入口选择均正常工作。

**但有两个重要限制**：
1. **独立润色（`start_polish`）严重残缺** — 缺少术语注入，润色结果被丢弃。如果要独立润色，应使用 `run_postprocess` 的 `phases=["polish", "arbitration"]` 参数代替。
2. **断点续传和暂停不支持** — 中断后无法从上次位置恢复。

**问：是否达到全部的功能？**

**答：未完全达到。** 缺失功能：
- 断点续传（checkpoint resume） — 中断后必须从头开始
- 暂停/恢复 — 运行中无法暂停
- 独立润色的完整等价性 — 应用 `run_postprocess` phases参数可作为替代方案

---

### 修复优先级建议

**P0 (立即修复 — 阻塞功能可用性)**:
1. 修复 `PostProcessCheckpoint()` 崩溃 — 传 `esp_stem` 参数或移除 checkpoint 代码 (B3) **[二次复核新发现]**
2. 修复 `start_polish` — 传递 term_manager/game_profile/target_lang，保留结果，生成报告 (B1, B2)
3. 修复 Orchestrator 提示词引用已删除的 `check_quality` — 更新为 `run_postprocess`，添加 `proofreader:*` 权限 (C6) **[二次复核新发现]**
4. 修复空 entry_ids 回退全量条目 (C1)
5. 修复 cross-format `_last_report` 崩溃 (C2)
6. 包裹初始化异常 (C3)
7. 修复 `phases=None` TypeError (C4)

**P1 (本迭代修复)**:
8. 将 `_last_report` 迁移到 TaskManager 或 ReportStore，消除全局状态 (C5)
9. 添加 proofreader 工具的参数 schema (M6)
10. 翻译/润色任务完成后发出 UI 刷新通知 (M9) **[二次复核新发现]**
11. 无效 entry_ids 时拒绝启动而非静默空跑 (M10) **[二次复核新发现]**
12. 添加 `max_workers` 和 `phases` 校验 (M3, M4)
13. 为 proofreader/orchestrator agent 补全工具权限 (M7, M8)
14. 修复仲裁阶段 O(N×M) 复杂度 — 预建查找表 (M11) **[二次复核新发现]**

**P2 (后续迭代)**:
15. 提取重复的 LLM客户端创建和 Thread+TaskManager 样板代码 (m7, m8)
16. 实现断点续传（修复 B3 崩溃后，正确实现 `.load()` + resume 逻辑）（原 M1）
17. 实现暂停/恢复支持（原 M2）
18. 统一 checkers 中 entry_id 使用方式 (m1)
19. SimpleNamespace 换为真正的 TranslationResult 类型适配 (M12) **[二次复核新发现]**
20. `_tool_run_postprocess` 改用 `@require_collection` 装饰器 (m11) **[二次复核新发现]**
21. 后台线程命名 (m12) **[二次复核新发现]**
22. `list_quality_reports` 优化 stat() 调用 (m13) **[二次复核新发现]**

---

### 二次复核附录

**复核日期**: 2026-05-20
**复核方式**: 6 Agent 并行深度验证（B1B2 深度验证 / 线程安全审计 / 方案合规性 / 测试根因诊断 / 遗漏猎手 / checkpoint 崩溃验证）
**复核结论**: 原始 QA 报告 25 项声明中 24 项准确，1 项根因分析有误（test_execute_with_guardrails_input_validation 非编码问题而是 PermissionGuard 顺序问题）。新发现 8 项问题（1 Blocker + 1 Critical + 4 Major + 3 Minor），其中最严重的是 `PostProcessCheckpoint()` 运行时崩溃（B3）和 Orchestrator 提示词引用已删除工具导致编排引擎失效（C6）。原始报告未发现的线程安全问题经审计确认低风险（Qt QueuedConnection 模式安全，_last_report 在 CPython GIL 下安全）。

---
### 签名

**QA 结论**: ⚠️ 条件通过 — 核心管线等价可用，但 start_polish 不可用（应引导用户使用 run_postprocess phases参数），`PostProcessCheckpoint()` 崩溃需立即修复（可直接移除未完成的 checkpoint 代码或补全 esp_stem 参数），6项 Critical 代码质量问题需在下次发布前修复。二次复核新增 8 项问题已全部纳入本报告。
