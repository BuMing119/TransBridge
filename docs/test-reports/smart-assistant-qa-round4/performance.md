# Smart Assistant -- 性能审查报告

**日期**: 2026-05-13
**审查人**: QA Agent (性能维度)
**范围**: `src/transbridge/smart_assistant/` + 相关 `src/transbridge/ui/tools/smart_assistant/` + `src/transbridge/infra/`

---

## 发现的问题

### Blocker 级

| # | 问题 | 文件:行号 | 影响 | 修复建议 |
|---|------|----------|------|---------|
| - | (无) | - | - | - |

### Critical 级

| # | 问题 | 文件:行号 | 影响 | 修复建议 |
|---|------|----------|------|---------|
| C1 | **ExecutionEngine ThreadPoolExecutor 泄漏** -- `__init__` 创建 `ThreadPoolExecutor(max_workers=4)` 但无 `shutdown()` 方法。每次执行计划时创建新 Engine，旧 Engine 的 executor 线程持续存活直到 GC。且信号连接 (`all_finished` / `step_started` / `step_finished` / `step_retrying` 等) 从未断开，旧 Engine 因 Qt 信号连接保持可达，GC 无法回收。 | execution_engine.py:57, chat_widget.py:428-437 | 每执行一次计划泄漏 4 个线程 + 整个 Engine 对象。在长时间会话中累积可达数十个闲置线程。 | (1) ExecutionEngine 添加 `shutdown()` 方法调用 `self._executor.shutdown(wait=False)`；(2) `_on_plan_confirmed` 在执行前断开旧 Engine 的所有信号连接，旧 Engine 调用 `shutdown()`；(3) 或改为会话级单例，复用同一个 executor。 |
| C2 | **LLM client 每次 LLM 轮次重新创建** -- `_get_llm_client()` 每次调用创建新的 `OpenAICompatibleClient` 或 `AnthropicClient`，其内部各自创建独立的 `httpx.Client()`（HTTP 连接池）。每次 `_run_llm_round()` 和错误重试都会触发新 client 构造。 | chat_widget.py:285-292, llm_client.py:40-47, 93-100 | 每个 httpx.Client 维护独立的连接池和 SSL 上下文。反复创建/丢弃浪费内存、连接资源和 TLS 握手开销。 | 将 LLM client 缓存为实例变量，仅在 `provider` / `api_key` / `base_url` 变化时重建。初始在 `__init__` 或首次使用时创建。 |
| C3 | **ChatWorker 完成后未清理** -- `_on_llm_finished()` 和 `_on_llm_error()` 未调用 `deleteLater()` 或断开信号。Worker 的 Qt 信号 (`chunk`, `finished`, `error`) 连接仍存活，阻止 QThread 被 GC。下一次 `_run_llm_round()` 仅覆盖 `self._worker` 引用，旧 worker 泄漏。 | chat_widget.py:342-349, 391-419 | 每次 LLM 调用泄漏一个 QThread 对象 + 信号连接。长时间对话累积数十个僵尸 worker。 | 在 `_on_llm_finished` / `_on_llm_error` 末尾调用 `self._worker.deleteLater()` 并在覆盖 `self._worker` 前先断开旧 worker 所有信号。 |
| C4 | **面板关闭时信号未断开** -- `SmartAssistantPanel.closeEvent` 清理了 worker/engine/memory，但未断开 `ObservabilityCollector.token_stats_updated`、`TaskManager.task_completed` / `task_failed`、`ExecutionEngine` 各信号与 `ChatWidget` 槽的连接。 | panel.py:55-70, chat_widget.py:80, 83-86 | Qt 的 sender-receiver 引用链阻止相关对象 GC，导致 `ChatWidget` 及其内部组件长时间存留。 | 在 `closeEvent` 或 ChatWidget 的显式 dispose 方法中调用 `disconnect()`，或对所有信号使用 `Qt.ConnectionType.SingleShotConnection` 语义替代。 |
| C5 | **ExecutionEngine 运行于独立的 daemon 线程** -- `_on_plan_confirmed` 用 `threading.Thread(target=self._engine.execute, args=(steps,), daemon=True)` 额外创建线程，而 Engine 内部已有 ThreadPoolExecutor。外层线程空等 `execute()` 返回，浪费一个线程。 | chat_widget.py:436-437 | 每个计划多占用一个 OS 线程，该线程仅作等待用途，占用 ~8MB 栈内存。 | 直接在 ThreadPoolExecutor 的某个 worker 中调用 `execute()`，去掉外层 daemon thread。或直接调用 `execute()`（它内部通过 `self._executor.submit` 已经是异步的），在主线程外仅需一个提交线程。 |

