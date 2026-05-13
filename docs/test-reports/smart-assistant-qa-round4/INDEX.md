# Smart Assistant 第四轮 QA 全面审查 — 汇总报告

**日期**: 2026-05-13
**审查方式**: 4 维度并行独立审查（功能/安全/性能/代码质量）→ 汇总去重
**审查原则**: 全新独立审查，未参考任何历史报告
**审查范围**: ~56 源文件 + 10 测试文件，跨 4 个 Plan

---

## 综合评分

| 维度 | 评分 | 审查人 |
|------|------|--------|
| **功能正确性** | 35 / 60 | QA Agent (功能维度) |
| **安全性** | 48 / 60 | QA Agent (安全维度) |
| **性能** | 30 / 60 | QA Agent (性能维度) |
| **代码质量** | 31 / 60 | QA Agent (代码质量维度) |
| **综合平均** | **36 / 60** | |

---

## 问题总览

| 严重级别 | 数量 | 说明 |
|---------|------|------|
| **Blocker** | 4 | 2 功能崩溃 + 2 架构严重违规 |
| **Critical** | 15 | 核心功能缺陷/安全短板/线程泄漏/静默异常吞没 |
| **Major** | 28 | 重要功能缺陷/性能问题/代码规模超标 |
| **Minor** | 40 | 命名/类型/文档/硬编码/资源清理不彻底 |

---

## Blocker 级问题

### BR1. `ContextBuilder.build()` 被当作静态方法调用 — 系统提示词构建崩溃

**发现维度**: 功能 (B1)
**文件**: `ui/tools/smart_assistant/chat_widget.py:305`

`ContextBuilder.build(self._ctx)` 将 `AppContext` 实例作为 `self` 传入 `build()` 实例方法。`build()` 内部执行 `ctx = ctx or self._ctx` 时，`self` 实际是 AppContext（没有 `_ctx` 属性），必然抛出 `AttributeError`。**所有 LLM 对话的系统提示词构建彻底中断。**

**修复**: 改为 `ContextBuilder(self._ctx).build()` 或 `ContextBuilder().build(self._ctx)`

---

### BR2. `_pending_memory_context` 在赋值前使用 — 首次对话崩溃

**发现维度**: 功能 (B2)
**文件**: `ui/tools/smart_assistant/chat_widget.py:308`

`_run_llm_round()` 中 `if self._pending_memory_context:` 直接访问该属性，但 `__init__` 从未初始化。`_pending_memory_context` 仅在 `_on_send()` 第 694 行被赋值，首次对话必然触发 `AttributeError`。

**修复**: 在 `__init__` 中添加 `self._pending_memory_context = ""`

---

### BR3. `GraphExecutor` ABC 孤立 — ADR-011 核心契约未实现

**发现维度**: 代码质量 (B1)
**文件**: `smart_assistant/graph_executor.py:1-23`

ADR-011 规划 `ExecutionEngine` 应继承 `GraphExecutor`（`StatefulDAGExecutor(GraphExecutor)`），但实际 `ExecutionEngine` 仅继承 `QObject`。`GraphExecutor` ABC 全代码库零引用，`__init__.py` 也未导出。这个死抽象类伪装为正式接口契约。

**修复**: 方案 A — 让 `ExecutionEngine` 继承 `GraphExecutor`；方案 B — 删除 `graph_executor.py` 并更新 ADR

---

### BR4. `SkillExecutor` 反向依赖 UI 层 — 违反 ADR-008

**发现维度**: 代码质量 (B2)
**文件**: `skills/skill_executor.py:9,17,26-27`

`SkillExecutor.__init__` 接收 `ChatWidget`（UI 类）作为依赖，并直接调用其私有方法 `_on_send()` 和 `set_input()`。违反 ADR-008 "backend 不可直接依赖 UI widget" 原则。

**修复**: 改为信号驱动或回调接口，移除对 `ChatWidget` 私有方法的直接调用

---

## Critical 级问题

