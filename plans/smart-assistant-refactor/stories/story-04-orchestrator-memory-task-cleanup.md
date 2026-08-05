# Story 04: 剩余模块精简收尾

**所属方案**: `plans/smart-assistant-refactor/plan.md`
**技术模块**: backend
**状态**: 已实现
**创建日期**: 2026-05-22

## 前置依赖

### 上游 Story
- 无（Story 01 仅提供 ConditionEvaluator/CheckpointManager，本 Story 不依赖它们）
- 建议在 Story 02（base.py 类型分离）之后执行，仅因 TaskManager 精简涉及 `ToolResult` 类型引用

### 跨 Plan 依赖
- 无

### 引用的架构决策
- ADR-008 (2026-05-22 更新节): 文件≤450行、类≤22方法、模块级函数≤3
- ADR-009: 无冲突
- ADR-012: 护栏体系不变

## 验收标准

- [ ] `conversation_orchestrator.py`: LLM 客户端创建逻辑提取为模块级 `_create_llm_client()` 函数（~60行），从 `_get_llm_client()` 中委托调用
- [ ] `conversation_orchestrator.py`: 去除 `_get_prompt_builder()` 中与 `prompts.build_system_prompt()` 重复的内联逻辑
- [ ] `memory/memory_writer.py` 存在，包含 `MemoryWriterThread` 类（~42行）
- [ ] `memory/memory_store.py` 顶部 `from .memory_writer import MemoryWriterThread` 重导出
- [ ] `tools/task_manager.py`: `on_completed`+`on_failed` → `on_finished`; `notify_completed`+`notify_failed` → `notify_finished`。旧方法保留为 deprecated wrapper（兼容 chat_widget.py 的外部调用）
- [ ] `tools/task_manager.py`: `set_main_thread_dispatcher` / `reset_dispatcher` / `get_handle` **保留不动**（chat_widget.py:712 和 tool_proofreader.py:129 有活跃调用方）
- [ ] `tools/task_manager.py`: 移除仅内部分发用的死代码（`_default_dispatcher` 模块级函数，如无调用方可删除）
- [ ] `tools/task_manager.py`: 所有公开 API 签名不变
- [ ] 现有测试全部通过

## 数据流

本 Story 包含 3 个独立子任务，互不依赖：

```
子任务 A: conversation_orchestrator.py 精简
  ConversationOrchestrator.init()
    │
    ├── _create_llm_client(config)    ← 新提取的模块级函数
    │     ├── 计算缓存键 (api_key + provider + base_url + model)
    │     ├── 检查配置 mtime
    │     └── 创建/返回缓存的 LLM 客户端
    │
    ├── _get_llm_client()             ← 精简后委托给 _create_llm_client
    ├── _get_prompt_builder()         ← 去除内联 prompt 构建逻辑
    └── start_round()                 ← 不变

子任务 B: memory/memory_writer.py 外提
  memory/memory_store.py             memory/memory_writer.py
    ├── MemoryEntry                  └── MemoryWriterThread (QThread)
    ├── MemoryStore                      │── run()
    └── from .memory_writer import       └── stop()
         MemoryWriterThread  ←──────

子任务 C: tools/task_manager.py 精简
  TaskManager (单例)
    ├── 保留: set_main_thread_dispatcher, reset_dispatcher, get_handle（有活跃调用方）
    ├── 合并: on_completed + on_failed → on_finished (旧方法保留为 deprecated wrapper)
    └── 合并: notify_completed + notify_failed → notify_finished (旧方法保留为 deprecated wrapper)
```

## 关键接口

### conversation_orchestrator.py

```python
# 新提取的模块级函数
def _create_llm_client(config: LLMConfig) -> LLMClient:
    """创建或返回缓存的 LLM 客户端。
    缓存键: (api_key, provider, base_url, model) 的哈希
    自动检测配置文件 mtime 变化并重建缓存"""

class ConversationOrchestrator:
    # react_depth 和 auto_mode 经代码验证无重复定义，无需修复
    ...

### memory/memory_writer.py

```python
class MemoryWriterThread(threading.Thread):
    """后台异步写入线程（从 memory_store.py 提取，不改代码）

    实际构造器（5 参数，保持完全不变）:
    """

    def __init__(self, storage_dir, metadata_path, index_path,
                 get_metadata_cb, get_vector_store_cb):
        super().__init__(daemon=True)
        self._storage_dir = storage_dir
        self._metadata_path = metadata_path
        self._index_path = index_path
        self._get_metadata_cb = get_metadata_cb
        self._get_vector_store_cb = get_vector_store_cb
        self._queue = queue.Queue()
        self._stop_event = threading.Event()

    def run(self) -> None: ...
    def stop(self) -> None: ...
```

### tools/task_manager.py

```python
class TaskManager:
    # 保留不动（活跃调用方: chat_widget.py:712, tool_proofreader.py:129）
    def set_main_thread_dispatcher(self, dispatcher): ...
    def reset_dispatcher(self): ...
    def get_handle(self, task_id: str) -> TaskHandle | None: ...

    # 新增合并方法
    def on_finished(self, callback: Callable) -> None:
        """注册任务完成/失败回调（合并原 on_completed + on_failed）"""

    def notify_finished(self, task_id: str, success: bool, message: str = "",
                        data: dict | None = None) -> None:
        """通知任务完成（合并原 notify_completed + notify_failed）"""

    # 保留为 deprecated wrapper
    def on_completed(self, callback): ...
    def on_failed(self, callback): ...
    def notify_completed(self, task_id, result): ...
    def notify_failed(self, task_id, error): ...

    # 不变的公开 API
    def register(self, ...) -> str: ...
    def cancel(self, task_id: str) -> bool: ...
    def pause(self, task_id: str) -> bool: ...
    def resume(self, task_id: str) -> bool: ...
    def get_status(self, task_id: str) -> dict: ...
    def list_active(self) -> list[str]: ...
    def list_all(self) -> list[dict]: ...
    def reset(self) -> None: ...
