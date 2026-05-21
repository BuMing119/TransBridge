# Story 26: 后处理断点续传与暂停/恢复

**所属方案**: `plans/agent-tool-expansion/plan.md`
**技术模块**: backend (smart_assistant)
**状态**: 已实现
**创建日期**: 2026-05-21

## 前置依赖

### 上游 Story
- Story 25（同 plan）：已完成 → 提供 `run_postprocess` 统一工具，本 Story 在其基础上增强
- Story 02（同 plan）：已完成 → 提供 `TaskManager` 单例，`TaskHandle` 已预留 `pause_event` 字段

### 跨 Plan 依赖
- `ai-post-process/plan.md` → `PostProcessor.process_entries(entries, stop_event, pause_event, checkpoint, ...)` — 已原生支持
- `ai-post-process/plan.md` → `PostProcessCheckpoint` — 已有 `save()`/`load()`/`is_batch_completed()`/`mark_batch_completed()`/`delete()`

### 引用的架构决策
- ADR-008: 后端去 PyQt6 — TaskManager 使用纯 Python threading + 回调，pause/resume 方法保持无 Qt 依赖
- ADR-012: 安全护栏 — stop_task 新增 action 参数通过 `@validate_params` 校验

## 验收标准

（从 plan 原样复制）

- [ ] `run_postprocess` 每阶段完成后保存 `PostProcessCheckpoint` 到文件；再次调用时检测已有 checkpoint，跳过已完成阶段（`is_batch_completed` 按 phase+entry_ids 匹配）
- [ ] 正常完成或用户停止后自动删除 checkpoint 文件
- [ ] `stop_task` 工具扩展 `action` 参数：`"pause"`（set pause_event） / `"resume"`（clear pause_event） / `"stop"`（set stop_event，默认行为）
- [ ] `get_task_status` 对 paused 任务返回 `"paused"` 状态；`list_active()` 包含 paused 任务
- [ ] `run_postprocess` 将 `pause_event` 传入 `process_entries()`，暂停时等待当前批次完成后挂起
- [ ] checkpoint 文件损坏时跳过恢复，从头开始，记录警告日志

## 数据流

```
用户/LLM 调用 stop_task(action="pause")
    → _tool_stop_task() 解析 action 参数
    → TaskManager.pause(task_id)
    → TaskHandle.pause_event.clear()
    → 后台线程中 process_entries() 检测 pause_event.is_set()==False
    → pause_event.wait() 阻塞当前批次完成后挂起
    → get_task_status() 返回 status="paused"

用户/LLM 调用 stop_task(action="resume")
    → TaskManager.resume(task_id)
    → TaskHandle.pause_event.set()
    → 后台线程从 wait() 返回，继续下一批次

run_postprocess 启动时
    → PostProcessCheckpoint.load(esp_path)
    → 若 checkpoint 存在且有效 → 传给 process_entries(checkpoint=cp)
    → processor 内部按 phase 调用 is_batch_completed() 跳过已完成阶段
    → 每阶段完成后 mark_batch_completed() + checkpoint.save()
    → 正常完成/停止 → checkpoint.delete()
```

## 关键接口

### TaskManager 新增方法

```python
class TaskManager:
    def pause(self, task_id: str) -> bool:
        """暂停任务：clear pause_event，更新状态为 paused。返回是否成功。"""
    
    def resume(self, task_id: str) -> bool:
        """恢复任务：set pause_event，更新状态为 running。返回是否成功。"""
```

### TaskManager 修改方法

```python
class TaskManager:
    def list_active(self) -> list[str]:
        """列出所有活跃任务 ID。修改：包含 status=="paused" 的任务。"""
    
    def register(self, ...) -> str:
        """注册新任务。修改：默认创建 pause_event（当前仅预留字段，改为实际创建）。"""
```

### tool_proofreader 增强

```python
def _tool_run_postprocess(args: dict, ctx) -> ToolResult:
    """增强：checkpoint 保存/加载/清理 + pause_event 传递"""
    # 新增：从文件加载 checkpoint
    # 新增：传入 process_entries(checkpoint=cp, pause_event=pause_event)
    # 新增：每阶段后 checkpoint.save()
    # 新增：完成/停止后 checkpoint.delete()
```

### tool_translator stop_task 增强

```python
def _tool_stop_task(args: dict, ctx) -> ToolResult:
    """增强：新增 action 参数"""
    # action: "stop"(默认) / "pause" / "resume"
    # pause → tm.pause(task_id)
    # resume → tm.resume(task_id)
    # stop → tm.cancel(task_id) (原有行为)
```

## 实现步骤

### 步骤 1: TaskManager 补全 pause/resume 方法

**涉及文件**: `src/transbridge/smart_assistant/tools/task_manager.py`（修改）

