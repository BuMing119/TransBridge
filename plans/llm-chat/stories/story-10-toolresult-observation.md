# Story-10: ToolResult 观察消息序列化增强

**Phase**: 10 | **预估**: 3h | **状态**: 待编码
**对应需求**: FR7.17 | **架构引用**: ADR-012（更新 2026-05-14）
**父方案**: `../plan.md`

## 背景

当前 `ToolResult` 有 `data: dict` 承载工具返回的结构化数据（条目列表、统计数字、任务状态等），但 `ToolExecutionHandler._handle_result()` 仅提取 `message` 字符串传给 LLM，`data` 被完全丢弃。LLM 只能看到 `[OK] tool: 人读摘要`，无法基于结构化数据做后续推理。

另有 6 个工具连 `data` 都没填充，结构化信息只放在 `message` 字符串里。

## 验收标准

- [ ] `ToolResult` 新增 `pagination`、`execution_meta`、`tool_suggestions` 三个可选字段
- [ ] `ToolResult.to_observation(tool_name, max_chars=2000)` 正确格式化观察文本
- [ ] `ToolResult._serialize_data(max_chars)` 智能摘要大数据（列表→count+sample）
- [ ] `ToolExecutionHandler._handle_result()` 调用 `to_observation()` 替代仅用 `message`
- [ ] `ConversationManager.add_observation()` 换行感知截断
- [ ] 6 个工具补充 `data` 参数（filter_by_stage/category/label, search_entries, clear_all_filters, stop_task）
- [ ] `get_visible_entries` 首次使用 `pagination` 字段
- [ ] 筛选/搜索工具首次使用 `tool_suggestions` 字段
- [ ] 向后兼容：`data=None` 时输出格式不变
- [ ] 大数据场景：50+ 条目列表自动摘要为 count + 2 条样本，不超 2000 字符

## 实现步骤

### 步骤 1: 扩展 ToolResult 类（base.py）

**文件**: `src/transbridge/smart_assistant/tools/base.py`

**1a. 新增字段**：在 `@dataclass` 定义中 `warnings` 后追加：

```python
pagination: dict[str, Any] | None = None
execution_meta: dict[str, Any] | None = None
tool_suggestions: list[str] | None = None
```

**1b. 文件顶部添加 `import json`**

**1c. 新增 `to_observation()` 方法**：

```python
def to_observation(self, tool_name: str, max_chars: int = 2000) -> str:
    if self.partial:
        prefix = "[PARTIAL]"
    elif self.success:
        prefix = "[OK]"
    else:
        prefix = "[FAIL]"
    lines = [f"{prefix} {tool_name}: {self.message or ('完成' if self.success else '失败')}"]
    if self.data:
        data_budget = max(100, int(max_chars * 0.6))
        data_str = self._serialize_data(data_budget)
        if data_str:
            lines.append(f"  data: {data_str}")
    if self.warnings:
        lines.append(f"  warnings: {json.dumps(self.warnings, ensure_ascii=False)}")
    if self.pagination:
        lines.append(f"  pagination: {json.dumps(self.pagination, ensure_ascii=False, default=str)}")
    if self.execution_meta:
        lines.append(f"  meta: {json.dumps(self.execution_meta, ensure_ascii=False, default=str)}")
    if self.tool_suggestions:
        lines.append(f"  suggest: {', '.join(self.tool_suggestions)}")
    if self.failed_items:
        lines.append(f"  failed: {len(self.failed_items)} items")
        if len(self.failed_items) <= 3:
            lines.append(f"  failed_details: {json.dumps(self.failed_items, ensure_ascii=False, default=str)}")
    if self.truncated:
        lines.append("  truncated: true")
    result = "\n".join(lines)
    if len(result) > max_chars:
        keep = max_chars - 30
        cut = result.rfind("\n", 0, keep)
        if cut < max_chars // 2:
            cut = keep
        result = result[:cut] + "\n  ...(truncated)"
    return result
```

**1d. 新增 `_serialize_data()` 私有方法**（智能摘要逻辑，约 40 行）：

- 数据 < max_chars → 直接返回 `json.dumps(separators=(",",":"))`
- 数据超限 → 对 `entries`/`projects`/`tasks`/`collections`/`history`/`details` 等已知列表键名替换为 `{key}_count` + `{key}_sample`（前 2 条，每条截断到 5 字段/80 字符）
- 长字符串截断到 100 字符
- 其他 dict → `{key}_keys` + `{key}_size`

**1e. 更新 `to_dict()`**：按 existing 模式添加新字段（仅非 None 时输出）

**1f. 更新 `get()`**：添加 `pagination`/`execution_meta`/`tool_suggestions` 键处理

### 步骤 2: 更新 _handle_result（tool_execution_handler.py）

**文件**: `src/transbridge/ui/tools/smart_assistant/tool_execution_handler.py`

**2a. 提升 import**：将 `from src.transbridge.smart_assistant.tools.base import ToolResult` 移到文件顶部

**2b. 重写 `_handle_result()`**：

