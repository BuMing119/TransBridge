# Story 15: FR9.11 工具补完 — 搜索维度扩展 + ParaTranz 项目查询与切换

**Epic**: [agent-tool-expansion](../plan.md)
**对应需求**: [FR9.11](../../../docs/requirements.md)
**状态**: 已确认
**优先级**: P1
**创建日期**: 2026-05-15

## 概述

对 FR9.2（editor namespace）和 FR9.5（paratranz namespace）已编码工具的补完。包含两个独立子功能：
1. `search_entries` 搜索维度从 4 字段扩展至 6 字段，底层 `filter_entries()` 补全缺失的搜索分支
2. 新增 ParaTranz 项目查询与切换工具，填补"无当前选中项目"的缺口

## 数据流

```
Tool 调用                    filter_entries() 分支
─────────                    ────────────────────
search_entries(field="id")        → e.id 匹配
search_entries(field="key")       → e.key 匹配
search_entries(field="original")  → e.original 匹配
search_entries(field="translation")→ e.translation 匹配  ← NEW
search_entries(field="context")   → e.context 匹配      ← NEW
search_entries(field="all")       → OR(key+original+translation+context) ← NEW
search_entries(field="text")      → e.original 匹配（兼容）

switch_paratranz_project(pid) → ctx.paratranz_project_id = pid
                                 ↓
get_paratranz_project()       → 读取 ctx.paratranz_project_id
                                 ↓
其他 PT 工具不传 project_id   → _get_paratranz_client() 自动取 ctx.paratranz_project_id
```

## 实现步骤

### 步骤 1: 修改 `tools/tool_editor.py` — search_entries 参数校验

**文件**: `src/transbridge/smart_assistant/tools/tool_editor.py`

- 更新 `_PARAM_SCHEMAS["search_entries"]["field"]` 的 description，列出 6 个有效值：`id, key, original, translation, context, all`
- 更新 `_tool_search_entries()` 中的 field 校验逻辑：
  ```python
  VALID_FIELDS = ("id", "key", "original", "translation", "context", "all", "text")
  if field not in VALID_FIELDS:
      return ToolResult.fail(f"无效的搜索字段: {field}，可选: id, key, original, translation, context, all")
  # "text" 向后兼容，映射到 "original"
  if field == "text":
      field = "original"
  ```
- description 中不推荐 `text`，但代码层面保留映射

### 步骤 2: 修改 `tools/base.py` — filter_entries 搜索分支补全

**文件**: `src/transbridge/smart_assistant/tools/base.py`

在 `filter_entries()` 函数的搜索逻辑部分（当前约第 483-490 行），将单一的 `else` 分支扩展为多个显式分支：

```python
search_query = filter_state.get("search_query")
search_field = filter_state.get("search_field", "text")
if search_query:
    q = search_query.lower()
    if search_field == "id":
        results = [e for e in results if q in (e.id or "").lower()]
    elif search_field == "key":
        results = [e for e in results if q in (e.key or "").lower()]
    elif search_field in ("original", "text"):
        results = [e for e in results if q in (e.original or "").lower()]
    elif search_field == "translation":
        results = [e for e in results if q in (e.translation or "").lower()]
    elif search_field == "context":
        results = [e for e in results if q in (e.context or "").lower()]
    elif search_field == "all":
        results = [
            e for e in results
            if q in (e.key or "").lower()
            or q in (e.original or "").lower()
            or q in (e.translation or "").lower()
            or q in (e.context or "").lower()
        ]
```

**边界条件**:
- `translation`/`context` 为 `None` 时与空字符串等效（`e.translation or ""`）
- `all` 是 OR 语义（任一字段匹配即命中），非 AND
- 空字符串 query 在进入 if 块前已被过滤（`if search_query:` 在 `q = search_query.lower()` 之前为 truthy 检查）

### 步骤 3: 修改 `ui/context.py` AppContext — 新增 paratranz_project_id

**文件**: `src/transbridge/ui/context.py`

在 `AppContext.__init__()` 中新增：
```python
self.paratranz_project_id: int | None = None
```

此为普通 Python 属性（非 pyqtSignal），会话内有效即可，无需持久化。

### 步骤 4: 追加 `tools/tool_paratranz.py` — 新增 2 个工具

**文件**: `src/transbridge/smart_assistant/tools/tool_paratranz.py`