```

## 实现步骤

### 子任务 A: conversation_orchestrator.py 精简

**涉及文件**: `src/transbridge/smart_assistant/conversation_orchestrator.py`（修改）

#### 步骤 A1: 提取 _create_llm_client()

- 从 `_get_llm_client()` 方法中提取 LLM 客户端创建逻辑
- 新函数签名: `_create_llm_client(config: LLMConfig, _cache: dict | None = None) -> LLMClient`
- 职责：计算缓存键 → 检查 mtime → 创建/复用客户端
- `_get_llm_client()` 简化为调用 `_create_llm_client()`

**边界条件**:
- 缓存命中且 mtime 未变 → 返回缓存客户端
- 缓存命中但 mtime 变更 → 重建客户端，更新缓存
- api_key 为空 → 抛出配置错误

#### 步骤 A2: 去除内联 prompt 构建逻辑

- 检查 `_get_prompt_builder()` 方法体中是否有与 `prompts.build_system_prompt()` 重复的代码
- 如有 → 改为调用 `build_system_prompt()` 或简化为 factory 方法
- 确保不影响 `start_round()` 中的 system prompt 构建流程

### 子任务 B: memory/memory_writer.py 外提

**涉及文件**:
- `src/transbridge/smart_assistant/memory/memory_writer.py`（新建）
- `src/transbridge/smart_assistant/memory/memory_store.py`（修改）

#### 步骤 B1: 创建 memory_writer.py

- 从 `memory_store.py` 完整复制 `MemoryWriterThread` 类定义
- 保持类名、方法签名、线程逻辑完全不变
- 复制必要的 import（`threading`, `queue` 等）

#### 步骤 B2: 更新 memory_store.py

- 删除 `MemoryWriterThread` 类定义
- 顶部添加: `from .memory_writer import MemoryWriterThread`
- 确保 `MemoryStore.__init__` 中创建 `MemoryWriterThread` 的代码不变

**边界条件**:
- 实际构造器签名: `__init__(self, storage_dir, metadata_path, index_path, get_metadata_cb, get_vector_store_cb)`（5 参数），接收 Path + 回调，不是 MemoryStore 实例，不会被循环导入影响
- 提取后需同步更新 `memory/__init__.py`，新增 `MemoryWriterThread` 导出

**测试策略**: 运行现有 memory 测试，验证 MemoryWriterThread 仍正常工作

### 子任务 C: tools/task_manager.py 精简

**涉及文件**: `src/transbridge/smart_assistant/tools/task_manager.py`（修改）

#### 步骤 C1: 合并回调方法（新增 on_finished / notify_finished）

- 新增 `on_finished(callback)` — 注册统一完成/失败回调
- 新增 `notify_finished(task_id, success, message="", data=None)` — 统一通知
- `on_completed` / `on_failed` 保留为 deprecated wrapper，内部委托给 `on_finished`
- `notify_completed` / `notify_failed` 保留为 deprecated wrapper，内部委托给 `notify_finished`

**边界条件**:
- 旧调用方 `chat_widget.py:717-718` 继续使用 `on_completed`/`on_failed`，不受影响
- 旧调用方 `tool_translator.py` / `tool_proofreader.py` 继续使用 `notify_completed`/`notify_failed`，不受影响
- 新代码可通过 `on_finished` / `notify_finished` 使用统一接口

#### 步骤 C2: 移除真正无调用方的死代码

- 检查 `_default_dispatcher` 模块级函数是否有调用方 → 如无，删除
- 其他方法（`set_main_thread_dispatcher`/`reset_dispatcher`/`get_handle`）验证有活跃调用方后保留不动
- 全局搜索确认无残留调用: `grep -r "_default_dispatcher" src/ tests/`

**测试策略**: 运行现有 task_manager 测试，验证所有公开 API 行为不变

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/smart_assistant/conversation_orchestrator.py` | 修改 | 修bug+提取+精简，433→~350行 |
| `src/transbridge/smart_assistant/memory/memory_writer.py` | 新建 | MemoryWriterThread，~42行 |
| `src/transbridge/smart_assistant/memory/memory_store.py` | 修改 | 删除类+加重导出，335→~280行 |
| `src/transbridge/smart_assistant/tools/task_manager.py` | 修改 | 移除+合并+内联，317→~280行 |

## 风险与注意事项

- **风险 1（关键）**: 修复 `react_depth`/`auto_mode` 重复属性时需精确匹配——两个定义可能有不同的 docstring、类型注解或实现。只删除第二个定义，确保第一个定义的语义完整覆盖当前行为。→ 缓解：比较两个定义的源代码，确认保留的版本包含正确逻辑。
- **风险 2**: `notify_completed`/`notify_failed` 合并后，外部调用方（如 ExecutionEngine、ChatWorker）引用了旧方法 → 缓解：全局搜索并更新所有调用方。或者保留旧方法作为 deprecated wrapper。
- **注意 1**: `task_manager.py` 的 `_safe_callback` 方法是 Phase 1 解耦引入的关键安全机制 → 不修改
- **注意 2**: `MemoryWriterThread` 可能被 `memory_store.py` 以外的模块引用 → 全局搜索确认所有 import，确保重导出覆盖
- **注意 3**: 子任务 A/B/C 互相独立，可并行开发（但在编码阶段建议串行，逐个验证测试）