| # | 问题 | 文件:行号 | 发现维度 | 修复建议 |
|---|------|----------|---------|---------|
| CR1 | **RetryHandler 无 llm_client → Reflexion 自纠错完全失效** — `RetryHandler()` 未传入 `llm_client`，`analyze_and_adjust()` 第 24 行直接 `return None`，所有工具执行失败后均无 LLM 分析重试 | `execution_engine.py:42` | 功能 | 传入 LLM client 实例 |
| CR2 | **Orchestrator 将人类可读描述误用作工具名** — `tool_name = getattr(st, 'action', '')` 取到 "翻译DLC1条目" 这样的描述文本，不是有效工具名 | `orchestrator.py:84-86` | 功能 | LLM prompt 中要求返回 `tool_name` 字段 |
| CR3 | **ReAct 单步模式 admin/write 确认无 UI 交互** — `execute_with_guardrails` 中 PermissionGuard 返回拒绝后仅返回失败文本，不触发确认弹窗 | `chat_widget.py:549-551` | 功能 | 增加确认回调参数 |
| CR4 | **MCP Server 默认无认证运行** — `auth_token` 默认空字符串，MCP 启用后任何本地 stdio 进程均可调用已暴露工具 | `config/llm.py:79-83`, `mcp/server.py:55-56` | 安全 | 强制要求配置 token 才允许启动 MCP |
| CR5 | **API Key 明文存储** — LLM/Embedding API Key 以明文写入 `data/paratranz_config.ini` | `config/llm.py:96,118` | 安全 | 使用 OS 密钥链或 AES 加密 |
| CR6 | **ExecutionEngine ThreadPoolExecutor 泄漏** — `__init__` 创建 executor 但无 `shutdown()` 方法，旧 Engine 因信号连接保持可达无法 GC | `execution_engine.py:57` | 性能 | 添加 `shutdown()` 方法，信号断开 |
| CR7 | **LLM client 每次轮次重新创建** — `_get_llm_client()` 每次创建新的 `httpx.Client`，浪费连接池和 TLS 会话 | `chat_widget.py:285-292` | 性能 | 缓存为实例变量，仅配置变更时重建 |
| CR8 | **ChatWorker 完成后未清理** — `_on_llm_finished/error` 未调用 `deleteLater()` 或断开信号，旧 worker 泄漏 | `chat_widget.py:342-349, 391-419` | 性能 | 完成后调用 `deleteLater()` + 断开信号 |
| CR9 | **面板关闭时信号未断开** — `ObservabilityCollector`/`TaskManager`/`ExecutionEngine` 信号连接未断开，阻止 GC | `panel.py:55-70` | 性能 | 在 `closeEvent` 中显式 `disconnect()` |
| CR10 | **上传操作静默吞异常** — 逐条目上传时 `except Exception: pass` 完全吞掉单条失败，用户收到误导性成功消息 | `tools/tool_paratranz.py:89-90` | 代码质量 | 收集失败条目信息在 ToolResult 中返回 |
| CR11 | **Checkpoint 写入静默失败** — `execute_graph` 核心循环中 `_save_checkpoint` 被 `except Exception: pass` 包裹 | `execution_engine.py:314-315` | 代码质量 | 至少记录 `logger.warning` |
| CR12 | **ParatranzParser 重复打开 ZIP** — `_parse_zip` 中同一文件打开两次，第二次未使用 context manager | `file_parser/paratranz_parser.py:37,45` | 代码质量 | 第一次 with 块内保存 `namelist()` |
| CR13 | **16 处静默 `except Exception: pass`** — 全模块多处关键路径异常被完全忽略（含上传、checkpoint、cancel、observability） | 多文件（见详细报告） | 代码质量/功能 | 分级处理：关键路径至少 `logger.warning` |
| CR14 | **Test ExecutionEngine 4 个核心 Graph 测试全部 skip** — `test_execute_linear_graph` 等核心功能无法验证 | `tests/test_execution_engine.py:59,78,87,102` | 代码质量 | 创建集成测试基础设施，取消 skip |
| CR15 | **Redundant daemon thread per plan execution** — 每次计划执行额外创建 `threading.Thread` 空等 `execute()` 返回 | `chat_widget.py:436-437` | 性能 | 直接在 ThreadPoolExecutor worker 中调用 |

---

## Major 级问题（去重后 28 项）

