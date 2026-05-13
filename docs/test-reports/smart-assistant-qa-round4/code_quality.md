# Smart Assistant -- 代码质量审查报告

**日期**: 2026-05-13
**审查人**: QA Agent (代码质量维度)
**审查范围**: `src/transbridge/smart_assistant/` (48源文件) + `src/transbridge/ui/tools/smart_assistant/` (7源文件) + `src/transbridge/infra/markdown_renderer.py` + `src/transbridge/config/llm.py` + `tests/` (8测试文件)

---

## 发现的问题

### Blocker 级

| # | 问题 | 文件:行号 | 详情 | 修复建议 |
|---|------|----------|------|---------|
| B1 | GraphExecutor ABC 孤立--全代码库零引用 | `graph_executor.py:7-22` | ADR-011 规划 `ExecutionEngine` 应继承 `GraphExecutor`（发行为 `StatefulDAGExecutor(GraphExecutor)`），但实际上 `ExecutionEngine` 类声明为 `ExecutionEngine(QObject)` 而非 `ExecutionEngine(GraphExecutor, QObject)`。全代码库无任何文件 import `graph_executor`，`__init__.py` 也**未**导出它。此 ABC 是死接口，违反 ADR-011 设计契约。 | 方案 A: 让 `ExecutionEngine` 继承 `GraphExecutor` 并实现抽象方法（约 300 行增量），按 ADR-011 实现。方案 B: 如不再维护此契约则删除 `graph_executor.py`，从 ADR 文档中移除相关引用。 |
| B2 | SkillExecutor 反向依赖--backend 直接引用 UI widget | `skills/skill_executor.py:9,17,26-27` | `SkillExecutor.__init__` 接收 `chat_widget`（UI 层的 `ChatWidget` 实例），并直接访问其私有方法 `self._chat._on_send()` 和 `self._chat.set_input()`。这违反 ADR-008 UI/backend 分层。后端 `skill_executor` 不应知道 UI widget 的 API。 | 将 `SkillExecutor` 改为信号驱动：定义抽象接口或回调函数，由 `ChatWidget` 在构造时注入。移除对 `ChatWidget` 私有方法的直接调用。 |

### Critical 级

| # | 问题 | 文件:行号 | 详情 | 修复建议 |
|---|------|----------|------|---------|
| C1 | 上传操作静默吞异常--单条目失败被忽略 | `tools/tool_paratranz.py:89-90` | `_tool_upload_entries` 中逐个上传条目时，`except Exception: pass` 完全吞掉了单条上传失败的错误。用户将收到 "已上传 N/M 条" 的误导性成功消息，无法得知哪些条目失败。 | 改为收集失败条目信息：`failed.append({"key": e.key, "error": str(exc)})`，在最终 `ToolResult` 中以 `failed_items` / `warnings` 返回。 |
| C2 | 关键路径 checkpoint 写入静默失败 | `execution_engine.py:314-315` | `execute_graph` 核心循环中 `_save_checkpoint` 被 `except Exception: pass` 包裹，checkpoint 写入失败时完全静默，导致长时间运行的任务无法从中断点恢复。 | 至少记录 `logger.warning`，考虑设置重试或通知上层调用方。 |
| C3 | `paratranz_parser.py` 重复打开 ZIP 文件 | `file_parser/paratranz_parser.py:37,45` | `_parse_zip` 方法中，用 `with zipfile.ZipFile(path, "r") as zf:` 打开一次解包内容后，又在 line 45 用 `zipfile.ZipFile(path, "r").namelist()` **再次打开同一文件**生成 metadata。同一文件被打开两次，且 line 45 未使用 context manager。 | 在第一次 `with` 块内保存 `zf.namelist()` 到变量，复用于 metadata。 |
| C4 | 多个 `except Exception:` 无日志记录 | 全模块 16 处 | 多处使用 `except Exception: pass` 或仅 `except Exception:` 后不记录日志（共 16 处）。关键位置包括：`chat_worker.py:47-48`(token估算失败)、`chat_worker.py:61-62`(cancel失败)、`panel.py:68-69`(TaskManager reset失败)、`chat_widget.py:56-57,73-74,388-389,511-513,653-654,665-666`、`output_validator.py:44-45`(JSON序列化失败)、`memory_store.py:127-128`(vector_store加载失败)。 | 分级处理：对于 `cancel()` 和 token 估算这类非关键操作允许静默，但需添加注释说明原因。对于 checkpoint、TaskManager reset、serialization/deserialization 失败，至少添加 `logger.warning/debug`。 |
| C5 | `_CancelledByStop` 继承 `BaseException` 非 `Exception` | `chat_worker.py:6-7` | `_CancelledByStop` 继承 `BaseException` 以穿透 `except Exception` 拦截，这是正确的设计模式。但 `run()` 的 top-level `except (_CancelledByStop,): pass` 完全吞掉取消信号，若内部有逻辑错误被误标记为 `_CancelledByStop` 将无法排查。 | 低风险。建议在取消路径添加 `logger.debug` 记录取消时刻。 |

