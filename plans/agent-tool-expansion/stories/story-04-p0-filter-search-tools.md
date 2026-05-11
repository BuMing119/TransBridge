# Story 04: P0 筛选+搜索+编辑+选择+批量标记工具 (editor namespace)

**所属方案**: `plans/agent-tool-expansion/plan.md`
**技术模块**: backend (smart_assistant/tools)
**状态**: 已确认 (v2)
**创建日期**: 2026-05-11
**更新日期**: 2026-05-11（v2: 合并原 Story 05 +set_stage(H3) +_selected_ids(H2) +new_stage参数(H4) +_filter_entries复用(H8)）

## 前置依赖

### 上游 Story
- Story 01 → `ToolResult` + `@validate_params` + `@require_collection`（本 Story 部分工具使用）
- Story 03 → `ctx.set_filter()` / `ctx.clear_filters()` / `ctx.filter_state`

### 引用的架构决策
- ADR-008: 架构师路线 — 纯数据操作，工具不碰 QTableWidget
- ADR-012: 安全护栏 read 权限

## 验收标准

- [ ] `filter_by_stage` / `filter_by_category` / `filter_by_label` — 筛选工具
- [ ] `search_entries` / `clear_all_filters` — 搜索与清除筛选
- [ ] `get_visible_entries` — 参数 `limit: int(50), offset: int(0)`，**复用 `_filter_entries()` 公共函数（H8）**，纯数据过滤，上限 200，含 `truncated` + `total_count`
- [ ] `select_entries` — 参数 `entry_ids: list[str], action: str("select"/"deselect")`，操作独立 `_selected_ids: set[str]` 存储在 AppContext 上（**H2: 与用户标签系统完全隔离**）
- [ ] `edit_translation` — 参数 `entry_id: str, new_translation: str, new_stage: int | None = None`。不传 new_stage 时保持现有 stage 不变（**H4: 不再硬编码 stage=2**）
- [ ] `set_stage` — 参数 `entry_ids: list[str], stage: int`，支持批量设置翻译阶段（**H3: 填补批量标记缺口**）
- [ ] 全部注册到 `editor` namespace，筛选工具 permission: `read`；编辑/标记工具 permission: `write`

## 数据流

```
filter_by_stage(stages=[0, 1])
    → @validate_params → @require_collection
    → ctx.set_filter(stage=[0, 1])
    → filter_changed.emit({stage: [0, 1], ...})
    → ToolResult.ok(f"已按阶段筛选: {len(stages)} 个阶段")

get_visible_entries(limit=50, offset=0)
    → @require_collection
    → 读取 ctx.filter_state + collection
    → 纯数据遍历: 对每个 entry 检查 stage/category/label/search 条件
    → 返回 ToolResult.ok(data={"entries": [...], "total_count": N, "truncated": True/False})
```

## 关键接口

```python
# tools/tool_editor.py

_PARAM_SCHEMAS = {
    "filter_by_stage": {
        "stages": {"type": "list", "required": True, "description": "ParaTranz stage 值列表: 0=未翻译 1=已翻译 2=有疑问 3=已检查 5=已审核 9=已锁定 -1=已隐藏"}
    },
    "filter_by_category": {
        "categories": {"type": "list", "required": True, "description": "分类名列表如 ['NPC_', 'INFO', 'BOOK']"}
    },
    "filter_by_label": {
        "label_names": {"type": "list", "required": True, "description": "标签名列表"}
    },
    "search_entries": {
        "query": {"type": "str", "required": True, "description": "搜索关键词"},
        "field": {"type": "str", "required": False, "description": "搜索字段: key/original/translation/all，默认 all"}
    },
    "get_visible_entries": {
        "limit": {"type": "int", "required": False, "description": "返回条数上限，默认 50，最大 200"},
        "offset": {"type": "int", "required": False, "description": "偏移量，默认 0"}
    },
}
```