### Major 级

| # | 问题 | 文件:行号 | 影响 | 修复建议 |
|---|------|----------|------|---------|
| M1 | **流式渲染时 MarkdownRenderer 每 50ms 重建完整 Widget 树** -- `MessageBubble.set_text()` 调用 `_RENDERER.render(text)` 每次重建所有 QWidget（QLabel、QTextEdit、QTableWidget 等）。50ms 定时器触发时，整个 markdown 文本被重新解析并创建全新 widget 树。 | message_bubble.py:78-96, markdown_renderer.py:351-394, chat_widget.py:47-48 | 流式输出期间每 50ms 执行一次 markdown 解析 + QWidget 创建（可能包含数十个子 widget），造成持续 UI 卡顿和大量短命 QWidget 分配。 | (1) 流式模式下关闭 markdown 渲染，仅使用 QLabel 的纯文本；(2) 在流式结束后一次性渲染 markdown；(3) 或实现增量渲染，仅追加新增文本块。 |
| M2 | **`_clear_conversation` widget 移除 O(n^2)** -- `while self._msg_layout.count() > 1: item = self._msg_layout.takeAt(0)` -- 每次 `takeAt(0)` 导致 QLayout 内部列表剩余元素向前移位。n 个 widget 的清除需要 O(n^2) 次移位。 | chat_widget.py:758-760 | 长对话清除时（数百条消息）可导致明显卡顿。 | 从末尾向前移除：`while count > 1: item = self._msg_layout.takeAt(count - 2); ...; count -= 1`；或逐一遍历删除而非每次取 index 0。 |
| M3 | **ObservabilityCollector 在主线程执行文件 I/O** -- `end_conversation()` 调用 `_save_trace()`（同步 JSON 写入）和 `_cleanup_old()`（glob + stat + unlink）。此方法在 LLM 响应完成后由主线程信号链触发。 | collector.py:64-95, chat_widget.py:374 | 大型 trace JSON 或旧文件清理时可能阻塞 UI 若干毫秒，但影响较小。累积的 .json 文件在 `_cleanup_old` 中逐个 stat 判定，文件多时耗时线性增长。 | (1) 保存操作放入 QThread 或 ThreadPoolExecutor；(2) `_cleanup_old` 限制单次扫描文件数上限；(3) 仅在面板关闭时做全量清理。 |
| M4 | **MemoryWriterThread 不必要的 0.5s 轮询唤醒** -- Writer 线程在 `while self._running` 循环中 `_cv.wait(timeout=0.5)`，即使无数据也每 0.5s 唤醒一次，执行 `_flush()`。 | memory_store.py:62-67 | 每 0.5s 唤醒线程并进行无操作 flush（元数据未变化时仍执行 JSON 序列化 + 文件写入 + FAISS 保存）。浪费 CPU 和 I/O。 | (1) 引入 dirty flag，仅在有实际变更时写入；(2) 使用 `queue.Queue` 替代 Condition，阻塞等待真实数据到达。 |
| M5 | **`_on_send` 主线程执行同步检索** -- `memory_retriever.retrieve()` 在主线程调用，内部含 FAISS 向量搜索（O(d\*n)）和潜在的 Embedding API 同步网络调用。 | chat_widget.py:696, memory_retriever.py:18-23 | Embedding 模式启用时（非 disabled），API 调用阻塞主线程可能 200-2000ms，导致 UI 冻结。FAISS 搜索在 1000 条目下 < 5ms 可接受。 | Embedding 调用放入 QThread 或后台线程，通过信号回调返回结果。FAISS 搜索保留在主线程（因其速度可接受），但 `embed()` 调用必须异步。 |
| M6 | **OutputValidationGuard 递归深拷贝结果数据** -- `_redact_dict` 创建全新的 dict（`result = {}`），`_redact_list` 创建全新 list，对嵌套结构全量复制。每次工具执行完成后触发一次。 | output_validator.py:58-92 | 对于大数据量的工具输出（如 `get_statistics` 返回数百条分类分布），复制 + 逐值正则脱敏可能耗时 5-50ms。主线程内执行累积影响 UI 响应。 | (1) 仅对有实际脱敏需求的值做就地修改（mutating），避免全量复制；(2) 或对超过阈值的大输出跳过深度脱敏，仅检查顶层字符串。 |
| M7 | **`ChatWidget.__init__` 中同步加载 LLMConfig 和 embedding client** -- 面板构造时即读取配置文件并尝试创建 embedding_client。虽然当前 `embedding_mode="disabled"`，但若未来启用，面板打开时会有初始化延迟。 | chat_widget.py:61-75 | 面板打开时间增加，对用户体验影响不大（仅在启用 embedding 时）。 | 延迟初始化 embedding_client，仅在首次 memory search 时才尝试加载。 |
| M8 | **`ContextBuilder.build()` 双重遍历 collection** -- 第一次遍历计算 `translated` 计数（line 30），第二次遍历统计分类分布（line 35-42）。每个 entry 被访问两次。 | context_builder.py:30-42 | 对于 10K+ 条目集合，两次遍历耗时约 2-5ms（每条目的 context 字符串拆分操作很小）。影响轻微。 | 合并为单次遍历：一次性统计 translated count + 分类分布。 |