### Major 级

| # | 问题 | 文件:行号 | 详情 | 修复建议 |
|---|------|----------|------|---------|
| M1 | `_run_single` 上帝方法 (123行) | `execution_engine.py:67-190` | 单个方法同时负责：工具查找、before 中间件链、confirm 循环等待、Reflexion 重试循环、after 中间件链。圈复杂度极高（5 层嵌套），几乎不可能单元测试。 | 拆分为：`_resolve_tool()`, `_run_before_guards()`, `_await_decision()`, `_run_retry_loop()`, `_run_after_guards()`，每个方法 <40 行。 |
| M2 | `execute_graph` 上帝方法 (125行) | `execution_engine.py:194-319` | BFS 遍历、节点 dispatch、条件路由、LoopNode 循环、HITL confirm、checkpoint 自动保存耦合在一起。`_dispatch` 内联函数包含 5 种节点类型的分支逻辑（~60行）。 | 拆分为：`_bfs_iterate()`, `_dispatch_node()` (提取内部 _dispatch 函数), `_collect_next_level()`, `_auto_checkpoint()`。 |
| M3 | `ChatWidget.__init__` 上帝方法 (216行) | `chat_widget.py:24-240` | 构造函数中完成：字体设置、ConversationManager/ExecutionEngine/Worker 初始化、流式 timer、自动模式 QSettings、MemoryStore/MemoryRetriever/EmbeddingClient/ObservabilityCollector/TaskManager 全部初始化、ScrollArea 布局、观测面板 Tab、输入框、按钮行等。 | 提取私有工厂方法：`_init_core_services()`, `_init_streaming()`, `_init_memory()`, `_init_observability()`, `_init_ui_layout()`, `_init_input_area()`。 |
| M4 | `LLMConfig.load_from_file` 过长 (80行) | `config/llm.py:163-243` | 包含 llm、embedding、后处理、guardrails、mcp 五个section的逐字段读取，每个字段一行 setattr + config.get/getint/getboolean。高度重复的样板代码。 | 使用配置描述表（field_list）驱动读取：`[("llm", "provider", str, "provider"), ...]`，用 for 循环替代重复代码，减少 50% 行数。 |
| M5 | `LLMConfig.save_to_file` 过长 (74行) | `config/llm.py:87-161` | 与 load 镜像的 write 样板代码。每个字段一行 `c.set("llm", ...)`。 | 使用与 load 相同的配置描述表驱动 write，或使用 `dataclasses.asdict()` 批量写入。 |
| M6 | `_eval_ast_node` 过长 (79行) | `execution_engine.py:362-441` | 同时处理 7 种 AST 节点类型的求值：Constant、Name、Attribute、Subscript、Compare、BoolOp、UnaryOp、Call。方法虽分层但规模过大。 | 按节点类型拆分为独立私有方法：`_eval_constant()`, `_eval_name()`, `_eval_attribute()`, `_eval_compare()`, `_eval_boolop()`, `_eval_call()`。 |
| M7 | 大量懒导入散布于函数体内 | 全模块 ~25 处 | 工具函数内部使用 `from src.transbridge... import X` 延迟导入模式，虽有避免循环依赖的意图，但导致：(a) 导入开销在热路径上，(b) IDE 无法静态分析依赖，(c) ImportError 延迟到运行时才暴露。 | 对已知稳定无循环依赖的模块（如 `translator`, `writer`, `ai_translator`, `post_processor`）应改为模块顶部的标准 import。保留对 LLM 配置和可选依赖的懒导入。 |
| M8 | `_tool_get_translation_config` 调用链过长 (238行 )  | `tools/tool_translator.py:250-314` | 单个函数中完成了：LLM 配置读取、后处理配置构建、术语数据库搜索与 JSON 文件读取、ParaTranz 配置检查、profile 列表构建。职责过于宽泛。 | 拆分为独立的数据获取函数：`_get_post_process_config()`, `_get_term_db_info()`, `_get_paratranz_status()`，主函数仅负责组装。 |
| M9 | `_tool_set_stage` 全量循环无批处理 | `tools/tool_editor.py:204-234` | 批量设置 stage 时对每个 entry_id 逐条 `collection.get()` + `entry.stage = int(stage)`，大数据量时性能堪忧。 | 无明显优化空间（collection 不支持 bulk update），但应记录性能警告或考虑在 collection 层提供 `bulk_set_stage()` 方法。 |
| M10 | `_tool_upload_entries` 全量同步上传无批处理 | `tools/tool_paratranz.py:84-91` | 逐条 API 调用上传，大数据量（>100条）将产生 N 次 HTTP round-trip。 | 考虑使用 ParaTranz 的批量上传端点（如可用），或引入异步并发上传。 |