**实现要点**:
- 新增 `pause(task_id)` — 获取 handle，`handle.pause_event.clear()`，`handle.status = "paused"`，返回 bool
- 新增 `resume(task_id)` — 获取 handle，`handle.pause_event.set()`，`handle.status = "running"`，返回 bool
- 修改 `register()` — 当未传入 `stop_event` 时默认创建 `threading.Event()`，同时默认创建 `pause_event=threading.Event()` 并设为 set（初始非暂停）
- 修改 `list_active()` — 返回 `status in ("running", "paused")` 的任务
- 修改 `list_active()` 的注释 — 移除 "不再有 paused 状态" 的过时注释

**边界条件**:
- 任务不存在 → `pause()`/`resume()` 返回 False
- 任务已完成/失败/取消 → pause/resume 操作无意义但不应崩溃（handle 存在即可操作）
- pause_event 为 None（旧版 TaskHandle） → `pause()` 自动创建并 clear

**伪代码**:
```python
def pause(self, task_id: str) -> bool:
    with self._lock:
        handle = self._tasks.get(task_id)
    if handle is None:
        return False
    if handle.pause_event is None:
        handle.pause_event = threading.Event()
    handle.pause_event.clear()
    handle.status = "paused"
    return True

def resume(self, task_id: str) -> bool:
    with self._lock:
        handle = self._tasks.get(task_id)
    if handle is None:
        return False
    if handle.pause_event is None:
        handle.pause_event = threading.Event()
    handle.pause_event.set()
    handle.status = "running"
    return True
```

**测试策略**:
- `test_pause_resume` — 创建任务，pause→验证 status=paused，resume→验证 status=running
- `test_pause_nonexistent` — pause 不存在的 task_id，返回 False
- `test_list_active_includes_paused` — paused 任务出现在 list_active() 中
- `test_pause_event_default_set` — 新注册的任务 pause_event 初始为 set

### 步骤 2: tool_proofreader 串联 checkpoint 和 pause_event

**涉及文件**: `src/transbridge/smart_assistant/tools/tool_proofreader.py`（修改）

**实现要点**:
- `_run()` 中，在创建 `processor` 前加载 checkpoint：
  ```python
  cp = None
  esp_path = getattr(ctx, 'esp_path', None)
  if esp_path:
      cp = PostProcessCheckpoint.load(esp_path)
      if cp:
          logger.info("从 checkpoint 恢复: %s, 已完成阶段=%s", esp_path, list(cp.completed_batches.keys()))
  ```
- `process_entries()` 调用增加参数：
  ```python
  result = processor.process_entries(
      entries, stop_event=stop_event,
      pause_event=pause_event,  # 新增
      checkpoint=cp,            # 新增
      esp_path=esp_path,
      progress_callback=_progress,
      max_workers=max_workers,
  )
  ```
- 在 `_run()` 开始处创建 `pause_event`（从 TaskManager 获取或新建），并注册到 TaskHandle
- 每阶段完成后（通过 progress_callback 检测 phase change）调用 `checkpoint.save(esp_path)`
- 完成（completed/failed/cancelled）后调用 `checkpoint.delete(esp_path)`
- 更新 `register()` 调用传入 `pause_event`
- `_tool_get_task_status` 需要识别 paused 状态

**边界条件**:
- `esp_path` 为 None → 跳过 checkpoint 加载，不使用断点续传
- `PostProcessCheckpoint.load()` 返回 None（文件不存在/损坏） → 从头开始，记录 info/警告
- 进度回调中检测 phase 变化 → 通过保存上次 phase 名来检测切换
- 用户 stop → `stop_event.set()` 触发 `InterruptedError` → 在 except 块中删除 checkpoint
- pause_event 从 TaskManager 获取 → `tm.get_handle(task_id).pause_event`

**伪代码**:
```python
def _run():
    global _last_report
    cp = None
    last_phase = [None]  # 用列表以在闭包中修改
    
    try:
        # ... LLMClient/TermDB/Config 创建 ...
        
        # 加载 checkpoint
        esp_path = getattr(ctx, 'esp_path', None)
        if esp_path:
            try:
                cp = PostProcessCheckpoint.load(esp_path)
                if cp:
                    logger.info("从 checkpoint 恢复，已完成: %s", list(cp.completed_batches.keys()))
            except Exception:
                logger.warning("Checkpoint 加载失败，从头开始")
                cp = None
        
        # 获取 pause_event
        pause_event = threading.Event()
        pause_event.set()  # 初始非暂停
        handle = tm.get_handle(task_id)
        if handle:
            handle.pause_event = pause_event
        
        # 阶段跟踪回调
        def _progress(phase, current, total, message):
            tm.update_progress(task_id, {...})
            if phase != last_phase[0] and last_phase[0] is not None:
                # 阶段切换，保存 checkpoint
                if cp:
                    cp.mark_batch_completed(last_phase[0], all_entry_ids)
                    if esp_path:
                        cp.save(esp_path)
            last_phase[0] = phase
        
        processor = PostProcessor(config)
        processor.register_default_checkers(...)
        
        result = processor.process_entries(
            entries, stop_event=stop_event,
            pause_event=pause_event,
            checkpoint=cp,
            esp_path=esp_path,
            progress_callback=_progress,
            max_workers=max_workers,
        )
        
        # 正常完成 → 删除 checkpoint
        if cp and esp_path:
            cp.delete(esp_path)
        # ... 报告生成 ...
        
    except InterruptedError:
        # 用户停止 → 删除 checkpoint（用户意图是不继续）
        if cp and esp_path:
            cp.delete(esp_path)
        # ...
    except Exception:
        # 异常 → 保留 checkpoint（可恢复）
        # ...
```

