# Story 07: P0 状态查询工具 + check_quality 增强 (default + proofreader namespace)

**所属方案**: `plans/agent-tool-expansion/plan.md`
**技术模块**: backend (smart_assistant/tools)
**状态**: 已确认 (v2)
**创建日期**: 2026-05-11
**更新日期**: 2026-05-11（v2: get_collection_summary deprecated→合并到get_statistics(O8)）

## 前置依赖

### 上游 Story
- Story 01 → `ToolResult`
- Story 03 → `ctx.filter_state` + `ctx.get_statistics()` 数据源

### 引用的架构决策
- ADR-008: 纯数据操作，不碰 UI
- ADR-012: read 权限

## 验收标准

- [ ] `get_app_state` — 返回当前 step/活跃集合/项目/版本/筛选状态/API状态，permission: read
- [ ] `list_collections` — 返回所有已加载集合摘要，permission: read
- [ ] `switch_collection` — 参数 `collection_name | slot_index`，permission: write
- [ ] `get_current_filters` — 返回当前 filter_state，permission: read
- [ ] `get_statistics` — 条目统计（总数/翻译率/分布），permission: read
- [ ] 将现有 `check_quality` (proofreader) 返回格式升级为 ToolResult
- [ ] 状态查询工具注册到 `default` namespace

## 关键接口

```python
# tools/tool_default.py

def _tool_get_app_state(args, ctx) -> ToolResult:
    """聚合 AppContext 全部状态为可序列化摘要。"""
    state = {
        "current_step": getattr(ctx, 'current_step', None),
        "active_collection": _describe_collection(ctx),
        "collections_count": _count_collections(ctx),
        "project": _describe_project(ctx),
        "variant": getattr(ctx, 'active_variant', None),
        "filters": ctx.filter_state if hasattr(ctx, 'filter_state') else {},
        "api_connected": getattr(ctx, 'api_connected', False),
    }
    return ToolResult.ok(data=state)

def _tool_get_statistics(args, ctx) -> ToolResult:
    """统计当前集合的翻译进度和分布。"""
    collection = _get_collection(ctx)
    if not collection: return ToolResult.fail("当前没有加载翻译集合")
    total = len(collection)
    translated = sum(1 for e in collection if e.translation)
    stage_dist = {}
    category_dist = {}
    for e in collection:
        stage_dist[e.stage] = stage_dist.get(e.stage, 0) + 1
        cat = e.context.split(":")[0] if e.context else "UNKNOWN"
        category_dist[cat] = category_dist.get(cat, 0) + 1
    return ToolResult.ok(data={
        "total": total, "translated": translated, "untranslated": total - translated,
        "translation_rate": f"{translated/total*100:.1f}%" if total else "0%",
        "stage_distribution": stage_dist,
        "category_distribution": category_dist,
    })
```

## 实现步骤

### 步骤 1: 创建 `tool_default.py` + 实现 5 个工具

**涉及文件**: `tools/tool_default.py`（新建）

**实现要点**:
- `get_app_state`: 从 `ctx` 聚合所有状态信息，所有值可 JSON 序列化
- `list_collections`: 遍历 `ctx` 中的 slots/collections 列表，返回摘要（名称/条目数/翻译率）
- `switch_collection`: 通过 `collection_name` 匹配或 `slot_index` 直接切换 `ctx.active_slot`
- `get_current_filters`: 返回 `ctx.filter_state`（依赖 Story 03）
- `get_statistics`: 纯数据统计，遍历 collection 计算阶段分布和分类分布

**边界条件**:
- `switch_collection` 的 collection_name 不匹配 → `ToolResult.fail("未找到集合: {name}")`
- `switch_collection` 的 slot_index 越界 → `ToolResult.fail("槽位索引越界: {index}")`
- `get_statistics` 无 collection → `ToolResult.fail("当前没有加载翻译集合")`

---

### 步骤 2: 升级 `check_quality` 返回格式

**涉及文件**: `tools/tool_proofreader.py`（新建）; `tool_registry.py`（修改）

**实现要点**:
- 将 `_tool_check_quality` 从 `tool_v1.py` 移入 `tool_proofreader.py`（或保留在 `tool_v1.py` 不变，仅更新返回值）
- 返回 `ToolResult(...)` 替代 `{"success": ..., "message": ..., "data": {...}}`
- 函数逻辑和参数不变

---

### 步骤 3: 注册工具

**涉及文件**: 同上

**注册要点**: 状态查询工具 → `default` namespace；check_quality → `proofreader` namespace

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `smart_assistant/tools/tool_default.py` | 新建 | 5 个状态查询工具 |
| `smart_assistant/tools/tool_proofreader.py` | 新建 | check_quality 迁移至此（后续 Story 10 扩展） |
| `smart_assistant/tools/__init__.py` | 修改 | 导出 |
| `smart_assistant/tool_registry.py` | 修改 | check_quality 注册引用更新（可选：指向 tool_proofreader.py） |

## 风险与注意事项

- **注意**: `get_app_state` 返回的信息需过滤敏感数据（如 API key 前缀脱敏）
- **注意**: `switch_collection` 触发 UI 刷新（通过 AppContext 信号），但工具本身不直接操作 UI