### Minor 级

| # | 问题 | 文件:行号 | 详情 | 修复建议 |
|---|------|----------|------|---------|
| m1 | `_ToolRegistry` 类名以下划线开头但被导出为 `ToolRegistry` | `tool_registry.py:20-79` | `_ToolRegistry` → `ToolRegistry = _ToolRegistry` 的别名模式不够直观。命名约定 `_` 前缀通常表示"模块私有"，但此类显然是公共接口。 | 直接将类命名为 `ToolRegistry`，如有向后兼容顾虑则保留别名。 |
| m2 | `ConversationManager` 方法缺少类型标注 | `conversation_manager.py:11,15,20,24,27,36,45,48,51,56` | 所有方法的参数和返回值均无类型标注（例如 `def __init__(self, max_turns: int = 20)` 参数无类型）。 | 添加完整的类型标注：`self._messages: list[dict[str, str]]`、`def add_user(self, content: str) -> None:` 等。 |
| m3 | `Orchestrator.decompose_task` ctx 参数无类型 | `orchestrator.py:26` | `def decompose_task(self, user_request: str, ctx) -> list[Subtask]:` -- `ctx` 缺少类型 `Any` 或具体类型。 | 添加 `ctx: Any` 并用注释说明期望的接口/属性。 |
| m4 | `ContextBuilder.__init__` ctx 参数无类型 | `context_builder.py:11` | `def __init__(self, ctx=None):` -- `ctx` 缺少类型标注 | 添加 `ctx: Any | None = None` 或具体的 AppContext 类型引用（TYPE_CHECKING）。 |
| m5 | `SkillExecutor` 的 `execute` 方法缺少返回值标注 | `skills/skill_executor.py:12` | 签名 `def execute(self, spec: SkillSpec) -> None:` 给出了返回值类型，但方法体中通过 `self._chat._on_send()` 调用 UI 触发异步操作，此返回值标注并未体现异步特性。 | 方法签名合理，但应在 docstring 中说明这是异步触发的操作。 |
| m6 | `_run_postprocess_phase` 中 `type('Cfg', (), {})()` 动态类创建 | `tools/tool_proofreader.py:31` | 使用 `type('Cfg', (), {})()` 即时创建一个空配置对象作为 fallback。此模式晦涩难懂且无 docstring 说明。 | 定义一个显式的 `EmptyConfig` 类或使用 `SimpleNamespace()` 替代 `type('Cfg', (), {})()`。 |
| m7 | `Subtask` 中 `getattr(st, 'tool_name', '')` 无意义 | `orchestrator.py:84` | `tool_name = getattr(st, 'action', '') or getattr(st, 'tool_name', '')` -- `Subtask` dataclass 中**不存在** `tool_name` 属性，第二个 `getattr` 永远返回 `''`。这是死代码。 | 删除 `or getattr(st, 'tool_name', '')` 部分。 |
| m8 | `MemoryWriterThread` 缺少 docstring | `memory/memory_store.py:42` | `MemoryWriterThread` 类无 docstring，仅在 `__init__.py` header 中有高层说明。 | 添加类级 docstring 说明 `MemoryWriterThread` 的职责、生命周期、`enqueue()` 和 `_flush()` 的线程模型。 |
| m9 | 多个工具文件重复相似的注册模式 | `tools/tool_*.py` 各 `_register_*_tools()` 函数 | 6 个工具模块中的 `_register_*_tools()` 函数包含高度相似的把 (name, display_name, description, execute, permission) 元组循环注册到 `ToolRegistry.register()` 的模式。 | 抽取 `_register_tools_batch(tools: list[tuple], namespace: str, param_schemas: dict)` 公共函数，消除重复。 |
| m10 | `BinaryFileParser._parse_pdf/_parse_docx` 在 parse() 内导入依赖 | `file_parser/binary_parser.py:19,38` | `pdfplumber` 和 `python-docx` 在函数体内导入并在 ImportError 时提供友好的错误消息。这是好的模式，但 `import pdfplumber` 的 `try/except ImportError` 未在模块顶部统一处理。 | 保持现有模式，但可考虑在 `BinaryFileParser.__init__` 中检查可选依赖并缓存可用性。 |
| m11 | `MemoryStore._flush` 使用 `os.replace` 可能导致部分写入 | `memory/memory_store.py:80-82` | 使用 tmp 文件 + `os.replace()` 进行原子写入，但 `json.dump` 可能在中途失败（如磁盘满），留下不完整的 tmp 文件。`os.replace` 是原子的，但错误处理不足。 | 在 `except Exception` 块中清理 `tmp_meta` 文件。 |
| m12 | `MessageBubble._STYLES` 中 `padding: 0px` 与 `inner_layout.setContentsMargins(12, 8, 12, 8)` 不一致 | `message_bubble.py:12-29:61` | stylesheet 中 `BubbleInner` 设置 `padding: 0px`，但实际内层布局使用 `setContentsMargins(12, 8, 12, 8)`。style 的 padding 设置被忽略，留下迷惑性代码。 | 统一样式：要么用 stylesheet 设置 padding，要么删除 stylesheet 中的 padding 设置。 |
| m13 | `TestExecutionEngine` 4 个测试用例被 `@unittest.skip` | `tests/test_execution_engine.py:59,78,87,102` | `test_execute_linear_graph`, `test_execute_single_node`, `test_execute_results_order`, `test_cancel_stops_execution` 全部 skip，注释为"需要完整的 Qt + ToolRegistry 运行时环境"。核心 Graph 引擎功能实际未测试。 | 为 ExecutionEngine 的图编排功能创建集成测试基础设施（mock ToolRegistry + QApplication fixture），取消 skip。 |