### 功能 (8 项)
| # | 问题 | 文件:行号 |
|---|------|----------|
| M1 | `execute_with_guardrails` 在 raw_result 为 dict 时跳过 after 护栏链（脱敏/截断） | `tools/base.py:225-231` |
| M2 | `ToolRegistry.list_all()` 包含 deprecated 工具，MCP 通道仍可发现和调用 | `tool_registry.py:42-52` |
| M3 | ConversationManager 裁剪逻辑依赖严格消息顺序，异常消息顺序导致裁剪错位 | `conversation_manager.py:61-95` |
| M4 | `_tool_start_translation` 闭包捕获可变引用，collection 切换可能导致读到切换后数据 | `tools/tool_translator.py:67-117` |
| M5 | `_on_plan_all_finished` 不检查 ReAct 深度，Plan 执行后可能超限 | `chat_widget.py:444-454` |
| M6 | ObservabilityCollector `input_summary` 语义错误，实际记录的是输出数据 | `observability/collector.py:44` |
| M7 | `_uploaded_docs` 直接写入 AppContext 实例属性，违反封装 | `chat_widget.py:304` |
| M8 | PermissionGuard 确认逻辑在 `execute_with_guardrails` 中不触发 UI 弹窗 | `guardrails/permission.py:25-32` |

### 安全 (5 项)
| # | 问题 | 文件:行号 |
|---|------|----------|
| M9 | Markdown 渲染器未校验链接协议，`javascript:`/`data:` URL 可执行 | `infra/markdown_renderer.py:49` |
| M10 | InputValidationGuard 路径遍历 key 白名单仅 9 个硬编码参数名，新工具可能绕过 | `guardrails/input_validator.py:66-69` |
| M11 | MCP `admin_tool_whitelist` 实质无效，admin 工具即使被 whitelist 也被 PermissionGuard 阻断 | `mcp/adapter.py:52-58` |
| M12 | 上传文件无大小限制，超大文件可导致 OOM | `chat_widget.py:717-740` |
| M13 | OutputValidationGuard 强制 data 为 dict，非 dict 非 None 即拒绝 | `guardrails/output_validator.py:33-34` |

### 性能 (8 项)
| # | 问题 | 文件:行号 |
|---|------|----------|
| M14 | 流式渲染每 50ms 重建完整 Widget 树（MarkdownRenderer 重新解析+创建 QWidget） | `message_bubble.py:78-96`, `markdown_renderer.py:351-394` |
| M15 | `_clear_conversation` widget 移除 O(n^2)，每次 `takeAt(0)` 导致内部列表移位 | `chat_widget.py:758-760` |
| M16 | ObservabilityCollector 在主线程执行同步文件 I/O | `collector.py:64-95` |
| M17 | MemoryWriterThread 0.5s 不必要的定期唤醒，即使无数据也 flush | `memory_store.py:62-67` |
| M18 | `_on_send` 主线程执行同步检索（FAISS + 潜在 Embedding API 网络调用） | `chat_widget.py:696` |
| M19 | OutputValidationGuard 递归深拷贝结果数据，大数据量下耗时 | `output_validator.py:58-92` |
| M20 | Embedding client 在 ChatWidget 构造时同步加载（若未来启用会有初始化延迟） | `chat_widget.py:61-75` |
| M21 | ContextBuilder.build() 双重遍历 collection（translated 计数 + 分类分布） | `context_builder.py:30-42` |

### 代码质量 (7 项)
| # | 问题 | 文件:行号 |
|---|------|----------|
| M22 | `_run_single` 上帝方法 (123行)，圈复杂度极高 | `execution_engine.py:67-190` |
| M23 | `execute_graph` 上帝方法 (125行)，BFS/调度/checkpoint 耦合 | `execution_engine.py:194-319` |
| M24 | `ChatWidget.__init__` 上帝方法 (216行)，构造函数中始化全部子系统 | `chat_widget.py:24-240` |
| M25 | `LLMConfig.load_from_file` 样板代码 (80行) + `save_to_file` (74行) | `config/llm.py:87-243` |
| M26 | 大量懒导入散布于函数体内 (~25处)，ImportError 延迟暴露 | 全模块 |
| M27 | `_tool_get_translation_config` 调用链过长 (238行)，职责过宽 | `tools/tool_translator.py:250-314` |
| M28 | 6 个工具模块注册模式高度重复（`_register_*_tools()` 样板代码） | `tools/tool_*.py` |

---

## Minor 级问题（去重后 40 项，按类别分组）

