# Story 05: 线程与资源生命周期管理

**所属方案**: `plans/smart-assistant-qa-fix/plan.md`
**技术模块**: `smart_assistant/` (memory_store, agent_worker, execution_engine, conversation_manager, tool_registry)、`ui/tools/smart_assistant/` (chat_widget, panel)
**状态**: 已确认
**创建日期**: 2026-05-12
**覆盖问题**: C9（UI线程IO）、M7（cancel空操作）、M8（_paused共享）、M9（MemoryStore无限制）、M10（_trim不裁剪观察）、M11（无Token预算）、M12（观察消息无限增长）、M13（面板关闭线程未终止）、M14（_clear不清除worker）

## 前置依赖

### 上游 Story
- **Story-01**（安全护栏）: 已完成 → `_run_single` 中的护栏链已修复
- **Story-02**（异步通知）: 已完成 → TaskManager 信号机制可复用于本 Story

### 引用的架构决策
- **ADR-009 §3**（长期记忆 — MemoryStore + MemoryRetriever): 记忆存储架构
- **ADR-011**（Graph 编排引擎）: `_paused` 机制用于 checkpoint/暂停
- **ADR-008**（代码分层）: memory_store 属于 backend 子包

## 验收标准

- [ ] 记忆持久化从 UI 线程移出：`MemoryStore.add()` 提交到后台队列，由专用线程异步写入
- [ ] `AgentWorker.cancel()` 可中断正在执行的工具调用
- [ ] `ExecutionEngine._paused` 改为实例级属性，不同会话独立暂停
- [ ] `MemoryStore` 添加 `max_entries`（默认 1000）+ LRU 淘汰策略
- [ ] `ConversationManager._trim()` 裁剪时同步移除 observation/plan_result 消息
- [ ] `build_tool_schema_for_prompt()` 按当前 Agent namespace 过滤工具
- [ ] `add_observation()` 结果文本超过 2000 字符时自动截断
- [ ] `panel.py` 添加 `closeEvent` 覆盖：关闭面板时 cancel worker + stop engine
- [ ] `_clear_conversation()` 检查并取消运行中的 worker/engine

## 数据流

### C9: MemoryStore 异步写入

```
MemoryStore.add(entry)
  ├── 追加到 metadata dict + FAISS index（内存操作，快速）
  ├── 入队到 _write_queue: (entry_id, entry_data)
  ├── 立即返回（不等待磁盘 I/O）               ← FIX: 解除 UI 线程阻塞
  │
  └── MemoryWriterThread (后台 QThread)
        ├── 从队列取 batch（debounce 500ms）
        ├── JSON 写入 metadata.json
        ├── FAISS 写入 index.faiss
        └── 循环
```

### M7: AgentWorker 中断

```
AgentWorker.run()
  → for step in steps:
      if self._cancelled:              ← NEW: 中断检查
          emit error("已取消")
          return
      result = execution_engine._run_single(step)
      ...

AgentWorker.cancel()
  → self._cancelled = True             ← FIX: 之前是空操作
  → self._stop_event.set()            （已有）
```

### M10: _trim 裁剪观察消息

```
ConversationManager._trim() 当前:
  → 仅计算 user + assistant 对来裁剪
  → observation/plan_result/system 消息永不移除 ❌

修复后:
  → 遍历所有消息，保留最后 max_turns 轮
  → 每轮包含: user → assistant → [observation*] → [plan_result]
  → 裁剪时整轮移除（含该轮所有 observation/plan_result/system）
```

## 关键接口

### MemoryWriterThread

```python
# src/transbridge/smart_assistant/memory/memory_store.py

class MemoryWriterThread(QThread):
    """后台写入线程，批量刷写记忆数据到磁盘。"""
    batch_ready = pyqtSignal(int)  # queue_size

    def __init__(self, storage_dir: Path):
        super().__init__()
        self._queue: deque = deque()
        self._cond = threading.Condition()
        self._storage_dir = storage_dir
        self._running = True

    def enqueue(self, entry_id: str, entry_data: dict) -> None:
        with self._cond:
            self._queue.append((entry_id, entry_data))
            self._cond.notify()

    def run(self) -> None:
        while self._running:
            with self._cond:
                if not self._queue:
                    self._cond.wait(timeout=0.5)
                batch = list(self._queue)
                self._queue.clear()
            if batch:
                self._flush_batch(batch)

    def stop(self) -> None:
        self._running = False
        with self._cond:
            self._cond.notify()
```

### MemoryStore.with_capacity

```python
class MemoryStore:
    MAX_ENTRIES_DEFAULT = 1000

    def __init__(self, storage_dir: Path, max_entries: int = MAX_ENTRIES_DEFAULT):
        self._max_entries = max_entries
        self._access_order: list[str] = []  # LRU 链表（最旧在前）
        self._writer = MemoryWriterThread(storage_dir)
        self._writer.start()

    def add(self, entry: MemoryEntry) -> None:
        with self._lock:
            self._metadata[entry.entry_id] = entry
            self._update_lru(entry.entry_id)
            if len(self._metadata) > self._max_entries:
                self._evict_lru()
        self._writer.enqueue(entry.entry_id, entry.to_dict())

    def _evict_lru(self) -> None:
        while len(self._metadata) > self._max_entries and self._access_order:
            oldest = self._access_order.pop(0)
            del self._metadata[oldest]
            # FAISS 索引不立即删除（soft delete），定期重建
```

## 实现步骤

### 步骤 1: C9+M9 — MemoryStore 异步写入 + LRU 淘汰