**测试策略**:
- `test_checkpoint_save_on_phase_change` — mock processor，验证阶段切换时 checkpoint.save() 被调用
- `test_checkpoint_load_on_restart` — 预置 checkpoint 文件，再次调用时验证 is_batch_completed 被检查
- `test_checkpoint_delete_on_completion` — 正常完成后验证 checkpoint 文件不存在
- `test_checkpoint_delete_on_stop` — 停止后验证 checkpoint 文件被删除
- `test_checkpoint_skip_on_corrupt` — 损坏的 checkpoint 文件 → 从头开始，日志含警告
- `test_pause_event_passed_to_processor` — 验证 pause_event 传入 process_entries()

### 步骤 3: stop_task 扩展 action 参数

**涉及文件**: `src/transbridge/smart_assistant/tools/tool_translator.py`（修改）

**实现要点**:
- `_tool_stop_task()` 新增 `action` 参数
- `action="stop"`（默认）→ 现有行为：`tm.cancel(task_id)`
- `action="pause"` → `tm.pause(task_id)`
- `action="resume"` → `tm.resume(task_id)`
- 不传 `task_id` 时，对所有活跃任务执行 action
- 更新参数 schema `_PARAM_SCHEMAS["stop_task"]`

**边界条件**:
- 无效 action 值 → 返回 `ToolResult.fail("无效 action，可选: stop/pause/resume")`
- 无活跃任务 → 返回 `ToolResult.ok("当前无运行中的任务")`
- 批量 pause → 所有活跃任务逐个 pause，汇总结果

**伪代码**:
```python
def _tool_stop_task(args: dict, ctx) -> ToolResult:
    task_id = args.get("task_id")
    action = args.get("action", "stop")
    
    if action not in ("stop", "pause", "resume"):
        return ToolResult.fail(f"无效 action: {action}，可选: stop, pause, resume")
    
    tm = TaskManager()
    
    if not task_id:
        active = tm.list_active()
        if not active:
            return ToolResult.ok("当前无运行中的任务")
        
        if action == "pause":
            for tid in active:
                tm.pause(tid)
            return ToolResult.ok(f"已暂停 {len(active)} 个任务")
        elif action == "resume":
            for tid in active:
                tm.resume(tid)
            return ToolResult.ok(f"已恢复 {len(active)} 个任务")
        else:  # stop
            # 原有批量停止逻辑
            ...
    else:
        if action == "pause":
            ok = tm.pause(task_id)
        elif action == "resume":
            ok = tm.resume(task_id)
        else:
            ok = tm.cancel(task_id)
        # ...
```

**测试策略**:
- `test_stop_task_pause` — 创建运行中任务，stop_task(action="pause") → 验证 TaskHandle.pause_event.is_set()==False
- `test_stop_task_resume` — 暂停后 stop_task(action="resume") → 验证 TaskHandle.pause_event.is_set()==True
- `test_stop_task_invalid_action` → 返回 fail
- `test_stop_task_pause_all` — 多个活跃任务，不传 task_id → 全部暂停

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/smart_assistant/tools/task_manager.py` | 修改 | 新增 pause()/resume()；register() 默认创建 pause_event；list_active() 含 paused |
| `src/transbridge/smart_assistant/tools/tool_proofreader.py` | 修改 | checkpoint 加载/保存/清理；pause_event 传入 process_entries()；阶段跟踪回调 |
| `src/transbridge/smart_assistant/tools/tool_translator.py` | 修改 | stop_task 扩展 action 参数；更新 _PARAM_SCHEMAS |

## 风险与注意事项

- **checkpoint 与 entry_ids 的匹配**: `PostProcessCheckpoint.is_batch_completed(phase, entry_ids)` 按排序后的 entry_ids 列表匹配。若两次调用的 entry_ids 不同（如 scope 变化），已完成的阶段不会被跳过。这是期望行为——不同的条目集合应重新处理
- **阶段跟踪**: `process_entries()` 的 `progress_callback` 在阶段切换时被调用，通过比较上次 phase 名检测切换并触发 checkpoint 保存。需注意第一阶段开始时不触发保存（`last_phase` 初始为 None）
- **pause_event 生命周期**: `pause_event` 在 `_run()` 内创建，需在 `thread.start()` 前回写到 `TaskHandle.pause_event`，确保 `stop_task` 工具可访问
- **checkpoint 文件路径**: 使用 `PostProcessCheckpoint._get_path(esp_path)` 的已有逻辑（`data/ai_translator/{stem}/{stem}_post_process.json`），不引入新路径约定
- **线程安全**: `TaskManager.pause()/resume()` 持有 `_lock`，`pause_event.clear()/set()` 本身是线程安全的