**`_tool_get_paratranz_project`**:
```python
def _tool_get_paratranz_project(args: dict, ctx) -> ToolResult:
    """获取当前选中的 ParaTranz 项目。"""
    pid = getattr(ctx, 'paratranz_project_id', None)
    if not pid:
        return ToolResult.ok("未选择 ParaTranz 项目", data={"selected_project": None})
    try:
        from src.transbridge.paratranz.api_client import ParatranzClient
        client = ParatranzClient(ctx.config)
        info = client.get_project(pid)
        return ToolResult.ok(
            f"当前 ParaTranz 项目: {info.get('name')} (id={pid})",
            data={"id": info.get("id"), "name": info.get("name"), "visibility": info.get("visibility")}
        )
    except Exception as exc:
        return ToolResult.fail(f"获取项目信息失败: {exc}")
```

**`_tool_switch_paratranz_project`**:
```python
def _tool_switch_paratranz_project(args: dict, ctx) -> ToolResult:
    """切换当前选中的 ParaTranz 项目。"""
    project_id = args["project_id"]
    try:
        from src.transbridge.paratranz.api_client import ParatranzClient
        client = ParatranzClient(ctx.config)
        info = client.get_project(project_id)  # 验证有效性
        ctx.paratranz_project_id = project_id
        return ToolResult.ok(
            f"已切换到项目: {info.get('name')} (id={project_id})",
            data={"id": info.get("id"), "name": info.get("name"), "visibility": info.get("visibility")}
        )
    except Exception as exc:
        return ToolResult.fail(f"切换项目失败: {exc}")
```

### 步骤 5: 更新 `_get_paratranz_client()` — project_id 默认值关联

**文件**: `src/transbridge/smart_assistant/tools/tool_paratranz.py`

修改 `_get_paratranz_client()` 函数（当前第 7-16 行）：

```python
def _get_paratranz_client(ctx, project_id=None):
    from src.transbridge.paratranz.api_client import ParatranzClient
    client = ParatranzClient(ctx.config)
    # 优先显式传入的 project_id，其次 ctx 中选中的项目
    pid = project_id or getattr(ctx, 'paratranz_project_id', None)
    return client, pid
```

此修改后，所有已有 PT 工具在未显式传 `project_id` 时自动使用用户选中的项目。

### 步骤 6: 注册新工具

**文件**: `src/transbridge/smart_assistant/tools/tool_paratranz.py`

在 `_register_paratranz_tools()` 函数中追加两个工具注册：
```python
("get_paratranz_project", "PT当前项目", "获取当前选中的 ParaTranz 项目", _tool_get_paratranz_project, "read"),
("switch_paratranz_project", "切换PT项目", "切换到指定的 ParaTranz 项目（project_id 必填）", _tool_switch_paratranz_project, "write"),
```

同时更新 `_PARAM_SCHEMAS` 新增：
```python
"switch_paratranz_project": {
    "project_id": {"type": "int", "required": True, "description": "目标项目 ID"},
},
```

## 工具参数参考

| 工具 | 命名空间 | 权限 | 参数 | 确认 |
|------|---------|------|------|------|
| `search_entries`（增强） | editor | read | `query: str`, `field: str` (id/key/original/translation/context/all/text兼容) | 否 |
| `get_paratranz_project` | paratranz | read | 无 | 否 |
| `switch_paratranz_project` | paratranz | write | `project_id: int`（必填） | 否 |

## 验收标准

- [ ] `search_entries` 接受 6 个有效 field 值，text 兼容映射
- [ ] 无效 field 值返回 `ToolResult.fail` 并列出有效值
- [ ] `filter_entries` 中 `translation` 分支正确搜索译文
- [ ] `filter_entries` 中 `context` 分支正确搜索上下文
- [ ] `filter_entries` 中 `all` 分支 OR 匹配 4 个字段
- [ ] `get_paratranz_project` 返回当前项目或"未选择"
- [ ] `switch_paratranz_project(valid_id)` 验证通过后存入 AppContext
- [ ] `switch_paratranz_project(invalid_id)` 返回错误
- [ ] PT 工具不传 project_id 时自动使用当前选中项目
- [ ] 工具注册到 paratranz namespace

## 架构依赖

- **ADR-008**: smart_assistant 分层原则 — 工具代码纯后端，不依赖 UI
- **ADR-012**: 安全护栏 — permission 分级（read/write），中间件链自动适用

## 风险

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| `filter_entries` 改动影响所有调用方 | 低 | 高 | 仅为新增 elif 分支，不改现有分支逻辑；`text` 保留兼容映射 |
| ParatranzClient.get_project() 返回格式变化 | 低 | 低 | 使用 `.get()` 安全访问，异常捕获返回 ToolResult.fail |
| `paratranz_project_id` 跨线程竞态 | 低 | 低 | 单一写入点（switch 工具），读取均为 `getattr` 安全访问 |