**涉及文件**: `src/transbridge/smart_assistant/memory/memory_store.py`（修改）

**实现要点**:
- 新增 `MemoryWriterThread(QThread)` 后台写入线程
- `add()` 只做内存操作 + 入队，立即返回
- 添加 `max_entries` + LRU 淘汰（`_access_order` 链表）
- `close()` 方法：flush 队列 + stop writer thread
- 在 `panel.closeEvent` 中调用 `memory_store.close()`

**边界条件**:
- writer thread 写入失败 → 记录日志，不抛出（记忆丢失可接受）
- LRU 淘汰与 FAISS 索引同步 → 淘汰时标记 soft_delete，定期 `rebuild_index()`
- 应用崩溃 → 未刷盘数据丢失，恢复时从上一个 checkpoint 加载

---

### 步骤 2: M7 — AgentWorker.cancel() 实现

**涉及文件**: `src/transbridge/smart_assistant/agents/agent_worker.py`（修改）

**实现要点**:
- `run()` 方法中在每个 step 执行前后检查 `self._cancelled`
- `cancel()` 设置 `_cancelled` 标志（已有）+ `_stop_event.set()`

**伪代码**:
```python
def run(self):
    for step in self._steps:
        if self._cancelled:
            self.error.emit("任务已取消")
            return
        result = self._engine._run_single(step)
        self.progress.emit(f"完成: {step.get('tool')}")
    self.finished.emit(results)
```

---

### 步骤 3: M8 — ExecutionEngine._paused 实例化

**涉及文件**: `src/transbridge/smart_assistant/execution_engine.py`（修改）

**实现要点**:
- 删除类级 `_paused: threading.Event` 定义
- 在 `__init__` 中添加 `self._paused = threading.Event()`

---

### 步骤 4: M10 — ConversationManager._trim 裁剪观察消息

**涉及文件**: `src/transbridge/smart_assistant/conversation_manager.py`（修改）

**实现要点**:
- 改为按"轮次"裁剪：一轮 = user + assistant + 后续所有 observation/plan_result
- 保留最后 max_turns 轮
- 移除每轮关联的所有 observation 和 plan_result 消息

---

### 步骤 5: M11 — build_tool_schema_for_prompt 按 namespace 过滤

**涉及文件**: `src/transbridge/smart_assistant/tool_registry.py`（修改）

**实现要点**:
- `build_tool_schema_for_prompt(namespace=None)` 按 namespace 过滤
- namespace=None → 全部工具（编排 Agent）
- namespace="translator" → 仅 translator 命名空间工具

---

### 步骤 6: M12 — add_observation 截断

**涉及文件**: `src/transbridge/ui/tools/smart_assistant/chat_widget.py`（修改）

**实现要点**:
- `add_observation()` 中结果文本超过 2000 字符时自动截断
- 截断后追加 `...(已截断，共 {total} 字符)`

---

### 步骤 7: M13 — panel.closeEvent 清理

**涉及文件**: `src/transbridge/ui/tools/smart_assistant/panel.py`（修改）

**实现要点**:
- 覆盖 `closeEvent`：cancel worker + stop engine + close memory_store
- 等待线程结束（最多 3 秒超时）

**伪代码**:
```python
def closeEvent(self, event):
    if self._chat_widget._worker and self._chat_widget._worker.isRunning():
        self._chat_widget._worker.cancel()
        self._chat_widget._worker.wait(3000)
    if self._chat_widget._engine:
        self._chat_widget._engine.cancel()
    self._chat_widget._memory_store.close()
    super().closeEvent(event)
```

---

### 步骤 8: M14 — _clear_conversation 取消 worker/engine

**涉及文件**: `src/transbridge/ui/tools/smart_assistant/chat_widget.py`（修改）

**实现要点**:
- `_clear_conversation()` 开头检查并取消 `_worker` 和 `_engine`
- 清空 `_react_depth`、`_uploaded_docs`（M13 联动）

---

### 步骤 9: 汇总验证

**实现要点**:
- 启动应用 → 打开面板 → 对话 → 关闭面板 → 确认无残留线程
- 大量记忆写入 → 确认 UI 不冻结
- 多会话同时暂停 → 确认互不影响

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/smart_assistant/memory/memory_store.py` | 修改 | MemoryWriterThread + LRU 淘汰 + async write |
| `src/transbridge/smart_assistant/agents/agent_worker.py` | 修改 | cancel() 实现 + 中断检查 |
| `src/transbridge/smart_assistant/execution_engine.py` | 修改 | _paused 实例化 |
| `src/transbridge/smart_assistant/conversation_manager.py` | 修改 | _trim 扩展裁剪观察消息 |
| `src/transbridge/smart_assistant/tool_registry.py` | 修改 | build_tool_schema_for_prompt 按 namespace 过滤 |
| `src/transbridge/ui/tools/smart_assistant/chat_widget.py` | 修改 | add_observation 截断 + _clear 清理 |
| `src/transbridge/ui/tools/smart_assistant/panel.py` | 修改 | closeEvent 线程清理 |

## 风险与注意事项

- **风险**: MemoryWriterThread 在应用退出时可能未 flush 最后一批数据 → **缓解**: `close()` 中调用 `_writer.stop()` 前先 flush 队列；`atexit` 注册清理
- **风险**: `_paused` 实例化后，外部代码可能依赖类级属性访问 → **缓解**: grep `ExecutionEngine._paused` 确认所有引用改用实例访问
- **注意**: M11 按 namespace 过滤后，不同 Agent 看到的工具 schema 不同，需确保 Agent 注册时正确设置 namespace