---

## 架构合规检查

### ADR-008: UI/Backend 分层

| 合规状态 | 违规项 |
|---------|--------|
| **不合规** (1项) | `skills/skill_executor.py:9` -- `SkillExecutor.__init__` 接收 `ChatWidget`（UI类）作为依赖，并直接调用其私有方法 `_on_send()`、`set_input()`。违反 "backend 不可直接依赖 UI widget" 原则。 |

说明：其他 PyQt6 信号使用（`ExecutionEngine(QObject)`、`ChatWorker(QThread)`、`TaskManager(QObject)`）属于跨层通信基础设施，符合 PyQt 推荐的信号/槽模式，不视为违规。

### ADR-009: 工具接口一致性

| 合规状态 | 违规项 |
|---------|--------|
| **合规** (0项) | 所有工具函数遵循统一的 `(args: dict, ctx) -> ToolResult` 签名，通过 `ToolRegistry.register()` 注册。 |

### ADR-010: Embedding 三模式

| 合规状态 | 违规项 |
|---------|--------|
| **合规** (0项) | `MemoryStore.__init__` 正确处理 `embedding_mode: "api" | "local" | "disabled"` 三模式。 |

### ADR-011: Graph 编排引擎 (GraphExecutor ABC)

| 合规状态 | 违规项 |
|---------|--------|
| **严重违规** (1项) | `graph_executor.py:7-22` -- `GraphExecutor` ABC 已定义但 `ExecutionEngine` **未继承**它。ADR-011 明确要求 `StatefulDAGExecutor(GraphExecutor)` 或 `ExecutionEngine` 继承 `GraphExecutor`。实际代码中 `ExecutionEngine` 继承 `QObject` 而非 `GraphExecutor`。此外，`__init__.py` 未导出 `GraphExecutor`（虽然导出了 `GraphSpec` 等相关类型）。详见 Blocker B1。 |