### Minor 级

| # | 问题 | 文件:行号 | 影响 | 修复建议 |
|---|------|----------|------|---------|
| m1 | **LRU 使用 `list.remove()` O(n)** -- `_update_lru` 中 `self._access_order.remove(memory_id)` 需要线性扫描列表。`_evict_lru` 用 `pop(0)` 也是 O(n)。 | memory_store.py:195-206 | 1000 条记忆时每次访问约 500 次比较操作。影响微小。 | 使用 `collections.OrderedDict` 或双链表实现 O(1) LRU。 |
| m2 | **`get_messages()` 每次返回完整副本** -- `self._messages.copy()` 在每次 LLM 调用中至少使用 2 次（system prompt 构建 + 实际发送）。 | conversation_manager.py:45-46 | 长对话（数百条消息，每条约 1KB）时，每次拷贝约 200KB 内存分配。GC 压力增大，但对整体性能影响有限。 | 返回内部列表引用并提供不可变视图，或仅在消息实际变更时通知调用方重新获取。 |
| m3 | **Token 估算使用 chars/3** -- 对输入和输出均用字符数/3 估算 token 数。 | chat_worker.py:42-44 | 中文每字约 1.5-2 token，英文每词约 1-2 token。chars/3 对于英文低估，对中文高估。观测面板 Token 统计不准确。 | 迁移到 tiktoken 或模型特定的 tokenizer 进行精确计数，或标注"估算"并说明误差范围。 |
| m4 | **流式气泡在错误时未清理** -- `_on_llm_error` 停止 timer 和重置文本状态，但 `self._streaming_bubble` 未从 layout 中移除也未 `deleteLater()`。 | chat_widget.py:391-394 | 错误后 UI 残留一个显示部分文本（或 "..."] ) 的气泡。用户体验问题多于性能问题。 | 添加与 `_on_send` 中断相同的清理逻辑：查找 index，removeWidget + deleteLater，置 None。 |
| m5 | **`_on_scroll_changed` 每次像素变化都触发** -- scrollbar `valueChanged` 信号连接到此槽，在其中调用 `btn.move()` 定位按钮。 | chat_widget.py:786-802 | 快速滚动时每秒触发数百次。`move()` 本身开销极低，但频繁调用 signal/slot 机制有微小开销。 | 可添加节流（如仅在值变化超 10px 或超 100ms 时才更新按钮位置）。 |
| m6 | **ChatWorker max_tokens 硬编码为 2048** -- 所有 LLM 调用统一使用此值。 | chat_widget.py:320 | 对于简单对话浪费了响应空间，对于复杂任务（大量工具调用）则可能不足。无法从配置调整。 | 从 `LLMConfig` 读取 `max_tokens` 配置项，允许用户自定义。 |
| m7 | **`_eval_ast_node` 递归无深度限制** -- 虽条件表达式通常很浅，但恶意构造的嵌套条件可能导致递归栈溢出。 | execution_engine.py:362-441 | 实际使用场景极低风险（条件由内部系统生成，非用户输入）。 | 添加最大递归深度检查（如 50 层），超限时返回默认值。 |
| m8 | **Tool schema 构建遍历所有 namespace** -- `build_tool_schema_for_prompt` 在 `namespace=None` 时遍历所有 namespace 下的所有工具 dict。dict 合并用 `update()` 为 O(total_tools)。 | tool_registry.py:65-79 | 当前工具数约 20，影响可忽略。但随工具增长会线性增加 schema 构建时间。 | 缓存 schema 字符串，在工具注册/注销时使其失效。 |
| m9 | **`ConversationManager._trim()` 用 `del self._messages[idx]`** -- 在 `sorted(removed_indices, reverse=True)` 循环中逐个删除。每个 `del` 导致后续元素向前移位，为 O(n\*k)。 | conversation_manager.py:93-95 | 通常仅裁剪少量轮次，且消息量有限（< 1000），实际影响极小。 | 使用列表推导式重建 messages 列表而非逐个删除。 |
| m10 | **`_tool_start_translation` 内部同步读取 LLMConfig** -- 后台线程启动函数中调用 `LLMConfig.load_from_file()` 读取 INI。虽然在线程内不阻塞 UI，但增加了启动延迟。 | tool_translator.py:28-30 | 翻译任务启动延迟 < 5ms（INI 读取），无实际影响。 | 可在主线程预先加载配置，通过参数传入线程函数。 |
| m11 | **ObservabilityCollector `_cleanup_old` 无文件数上限** -- glob 匹配所有 JSON 文件并进行 stat 判断。若目录积累数万文件，每次清理可能耗时数百 ms。 | collector.py:85-95 | 每个会话结束时触发，通常仅几十个文件，无实际影响。 | 添加单次扫描文件数上限（如 500），分多次清理。 |
| m12 | **MarkdownRenderer 每次 render 创建空 widget** -- 当 blocks 为空列表时，仍创建 QWidget + QVBoxLayout + QLabel("")。 | markdown_renderer.py:359-366 | 每条空消息多创建 3 个 QObject。影响微小。 | 返回缓存的空 widget 单例。 |