```python
def _handle_result(self, step: dict, result) -> None:
    tool_name = step.get("tool", "?")
    # Normalize to ToolResult
    if isinstance(result, ToolResult):
        tr = result
    elif isinstance(result, dict):
        tr = ToolResult(
            success=result.get("success", True),
            message=result.get("message", result.get("error", "")),
            data=result.get("data"),
        )
    else:
        tr = ToolResult(success=False, message=str(result))
    # UI display (simple)
    self._on_system_message(
        f"[{'OK' if tr.success else 'FAIL'}] {tool_name}: {tr.message or ('完成' if tr.success else '失败')}"
    )
    # LLM observation (rich)
    self._conversation.add_observation(tool_name, tr.to_observation(tool_name))
    self._on_react_continue()
```

### 步骤 3: 更新 add_observation 截断（conversation_manager.py）

**文件**: `src/transbridge/smart_assistant/conversation_manager.py`

将 `add_observation()` 的行内字符截断改为换行感知截断：

```python
if len(result) > self._MAX_OBSERVATION_CHARS:
    cut_pos = self._MAX_OBSERVATION_CHARS - 30
    last_nl = result.rfind("\n", 0, cut_pos)
    if last_nl > cut_pos // 2:
        result = result[:last_nl] + "\n  ...(truncated)"
    else:
        result = result[:cut_pos] + "...(truncated)"
```

### 步骤 4: 修复 6 个工具 + 示例性 pagination/suggestions

**文件**: `src/transbridge/smart_assistant/tools/tool_editor.py`（5 工具）

| 工具 | 修改 |
|------|------|
| `filter_by_stage` | `ToolResult.ok(...)` → `ToolResult.ok(..., data={"stages": stages})` + `tool_suggestions=["get_visible_entries", "get_statistics"]` |
| `filter_by_category` | 同上，data={"categories": categories} |
| `filter_by_label` | 同上，data={"labels": label_names} |
| `search_entries` | 同上，data={"query": query, "field": field}，suggestions=["get_visible_entries"] |
| `clear_all_filters` | 同上，data={"filters_cleared": True}，suggestions=["get_visible_entries", "get_statistics", "filter_by_stage"] |
| `get_visible_entries` | 在 return 前设置 `result.pagination = {"page": ..., "page_size": limit, "total_count": total, "returned_count": len(entries), "has_more": truncated}` + 条件 `tool_suggestions` |

**文件**: `src/transbridge/smart_assistant/tools/tool_translator.py`（1 工具）

| 工具 | 修改 |
|------|------|
| `stop_task` | `ToolResult.ok(...)` → `ToolResult.ok(..., data={"task_id": task_id, "stopped": True})` |

## 数据流

```
工具执行返回 ToolResult(success=True, message="...", data={...}, pagination={...})
    ↓
_execute_step() → execute_with_guardrails() → ToolResult
    ↓ [OutputValidationGuard.after_execute: 检查 data 大小 + 脱敏]
    ↓
_handle_result(step, result)
    ↓
tr.to_observation(tool_name, max_chars=2000)
    ├── → _serialize_data(1200) → 紧凑 JSON 或 摘要
    ├── → 拼接完整观察文本
    └── → 总长截断（换行边界）
    ↓
conversation_manager.add_observation(tool_name, observation_text)
    ├── → 兜底截断（安全网）
    └── → 追加 {"role": "user", "content": "【工具执行结果 - tool】\n..."}
    ↓
下一轮 LLM 调用时 get_messages() → 观察消息在对话上下文中
```

## 边界条件

| 场景 | 处理 |
|------|------|
| `data=None` | `to_observation()` 跳过 data 行，只输出状态行 |
| `data` 包含不可序列化对象 | `json.dumps(default=str)` 兜底 |
| `data` 有 200+ 条目列表 | `_serialize_data()` 替换为 count + 2 条样本 |
| 所有扩展字段均为 None | 输出与旧格式完全相同 |
| `message` 为空 | 默认 "完成" / "失败" |
| dict 类型 result（非 ToolResult） | `_handle_result` 归一化为 ToolResult |
| 观察文本 > 2000 字符 | 三层截断：data 摘要 → to_observation 裁剪 → add_observation 安全网 |

## 测试策略

1. **单元测试**：`to_observation()` 各字段组合输出正确
2. **大数据测试**：模拟 200 条目 data，验证摘要后 < 2000 字符
3. **向后兼容**：无 data 的 ToolResult 输出与旧格式逐字相同
4. **全工具覆盖**：grep 确认 6 个目标工具已修复
5. **运行验证**：启动应用，触发工具调用，确认 LLM 收到富文本观察

## 风险与回退

| 风险 | 回退 |
|------|------|
| `to_observation()` 格式 LLM 解析异常 | 格式为纯文本 `key: value` 行，LLM 天然可读；异常时与旧格式一样可容错 |
| 智能摘要丢失关键信息 | 样本保留前 2 条完整条目；LLM 可通过翻页获取更多 |
| `_serialize_data()` 性能问题（大数据） | JSON 序列化 200 条目 < 5ms；摘要后数据量极小 |