### ADR-012: 文件解析器接口

| 合规状态 | 违规项 |
|---------|--------|
| **合规** (0项) | `FileParser` ABC 定义了 `supported_extensions`、`parse()`、`can_handle()` 和 `get_parser()` classmethod。所有子类（`TextFileParser`, `BinaryFileParser`, `ParatranzParser`）正确实现。 |

---

## 死代码清单

| # | 死代码项 | 文件:行号 | 为何是死的 |
|---|---------|----------|-----------|
| DC1 | `GraphExecutor` ABC (整文件) | `graph_executor.py:1-23` | 全代码库零导入。`ExecutionEngine` 未继承此 ABC。`__init__.py` 未导出。仅在 ADR 文档中被引用。 |
| DC2 | `getattr(st, 'tool_name', '')` 分支 | `orchestrator.py:84` | `Subtask` dataclass 只有 `task_id, agent_type, action, input_data, depends_on` 字段，无 `tool_name` 属性。第二个 `getattr` 恒返回 `''`。 |
| DC3 | CSS `padding: 0px` 被覆盖 | `message_bubble.py:17,25` | `BubbleInner` stylsheet 设置 `padding: 0px` 但 `inner_layout.setContentsMargins(12, 8, 12, 8)` 在代码中覆盖了 stylesheet padding，且 Qt stylesheet padding 与 layout margin 是不同的渲染层，此设置既无效果也无被引用价值。 |

---

## 上帝方法清单 (>80行)

| # | 方法 | 文件:行号 | 行数 | 建议拆分方式 |
|---|------|----------|------|-------------|
| GM1 | `ExecutionEngine._run_single` | `execution_engine.py:67-190` | 123 | 拆为: `_resolve_tool()` → `_run_before_guards()` → `_await_decision()` → `_run_retry_loop()` → `_run_after_guards()` |
| GM2 | `ExecutionEngine.execute_graph` | `execution_engine.py:194-319` | 125 | 拆为: `_bfs_iterate()` / `_dispatch_node()`(内联函数提升) / `_collect_next_level()` / `_auto_checkpoint()` |
| GM3 | `ChatWidget.__init__` | `chat_widget.py:24-240` | 216 | 拆为: `_init_core_services()`, `_init_streaming()`, `_init_memory()`, `_init_observability()`, `_init_ui_layout()`, `_init_input_area()` |
| GM4 | `LLMConfig.load_from_file` | `config/llm.py:163-243` | 80 | 使用配置描述表驱动读取: `fields = [("llm", "provider", str), ...]` |
| GM5 | `LLMConfig.save_to_file` | `config/llm.py:87-161` | 74 | 使用配置描述表驱动写入，与 load 共享字段定义 |