---

## 资源生命周期矩阵

### 线程资源

| 资源 | 创建点 | 清理点 | 是否泄漏 | 备注 |
|------|--------|--------|----------|------|
| `ChatWorker` (QThread) | `chat_widget.py:320` | `_on_send:680-682`, `_clear_conversation:744-747` | **泄漏** | `_on_llm_finished`/`_on_llm_error` 未清理。信号未断开。 |
| `ExecutionEngine._executor` (ThreadPoolExecutor 4 workers) | `execution_engine.py:57` | 无显式 shutdown | **泄漏** | Engine 无 shutdown 方法。信号连接保持 Engine 存活。 |
| `execution_engine.execute()` 外层 daemon thread | `chat_widget.py:436` | daemon=True，进程退出时终止 | 否（但浪费） | 每次计划执行多创建一个冗余线程。 |
| `MemoryWriterThread` (QThread) | `memory_store.py:130-135` | `memory_store.py:188-190` (`close()`) → `panel.py:63` | 否 | 生命周期正确管理。 |
| `AgentWorker` (QThread) | 按需由调用方创建 | 线程自然结束 | 潜在 | 无 `deleteLater()`，依赖 Qt GC。 |
| `TaskManager` 内部翻译/润色线程 | `tool_translator.py:113`, `tool_translator.py:168` | `TaskManager.cleanup()` / `reset()` → `panel.py:67` | 否 | daemon=True，且有 join 超时。 |

