# Story 18: stop_task 合并 (2→1)

**Epic**: agent-tool-expansion
**优先级**: P0
**净减**: -1 工具
**风险**: 低
**依赖**: S16（注册样板改造后的 tool_translator.py）
**状态**: 已方案

## 范围

合并 `stop_task` + `stop_all_tasks` → `stop_task`，`task_id` 改为可选参数。

## 验收标准

- [ ] `stop_task` 注册到 `translator` namespace
- [ ] 参数 `task_id: str | None` — 传 `task_id` 停止指定任务，不传（`None` 或空字符串 `""`）停止所有活跃任务
- [ ] 停止所有时返回被停止的任务 ID 列表
- [ ] 保留 `require_confirmation=True`
- [ ] 旧 `stop_all_tasks` 保留 deprecated wrapper，添加 `DeprecationWarning`，不注册到 ToolRegistry

## 实现步骤

1. 修改 `_tool_stop_task()`：
   - `task_id` 参数改为可选，默认 `None`
   - `task_id` 为空（`None` 或 `""`）→ `TaskManager().list_active()` 获取所有活跃任务 → 逐个 cancel → 返回 `{"stopped_task_ids": [...]}`
   - `task_id` 有值 → 保持现有逻辑：cancel 指定任务
2. 编写 `_tool_stop_all_tasks` deprecated wrapper：
   - `warnings.warn("stop_all_tasks is deprecated, use stop_task without task_id instead", DeprecationWarning)`
   - 转发到 `_tool_stop_task({"task_id": None}, ctx)`
3. 更新 `_PARAM_SCHEMAS`：
   - `"stop_task"` 的 `task_id` 从 `required: True` 改为 `required: False`
   - 保留 `"stop_all_tasks"` 的 schema 定义（wrapper 引用）
4. 更新 `_register_translator_tools()`：移除 `stop_all_tasks` 注册，更新 `stop_task` 描述
5. 运行 translator 相关测试

## 涉及文件

- `tools/tool_translator.py`

## 参数设计

```python
_PARAM_SCHEMAS["stop_task"] = {
    "task_id": {"type": "str", "required": False, "description": "要停止的任务ID，不传则停止所有运行中任务"},
}
```

## 边界条件

- `task_id` 传 `None` 且无活跃任务 → 返回 "当前无运行中的任务"
- `task_id` 传 `""` → 等同于 `None`（停止所有）
- `task_id` 传不存在的 ID → 返回错误 "任务 xxx 不存在或已结束"
- 停止所有时部分失败 → 返回 `partial=True`，`data["failed_task_ids"]` 列出失败的