---

## 异常处理审计

### 静默 `except Exception: pass` (无日志)

| # | 文件:行号 | 上下文 | 风险 |
|---|----------|--------|------|
| 1 | `chat_worker.py:47-48` | Token 估算失败 | 低: 仅是显示用估算值 |
| 2 | `chat_worker.py:61-62` | LLM client cancel() 失败 | 中: 可能导致连接泄漏 |
| 3 | `execution_engine.py:314-315` | checkpoint 保存失败 | **高**: 长时间任务不可恢复 |
| 4 | `observability/collector.py:94-95` | 旧追踪文件删除失败 | 低: 仅是清理操作 |
| 5 | `memory_retriever.py:23-24` | Embedding 失败降级 | 低: 有意降级到精确匹配 |
| 6 | `memory_store.py:127-128` | VectorStore 加载失败重新创建 | 低: 有注释说明 fallback |
| 7 | `tools/tool_paratranz.py:89-90` | 单条目上传失败 | **高**: 数据丢失不可见 |
| 8 | `guardrails/output_validator.py:44-45` | JSON 序列化截断失败 | 中: 输出可能未截断 |
| 9 | `panel.py:68-69` | TaskManager.reset() 失败 | 中: 会话间任务泄漏 |
| 10 | `chat_widget.py:56-57` | QSettings 读取失败 | 低: 使用默认值 |
| 11 | `chat_widget.py:73-74` | Embedding client 创建失败 | 低: 降级到 disabled |
| 12 | `chat_widget.py:388-389` | Memory 记录失败 | 低: 非关键功能 |
| 13 | `chat_widget.py:511-513` | LLMConfig 加载失败构建 guard chain | 低: fallback 链存在 |
| 14 | `chat_widget.py:653-654` | QSettings 写入失败 | 低: 非关键功能 |
| 15 | `chat_widget.py:665-666` | worker wait 超时后异常 | 低: 已有 warning |

### 宽泛 `except Exception` 返回默认/fallback

| # | 文件:行号 | 上下文 | 评价 |
|---|----------|--------|------|
| 16 | `execution_engine.py:359-360` | AST 条件求值异常 → 返回 False | 合理: 安全保守 |
| 17 | `execution_engine.py:477-478` | checkpoint 加载失败 → 返回 None | 合理: 视为重新开始 |
| 18 | `tools/tool_translator.py:52-54` | LLM 配置读取失败 → 返回 fail | 合理: 有错误信息 |
| 19 | `tools/tool_translator.py:255-256` | LLM 配置读取失败 → 创建默认 | 合理: 返回默认状态 |
| 20 | `tools/tool_translator.py:336-337` | LLM 配置读取失败 → 创建默认 | 合理: 不阻碍写入 |

---

## 代码重复

| # | 问题 | 涉及文件 | 详情 |
|---|------|---------|------|
| DR1 | 路径遍历 + 绝对路径检测逻辑重复 | `tools/tool_parser.py:23-27` 与 `tools/tool_writer.py:15-18` | `_validate_path` 和 `_validate_output_path` 都检测 `".." in path` 和 `os.path.isabs(path)`。虽然两者有不同职责（parser 还检查文件存在和扩展名白名单），但路径安全检测逻辑重复。 |
| DR2 | `_validate_output_path` 被外部模块引用 | `tools/tool_v1.py:102,131` | `_tool_export_json` 和 `_tool_write_back` 从 `tool_writer` import `_validate_output_path`（私有函数名以下划线开头但跨模块使用）。 |
| DR3 | 工具注册模式高度重复 | 6个 `tools/tool_*.py` 文件的 `_register_*_tools()` | 每个模块都有 `tools = [(name, display_name, description, execute, permission), ...]` + 循环 `ToolRegistry.register(ToolSpec(...))` 模式。可提取公共函数。 |
| DR4 | `_get_profiles` 函数内联 import | `tools/tool_translator.py:235-247` | 使用 `configparser` + `get_config_file_path()` 的模式与其他 config 读取重复，但因为是独立于 `LLMConfig` 读取 `[llm_profiles]` 节，部分合理。 |
| DR5 | 后处理工厂函数 `_run_postprocess_phase` 中的 `get_default_config` 模式 | `tools/tool_proofreader.py:18-49` | `processor_class.get_default_config()` 检查在每次调用中执行，说明 processor_class 接口不一致。 |