## 实现步骤

### 步骤 1: 创建 `tool_editor.py` 模块骨架

**涉及文件**: `src/transbridge/smart_assistant/tools/tool_editor.py`（新建）

**实现要点**:
- 导入 `ToolResult`, `ToolRegistry`, `ToolSpec`, 装饰器
- 定义 `_PARAM_SCHEMAS` 字典（本 Story + 后续 Story 05/08 共用）

---

### 步骤 2-5: 实现 5 个筛选工具

**涉及文件**: 同上追加

**实现要点**:
- `filter_by_stage`: 校验 stage 值在合法范围 `{0,1,2,3,5,9,-1}` 内，调用 `ctx.set_filter(stage=stages)`
- `filter_by_category`: 直接从 collection 获取已知分类列表用于校验（可选，仅警告）
- `filter_by_label`: 映射 label_name → label_id（通过 `ctx._label_library` 查找）
- `search_entries`: 调用 `ctx.set_filter(search_query=query, search_field=field or "all")`
- `clear_all_filters`: 调用 `ctx.clear_filters()`，返回 `ToolResult.ok("已清除所有筛选条件")`

**边界条件**:
- stage 值超出范围 → `ToolResult.fail("无效的 stage 值: {value}，合法值: 0,1,2,3,5,9,-1")`
- 空列表 stages → 等同于清除 stage 筛选
- label_name 不存在 → `ToolResult.fail("标签不存在: {name}")`

---

### 步骤 6: 实现 `get_visible_entries` 纯数据过滤

**涉及文件**: 同上追加

**实现要点**:
- 读取 `ctx.filter_state` 获取当前筛选条件
- 遍历 `collection`，对每个 entry 检查：
  - stage 匹配（若 filter_state.stage 非空）
  - category 匹配（`entry.context` 前缀匹配任一 category）
  - label 匹配（`entry.id` 在 `ctx._entry_labels` 中匹配 label_ids）
  - 搜索匹配（`query` in entry.key/original/translation 按 search_field）
- 分页：应用 offset → limit（最大 200）
- 返回条目摘要：`{id, key, original, translation, stage, labels}`

**边界条件**:
- 无筛选条件 → 返回所有条目（分页）
- 匹配 0 条 → `ToolResult.ok(message="筛选匹配 0 条", data={"entries": [], "total_count": 0})`
- 截断时 → `truncated=True, message="结果已截断，显示前 200 条（共 {total} 条）"`

### 步骤 7: 注册所有工具

**涉及文件**: 同上追加

**注册代码**:
```python
def _register_editor_tools():
    ToolRegistry.register(ToolSpec(
        name="filter_by_stage", display_name="按阶段筛选",
        description="按翻译阶段筛选表格...",
        parameters=_PARAM_SCHEMAS["filter_by_stage"],
        execute=_tool_filter_by_stage,
        permission="read",
    ), namespace="editor")
    # ... 其余 5 个工具类似注册
```

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `smart_assistant/tools/tool_editor.py` | 新建 | 9 个工具（筛选5 + 编辑1 + 选择1 + 批量标记1 + 清筛选1）+ 注册 |

## 风险与注意事项

- **注意**: `get_visible_entries` 复用 `_filter_entries()` 公共函数（Story 01 提供），与其他 Story 口径一致
- **注意**: `select_entries` 使用 `ctx._selected_ids`（独立集合），不使用标签系统。该集合不持久化、不在 UI 显示，仅 Agent 会话内有效
- **注意**: `edit_translation` 的 new_stage 可选——不传保持原 stage，传 0-6 显式设置。Agent 翻译空条目时传 new_stage=1
- **注意**: `set_stage` 批量操作一次性更新所有传入 entry 的 stage，替代逐条 edit_translation 的低效模式
- **注意**: category 匹配使用 `entry.context.startswith(category + ":")` 或 `category + "_"` 前缀匹配