### QWidget / QObject 资源

| 资源 | 创建点 | 清理点 | 是否泄漏 | 备注 |
|------|--------|--------|----------|------|
| `MessageBubble` | `chat_widget.py:249-256` | `_clear_conversation:758-760` | 否 | 通过 `deleteLater()` 清理。 |
| `MessageBubble._content` (MarkdownRenderer 输出) | `message_bubble.py:63` | `set_text:91-93` | 否 | 每次更新旧 widget 被 `deleteLater()`。 |
| `MarkdownRenderer` 输出 widget 树 | `markdown_renderer.py:368-380` | MessageBubble 清理时级联 | 潜在 | 流式期间每秒 20 次重建，大量短命 widget。 |
| `ToolCard` / `PlanCard` / `BatchToolCard` | `chat_widget.py:257-275` | `_clear_conversation` | 否 | 信号连接在销毁时自动断开。 |
| 流式气泡 `self._streaming_bubble` | `chat_widget.py:315` | `_on_send:684-689`, `_on_llm_finished:345-348` | **泄漏** (错误路径) | `_on_llm_error` 未清理。 |
| 观测面板 Tab 内 QLabel / QTableWidget / QListWidget | `chat_widget.py:166-184` | ChatWidget 销毁时级联 | 否 | 父级 widget 树正确。 |
| 重试按钮 (`_on_llm_error`) | `chat_widget.py:414` | `_clear_conversation` | 是 | 失败后残留，`_on_retry` 触发后未清理自身。 |

### 信号连接

| 连接 | 创建点 | 断开点 | 是否泄漏 | 备注 |
|------|--------|--------|----------|------|
| `ChatWorker.chunk` / `finished` / `error` / `token_usage` | `chat_widget.py:321-324` | 无显式断开 | **泄漏** | 旧 worker 信号保持连接。 |
| `ExecutionEngine.all_finished` / `step_started` / `step_finished` / `step_retrying` / `step_requires_confirmation` | `chat_widget.py:429-433` | 无显式断开 | **泄漏** | 旧 Engine 因此保持存活。 |
| `TaskManager.task_completed` / `task_failed` | `chat_widget.py:85-86` | 无显式断开 | **泄漏** | 跨会话保持。 |
| `ObservabilityCollector.token_stats_updated` | `chat_widget.py:80` | 无显式断开 | **泄漏** | 跨会话保持。 |
| `QSettings` 读写 | `chat_widget.py:54`, `chat_widget.py:652` | 不需要 | 否 | 无连接，仅方法调用。 |
| `QuickActionsChips.action_clicked` / `skill_triggered` | `chat_widget.py:129-130` | `_clear_conversation` 中 widget 销毁时自动断开 | 否 | Qt 父子关系正确。 |
| `_back_to_bottom_btn.clicked` | `chat_widget.py:116` | widget 销毁时 | 否 | - |

### 定时器

| 资源 | 创建点 | 停止点 | 是否泄漏 | 备注 |
|------|--------|--------|----------|------|
| `_streaming_timer` (50ms) | `chat_widget.py:47` | `_on_send:679`, `_on_llm_finished:344`, `_on_llm_error:392`, `_flush_streaming:336` | 否 | 多路径停止，覆盖良好。 |
| MemoryWriterThread 内部 Condition wait (500ms) | `memory_store.py:65` | `memory_store.py:88-90` (`stop()`) | 否 | 但在 stop 之前频繁唤醒。 |

### 大对象 / 缓存