### 资源泄露 / UI 残留
| # | 问题 | 文件:行号 | 发现维度 |
|---|------|----------|---------|
| m1 | 流式气泡在错误时未清理（`_on_llm_error` 未 removeWidget/deleteLater） | `chat_widget.py:391-394` | 性能 |
| m2 | 重试按钮可叠加，多次错误堆积多个"重试"按钮 | `chat_widget.py:414-416` | 功能/性能 |
| m3 | `_on_scroll_changed` 每次像素变化都触发，无节流 | `chat_widget.py:786-802` | 性能 |
| m4 | LRU 使用 `list.remove()` O(n) + `pop(0)` O(n) | `memory_store.py:195-206` | 性能 |
| m5 | `get_messages()` 每次返回完整副本 O(n) 内存分配 | `conversation_manager.py:45-46` | 性能 |
| m6 | ConversationManager._trim() 逐个 `del` 导致 O(n*k) | `conversation_manager.py:93-95` | 性能 |
| m7 | `_eval_ast_node` 递归无深度限制，恶意嵌套可能栈溢出 | `execution_engine.py:362-441` | 性能 |
| m8 | MarkdownRenderer 每次 render 空消息创建 3 个 QObject | `markdown_renderer.py:359-366` | 性能 |
| m9 | `ObservabilityCollector._cleanup_old` 无文件数上限 | `collector.py:85-95` | 性能 |
| m10 | MCP Server stdin 读取无长度限制，超大消息可 OOM | `mcp/server.py:23` | 安全 |
| m11 | `ParatranzParser._parse_zip` 重复打开 zip 文件 | `file_parser/paratranz_parser.py:37,45` | 安全 |

### 配置 / 硬编码
| # | 问题 | 文件:行号 | 发现维度 |
|---|------|----------|---------|
| m12 | ChatWorker max_tokens 硬编码为 2048 | `chat_widget.py:320` | 性能 |
| m13 | Token 估算使用 chars/3 不准确 | `chat_worker.py:42-44` | 性能 |
| m14 | `_MAX_WORKERS=4` / `timeout=300.0` / `2000` 截断等硬编码 | 多文件 | 代码质量 |
| m15 | 流式刷新间隔 50ms 硬编码 | `chat_widget.py:48` | 性能/代码质量 |

### 类型 / 命名 / 文档
| # | 问题 | 文件:行号 | 发现维度 |
|---|------|----------|---------|
| m16 | `_ToolRegistry` 类名以下划线开头但被导出为公共接口 | `tool_registry.py:20-79` | 代码质量 |
| m17 | ConversationManager 方法缺少类型标注 | `conversation_manager.py` | 代码质量 |
| m18 | Orchestrator.decompose_task ctx 参数无类型 | `orchestrator.py:26` | 代码质量 |
| m19 | ContextBuilder.__init__ ctx 参数无类型 | `context_builder.py:11` | 代码质量 |
| m20 | GuardMiddleware ABC 的 ctx 类型标注缺失 | `guardrails/base.py:17,21` | 功能 |
| m21 | MemoryWriterThread 缺少 docstring | `memory/memory_store.py:42` | 代码质量 |
| m22 | SkillExecutor execute 方法异步特性未在 docstring 说明 | `skills/skill_executor.py:12` | 代码质量 |
| m23 | `_run_postprocess_phase` 使用 `type('Cfg', (), {})()` 晦涩模式 | `tools/tool_proofreader.py:31` | 代码质量 |
| m24 | CSS `padding: 0px` 与实际 `setContentsMargins(12,8,12,8)` 不一致 | `message_bubble.py:12-29` | 代码质量 |

### 代码重复 / 功能缺陷
| # | 问题 | 文件:行号 | 发现维度 |
|---|------|----------|---------|
| m25 | `_tool_get_scope_preview` 未复用 `filter_entries` 公共函数 | `tools/tool_translator.py:378-397` | 功能 |
| m26 | MessageBubble 模块级预实例化 `_RENDERER`，依赖 QApplication 导入顺序 | `message_bubble.py:6` | 功能 |
| m27 | `build_system_prompt` namespace=None 返回全部工具 schema 而非元工具描述 | `prompts.py:76-79` | 功能 |
| m28 | `_tool_list_labels` 未区分"未初始化"和"空标签库" | `tools/tool_editor.py:239-250` | 功能 |
| m29 | MemoryRetriever 依赖不存在的 `memory/embedding.py` 模块 | `memory/memory_retriever.py:12` | 功能 |
| m30 | ToolCard.set_result 后按钮禁用但不触发消息气泡更新 | `tool_card.py:72-77` | 功能 |
| m31 | `_tool_export_json` 默认路径未校验 (若 esp_path 含 `../`) | `tools/tool_v1.py:99-103` | 功能 |
| m32 | ExecutionEngine `_safe_serialize` 对 Qt 对象调用 `str()` 泄露内存地址 | `execution_engine.py:494` | 功能 |
| m33 | `Subtask` 中 `getattr(st, 'tool_name', '')` 恒返回 `''` — 死代码 | `orchestrator.py:84` | 代码质量 |
| m34 | 路径校验逻辑在 `tool_parser.py` 和 `tool_writer.py` 重复 | 2 文件 | 代码质量 |
| m35 | MemoryStore._flush 异常时 tmp_meta 文件清理缺失 | `memory_store.py:80-82` | 代码质量 |