---

## 测试质量评估

| 测试文件 | 用例数 | 可加载 | 核心覆盖 | 备注 |
|---------|--------|--------|---------|------|
| `test_context_builder.py` | 8 | 是 | ContextBuilder 空集合/正常集合/上传文件/分类分布/依赖注入 | 覆盖全面 |
| `test_conversation_manager.py` | 7 | 是 | 基本操作/裁剪逻辑/observation/plan_result/清空 | 覆盖全面 |
| `test_observability.py` | 7 | 是 | 会话生命周期/token统计/持久化/清理/工具调用/重试 | 覆盖好 |
| `test_memory.py` | 9 | 是 | CRUD/LRU淘汰/搜索/异步写入 | 覆盖好 |
| `test_mcp.py` | 9 | 是 | 认证/工具列表/deprecated工具/prompt schema/namespace | 覆盖全面 |
| `test_chat_worker.py` | 6 | 是 | 流式响应/错误/取消/空消息 | 需 Qt QApplication |
| `test_execution_engine.py` | 14 (4 skip) | 是 | 条件求值/重试/暂停/decision/middleware/executor | **4 个核心 Graph 执行测试 skip** |
| `test_markdown_renderer.py` | 13 | 是 | Tokenize 层（不依赖 QApplication）/ Render 层 | 覆盖好，分层测试 |
| `test_agent_tool_integration.py` | 45+ | 是 | 完整工作流链路/标签系统/安全护栏/翻译配置/状态查询/ParserWriter/ToolResult v2/ExecutionContext/filter_entries/TaskManager/装饰器/Agent注册 | **最全面的测试文件** |

**总体评估**: 测试覆盖良好，`test_agent_tool_integration.py` 是最强的集成测试。主要问题是 `test_execution_engine.py` 的 4 个 Graph 引擎核心测试被 skip。

---

## 魔术数字清单

| # | 文件:行号 | 值 | 上下文 | 建议 |
|---|----------|-----|--------|------|
| 1 | `execution_engine.py:32` | `_MAX_WORKERS = 4` | ThreadPool 并发数 | 已是类常量，合理 |
| 2 | `execution_engine.py:109` | `timeout = 300.0` | confirm 等待超时 | 提升为类常量 `_CONFIRM_TIMEOUT` |
| 3 | `chat_worker.py:18` | `max_tokens: int = 2048` | LLM max_tokens | 合理默认值 |
| 4 | `conversation_manager.py:11` | `max_turns: int = 20` | 对话轮次 | 合理默认值 |
| 5 | `conversation_manager.py:29,39` | `2000` | observation/plan_result 截断 | 提升为类常量 `_MAX_OBSERVATION_CHARS` |
| 6 | `memory_store.py:105` | `MAX_ENTRIES_DEFAULT = 1000` | 最大记忆条目 | 已是类常量，合理 |
| 7 | `observability/collector.py:86` | `max_age_days: int = 30` | 追踪清理年龄 | 提升为类常量 `_MAX_TRACE_AGE_DAYS` |
| 8 | `input_validator.py:40` | `_MAX_INPUT_SIZE = 102400` | 输入大小限制 | 已是模块常量，合理 |
| 9 | `output_validator.py:22` | `_DEFAULT_MAX_OUTPUT = 102400` | 输出大小限制 | 已是模块常量，合理 |
| 10 | `output_validator.py:21` | `_MAX_MESSAGE_LEN = 10240` | 消息大小限制 | 已是模块常量，合理 |
| 11 | `chat_widget.py:22` | `_MAX_REACT_DEPTH = 10` | ReAct 循环深度 | 已是类常量，合理 |
| 12 | `chat_widget.py:48` | `self._streaming_timer.setInterval(50)` | 流式刷新间隔 | 提升为类常量 `_STREAMING_FLUSH_MS` |
| 13 | `message_bubble.py:57` | `inner.setMaximumWidth(420)` | 气泡最大宽度 | 提升为类常量 |
| 14 | `skill_executor.py` 中硬编码 `"Skill: {spec.name}"` 前缀字符串 | 多处 | Skill 执行前缀 | 已是固定格式，可接受 |