| 资源 | 生命周期 | 清理机制 | 是否泄漏 | 备注 |
|------|----------|----------|----------|------|
| `_prompt_builder` (PromptBuilder) | 延迟初始化，随 ChatWidget 存活 | ChatWidget 销毁时 | 否 | chat_widget.py:279-283 |
| `_middlewares` (护栏链) | 延迟初始化 + 缓存 | ChatWidget 销毁时 | 否 | chat_widget.py:504-527 |
| `_uploaded_docs` | 上传文件时填充，`_clear_conversation` 清空 | 手动清空 | 否 | chat_widget.py:764 |
| `_conversation._messages` | 整个对话生命周期 | `_clear_conversation` 清空 | 否 | conversation_manager.py:49 |
| `_memory_store` (MemoryStore + WriterThread) | ChatWidget 创建时，`close()` 停止 | `panel.py:63` | 否 | memory_store.py:188-190 |
| `_memory_retriever` (MemoryRetriever) | ChatWidget 创建时 | ChatWidget 销毁时 | 否 | 无特殊清理需求 |
| `_obs_collector` (ObservabilityCollector) | ChatWidget 创建时 | 无显式清理 | 潜在 | 未调用 `end_conversation()`，活跃 trace 可能丢失。 |
| `_engine._executor` 线程池 | 每次计划创建 Engine 时 | 无 shutdown | **泄漏** | 如上 C1。 |
| `_midlewares` (护栏链) | `_ensure_middlewares()` 首次调用 | ChatWidget 销毁时 | 否 | 缓存后复用。 |

---

## 性能维度评分

**总分**: 30 / 60

### 扣分明细

| 维度 | 满分 | 得分 | 扣分原因 |
|------|------|------|----------|
| 时间复杂度 | 10 | 6 | M2: O(n^2) widget 清除 (扣2)；M4: 不必要的轮询唤醒 (扣1)；M8: 双重遍历 collection (扣1) |
| 内存管理 | 10 | 6 | M6: 递归深拷贝 (扣2)；M1: 流式渲染大量短命 QWidget (扣1)；M12: 空消息创建多余 widget (扣1) |
| 线程管理 | 10 | 3 | C1: ThreadPoolExecutor 泄漏 + 信号未断开 (扣3)；C3: Worker 未清理 (扣2)；C4: 面板关闭信号未断开 (扣1)；C5: 冗余 daemon 线程 (扣1) |
| UI 响应性 | 10 | 7 | M1/M5: 流式渲染 + 主线程检索 (扣2)；M3: I/O 在主线程 (扣1) |
| 资源清理 | 10 | 4 | C1+C3+C4: 线程/信号/Engine 多项泄漏 (扣4)；m4: 错误路径未清理气泡 (扣1)；面板关闭信号未断开 (扣1) |
| Token 预算 | 5 | 3 | m3: Token 估算不准确 (扣1)；m6: max_tokens 硬编码 (扣1) |
| I/O 效率 | 5 | 3 | M4: 不必要的定期 flush (扣1)；M3: 主线程文件 I/O (扣1) |
| 缓存策略 | 5 | 4 | m1: LRU 未用 OrderedDict (扣0.5)；C2: LLM client 未缓存 (扣0.5) |

### 总结

本模块在核心架构上采用了合理的设计模式（ThreadPoolExecutor 复用、LRU 淘汰、条件变量替代忙等、流式渲染节流），但存在以下系统性缺陷：

1. **信号连接清理缺失** -- 贯穿 ExecutionEngine、ChatWorker、TaskManager、ObservabilityCollector。每次计划执行后信号不断累积，导致旧对象无法 GC，线程资源泄漏。这是最大的性能风险来源。

2. **LLM client 重复创建** -- 每次 LLM 轮次创建新的 HTTP 客户端，浪费连接池和 TLS 会话。高频使用场景下（多轮 ReAct 循环）此开销尤为显著。

3. **流式渲染效率** -- 50ms 定时器触发完整 markdown 重新解析和 widget 重建，而非增量追加。在模型快速输出 token 时产生大量短命 QWidget，给 Qt 事件循环和 GC 带来压力。

4. **资源生命周期不完整** -- ExecutionEngine 无 shutdown、ChatWorker 错误路径无清理、Panel closeEvent 信号清理不彻底。需要在架构层面固定资源创建-销毁配对模式。

**优先修复建议**：先解决 C1（ThreadPoolExecutor 泄漏）+ C3/C4（信号清理），这三项修复简单、影响面最大。其次解决 C2（LLM client 缓存）和 M1（流式渲染优化），可显著提升用户体验。Minor 级问题可在后续迭代中逐步优化。