### 安全 / 深度防御
| # | 问题 | 文件:行号 | 发现维度 |
|---|------|----------|---------|
| m36 | MCP auth_token 非恒定时间比较（时序攻击面） | `mcp/server.py:60` | 安全 |
| m37 | MemoryStore 会话内容明文 JSON 存储 | `memory/memory_store.py:73-82` | 安全 |
| m38 | 错误消息可能泄露文件路径 | `chat_widget.py:738`, `agent_worker.py:60` | 安全 |
| m39 | Skill prompt_template 无长度限制（恶意 TOML 可注入超长 prompt） | `skills/skill_loader.py:56` | 安全 |
| m40 | System prompt 工具 schema 暴露完整参数信息给外部 LLM | `prompts.py:78-79` | 安全 |

---

## 架构合规矩阵

| ADR | 合规状态 | 违规项 |
|-----|---------|--------|
| **ADR-008** (UI/Backend 分层) | ⚠ 不合规 | BR4: `SkillExecutor` 反向依赖 `ChatWidget` UI 类 |
| **ADR-009** (工具接口) | ✅ 合规 | 所有工具遵循统一签名 `(args, ctx) -> ToolResult` |
| **ADR-010** (Embedding 三模式) | ✅ 合规 | api/local/disabled 三模式正确处理 |
| **ADR-011** (Graph 编排引擎) | ❌ 严重违规 | BR3: `GraphExecutor` ABC 未被 `ExecutionEngine` 继承 |
| **ADR-012** (文件解析器) | ✅ 合规 | `FileParser` ABC + 子类实现正确 |

---

## 护栏覆盖率矩阵

| 执行路径 | PermissionGuard | InputValidationGuard | OutputValidationGuard | 工具级路径校验 |
|----------|:---:|:---:|:---:|:---:|
| GUI ReAct 模式 | ✅ | ✅(1) | ✅(1) | ✅ |
| GUI Plan 模式 | ✅ | ✅ | ✅ | ✅ |
| GUI Auto 模式 | ✅(3) | ✅(1) | ✅(1) | ✅ |
| Agent Worker | ✅ | ✅ | ✅ | ✅ |
| MCP 通道 | ✅(4) | ✅ | ✅ | ✅ |
| ExecutionEngine Graph | ✅ | ✅ | ✅ | ✅ |

**注释**:
1. GUI 通道中 Input/OutputValidationGuard 取决于用户配置是否启用。PermissionGuard 始终生效
3. Auto 模式对 admin/require_confirmation 工具回退到手动确认卡片
4. MCP 通道中 admin 工具即使在 whitelist 中也无法执行（HITL 确认不可用）

---

## Bottom Line

**综合评分 36/60，需要立即修复 2 个致命 Bug 才能使面板恢复正常工作。**

### 立即修复（Blocker）
1. **BR1** — `ContextBuilder.build(self._ctx)` → `ContextBuilder(self._ctx).build()` — 一行修复
2. **BR2** — `__init__` 添加 `self._pending_memory_context = ""` — 一行修复
3. **BR3** — 决定 `GraphExecutor` ABC 的去留
4. **BR4** — 移除 `SkillExecutor` 对 `ChatWidget` 的直接依赖

### 高优先级（Critical — 本周）
- CR1: RetryHandler 传入 llm_client
- CR6-CR9: 信号清理 + 线程泄漏修复（影响面最大）
- CR10: 上传操作错误处理

### 各维度详细报告
- [功能正确性审查报告](functional.md)
- [安全审查报告](security.md)
- [性能审查报告](performance.md)
- [代码质量审查报告](code_quality.md)

---

**QA 审查结论**: ⚠ **需修复 — 4 Blocker + 15 Critical + 28 Major + 40 Minor**

**审查签字**: QA 4 维度并行审查组
**审查日期**: 2026-05-13
