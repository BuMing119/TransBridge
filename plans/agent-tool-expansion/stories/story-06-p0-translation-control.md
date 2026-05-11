# Story 06: P0 翻译执行控制工具 (translator namespace)

**所属方案**: `plans/agent-tool-expansion/plan.md`
**技术模块**: backend (smart_assistant/tools)
**状态**: 已确认 (v2)
**创建日期**: 2026-05-11
**更新日期**: 2026-05-11（v2: -pause_task(B5) +stop_all_tasks(E7)）

## 前置依赖

### 上游 Story
- Story 01 → `ToolResult`
- Story 02 → `TaskManager`（注册/取消/状态查询）

### 跨 Plan 依赖
- 现有 `ai_translator/translator.py` → `AutoTranslator` 类（翻译入口）
- 现有 `ai_translator/post_processor/` → `LLMPolisher` 类（润色入口）

### 引用的架构决策
- ADR-012: write/admin 权限 + require_confirmation
- ADR-004: QThread 异步模式（翻译在后台线程执行）

## 验收标准

- [ ] `start_translation` — 参数 `mode: str, entry_ids: list[str] | None`，启动 AutoTranslator
- [ ] `start_polish` — 参数 `entry_ids: list[str], intensity: str`，启动 LLMPolisher
- [ ] `pause_task` — 通过 TaskManager 暂停
- [ ] `stop_task` — 设置 stop_event，需确认
- [ ] `get_task_status` — 调用 TaskManager.get_status()
- [ ] 全部注册到 `translator` namespace（与 v1 lookup_terms/translate_entries 同 namespace）

## 数据流

```
start_translation(mode="translate", entry_ids=None)
    → @validate_params → @require_collection
    → 创建 stop_event = threading.Event()
    → task_id = TaskManager.register(stop_event, metadata={"mode": "translate"})
    → ThreadPoolExecutor.submit(_run_translation, ...)
    → 立即返回 ToolResult.ok(data={"task_id": task_id})

_run_translation(collection, entry_ids, stop_event, task_id):
    → AutoTranslator.translate(collection, entry_ids, progress_callback, stop_event)
    → progress_callback → TaskManager.update_progress(task_id, progress)
    → 完成 → TaskManager._tasks[task_id].status = "completed"
    → 异常 → TaskManager._tasks[task_id].status = "failed"

pause_task(task_id):
    → TaskManager._tasks[task_id].status = "paused"

stop_task(task_id):
    → TaskManager.cancel(task_id) → stop_event.set()
    → 翻译线程检测到 stop_event.is_set() → 保存进度 → 退出

get_task_status(task_id):
    → TaskManager.get_status(task_id) → ToolResult.ok(data=status_dict)
```

## 关键接口

```python
# tools/tool_translator.py

def _tool_start_translation(args, ctx, collection) -> ToolResult:
    mode = args.get("mode", "translate")
    entry_ids = args.get("entry_ids")
    stop_event = threading.Event()
    task_id = TaskManager().register(stop_event, metadata={"type": "translation", "mode": mode})
    # 在后台线程启动翻译
    thread = threading.Thread(target=_run_translation, args=(collection, entry_ids, stop_event, task_id, mode))
    thread.start()
    return ToolResult.ok(f"翻译任务已启动: {mode}", data={"task_id": task_id})

def _tool_stop_task(args, ctx) -> ToolResult:
    task_id = args.get("task_id")
    # 如果不传 task_id，取消所有活跃任务
    if task_id:
        ok = TaskManager().cancel(task_id)
    else:
        for tid in TaskManager().list_active():
            TaskManager().cancel(tid)
        ok = True
    return ToolResult.ok("任务已停止") if ok else ToolResult.fail("任务不存在或已终止")
```

## 实现步骤

### 步骤 1: 创建模块骨架 + 实现 `start_translation`

**涉及文件**: `tools/tool_translator.py`（新建）

**实现要点**:
- 封装 `AutoTranslator` 的翻译调用
- 后台线程执行：在 `threading.Thread` 中运行翻译逻辑
- 进度回调桥接：`AutoTranslator` 的 progress_callback → `TaskManager.update_progress()`
- 返回 task_id 而非等待完成（long_running 语义）

**边界条件**:
- 无 collection → `@require_collection` 拦截
- entry_ids 为空列表 → 视为"使用作用域"（从 ctx.filter_state 获取匹配条目）

---

### 步骤 2: 实现 `start_polish`

**涉及文件**: 同上追加

**实现要点**:
- 封装 `LLMPolisher` 的润色调用
- 支持 `intensity` 参数映射到 `PolishIntensity` 枚举

**边界条件**:
- entry_ids 全部无译文 → 跳过，返回消息

---

### 步骤 3: 实现 `pause_task` / `stop_task` / `get_task_status`

**涉及文件**: 同上追加

**实现要点**:
- `pause_task`: 更新 TaskManager 状态（不 set stop_event，仅标记）
- `stop_task`: 调用 `TaskManager.cancel()`（set stop_event）
- `get_task_status`: 代理到 `TaskManager.get_status()`
- `stop_task` 设置 `require_confirmation: True`，permission: `admin`

**边界条件**:
- task_id 不存在 → `ToolResult.fail("任务不存在: {task_id}")`
- 不传 task_id → 操作所有活跃任务

---

### 步骤 4: 注册到 translator namespace

**涉及文件**: 同上

**注册要点**: 与 v1 的 `lookup_terms` / `translate_entries` 同 namespace

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `smart_assistant/tools/tool_translator.py` | 新建 | 5 个执行控制工具 |
| `smart_assistant/tools/__init__.py` | 修改 | 导出 |

## 风险与注意事项

- **注意**: 翻译线程异常时需在 `_run_translation` 中 try/except 并更新 TaskManager 状态，否则 `get_task_status` 永远不返回失败
- **注意**: `stop_event` 检查需在翻译循环中定期执行。现有 `AutoTranslator` 已支持 stop_event 参数
- **注意**: v1 的 `translate_entries` 工具功能与本 Story 的 `start_translation` 不同 — v1 是同步等待完成，新工具是异步启动+返回 task_id。两者共存，LLM 根据场景选择