---

## 代码质量维度评分

| 维度 | 满分 | 得分 | 扣分原因 |
|------|------|------|---------|
| 架构合规 | 10 | **4** | B1: GraphExecutor ABC 孤立 (ADR-011 违规, -4); B2: SkillExecutor 反向依赖 (ADR-008 违规, -2) |
| 死代码 | 5 | **3** | DC1: GraphExecutor 整文件死代码 (-1); DC2: orchestraator 无效 getattr (-1) |
| 代码重复 | 5 | **3** | DR1-DR5: 路径校验/工具注册/后处理模式重复 (-2) |
| 命名一致性 | 5 | **4** | m1: `_ToolRegistry` 命名 (-1) |
| 类型安全 | 5 | **2** | m2-m4: ConversationManager/Orchestrator/ContextBuilder 缺少类型标注 (-2); 工具函数 `ctx` 参数普遍无类型 (-1) |
| 错误处理 | 10 | **4** | C1: 上传操作静默吞异常 (-2); C2: checkpoint 静默失败 (-1); C4: 16处 except:pass 无日志 (-2); C3: 重复打开ZIP (-1) |
| 圈复杂度 | 10 | **3** | GM1: _run_single 123行 (-2); GM2: execute_graph 125行 (-2); GM3: ChatWidget.__init__ 216行 (-2); M9: M10 额外方法过长 (-1) |
| 文档与注释 | 5 | **3** | m8: MemoryWriterThread 缺少 docstring (-1); m5: SkillExecutor 缺少异步行为说明 (-1) |
| 依赖方向 | 5 | **4** | B2: SkillExecutor 的反向依赖 (-1) |
| 接口契约 | 5 | **3** | B1: GraphExecutor ABC 未被实现 (-2) |
| 测试覆盖 | 5 | **3** | test_execution_engine.py 4 个核心 Graph 测试 skip (-1); 缺少 guards/permission/output_validator 单元测试 (-1) |
| 魔术数字 | 5 | **3** | 多个硬编码值（#2, #5, #7, #12, #13）应提取 (-2) |
| **总计** | **75 → 60** | **39** | 换算公式: `60 * (39/75) = 31.2` |

**最终评分: 31 / 60**

### 评分区间参考

- 50-60: 优秀，代码质量高，维护性好
- 35-49: 良好，存在需要改进的问题但整体可控
- 20-34: **警戒线**，存在影响可维护性的系统性问题
- 0-19: 不合格，需要重大重构

### 核心问题摘要

1. **架构层面**: `GraphExecutor` ABC 孤立（ADR-011 核心契约未实现）和 `SkillExecutor` 的反向依赖是最严重的问题。两者都影响可扩展性和可维护性。
2. **错误处理**: 16 处 `except Exception: pass` 中有 3 处高风险（上传操作、checkpoint、cancel），需要立即修复。
3. **代码规模**: 5 个上帝方法合计 >600 行，是代码腐化的主要源头，建议优先拆分 `ChatWidget.__init__` 和 `execution_engine.py` 的两个方法。
4. **测试缺口**: Graph 引擎核心功能的测试全部 skip，安全护栏模块缺少独立单元测试。
