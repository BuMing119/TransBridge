# Story 24: Parser 工具副作用补全 — 解析结果落地

**Epic**: agent-tool-expansion
**对应需求**: FR9.12 — 解析工具副作用补全
**优先级**: P1
**状态**: 已方案
**创建日期**: 2026-05-18
**涉及文件**: `tools/tool_parser.py`（主修改）、`tools/base.py`（HITL 协议复用）、`tool_execution_handler.py`（HITL 回调接入）、`plans/agent-tool-expansion/plan.md`（范围更新）

## 验收标准

- [ ] 6 个工具（`parse_esp`/`parse_eet`/`parse_xt`/`parse_sst`/`import_json`/`import_strings`）新增 `action` 参数，可选值为 `create_slot`（默认）和 `append`
- [ ] `action=create_slot`：解析成功后创建 `CollectionSlot`，调用 `ctx.add_slot()` 注册并 `ctx.activate_slot()` 激活。slot key 为文件路径，label 为文件名（不含扩展名）
- [ ] `action=append`：解析出的条目合并到当前活跃集合。若无活跃集合，返回 `ToolResult.fail("当前无活跃集合，无法追加。请先创建或切换到一个集合。")`
- [ ] 同名 slot 已存在时，通过 HITL `confirm` 弹框询问覆盖或取消
- [ ] 副作用执行前通过 `PermissionGuard` 的 write 确认机制触发 HITL 弹框，显示操作摘要（文件名、action、预计条目数）
- [ ] 工具 permission 从 `read` 改为 `write`（产生副作用）
- [ ] `_PARAM_SCHEMAS` 补充 `action` 参数定义
- [ ] 无活跃集合时，LLM 仍可调用 `action=create_slot`（创建新槽位不依赖现有集合）
- [ ] 解析失败时不做任何副作用（现有行为不变）

## 实现步骤

### 步骤 1: 重构工具函数 — 保留解析结果

**文件**: `tools/tool_parser.py`

当前 6 个工具函数在解析后直接丢弃 collection 对象（`parse_esp` 解析了 `plugin.entries` 但不使用）。改造为返回解析结果供后续步骤使用。

每个工具函数改为统一模式：
```python
def _tool_parse_xxx(args: dict, ctx) -> ToolResult:
    path = args.get("path", "")
    action = args.get("action", "create_slot")
    # 1. 校验 path
    # 2. 解析 → 得到 collection / entries
    # 3. 根据 action 分流 → _execute_side_effect()
    # 4. 返回结果
```

**关键变更**：解析器调用后不丢弃结果，而是传入 `_execute_side_effect()`。

### 步骤 2: 实现副作用执行函数

**文件**: `tools/tool_parser.py`

```python
def _execute_side_effect(action, path, collection, ctx) -> ToolResult:
    """根据 action 执行副作用（创建 slot / 追加条目）。"""
    label = Path(path).stem
    if action == "create_slot":
        return _create_slot(path, label, collection, ctx)
    elif action == "append":
        return _append_to_collection(collection, ctx)
```

**`_create_slot(path, label, collection, ctx)`**:
1. 检查 `path in ctx.slots` → 如已存在，返回 HITL 请求（覆盖确认）
2. 创建 `CollectionSlot(label=label, collection=collection, esp_path=path, ...)`
3. `ctx.add_slot(path, slot)` 注册
4. `ctx.activate_slot(path)` 激活
5. 返回 `ToolResult.ok(f"已创建并激活集合「{label}」，共 {len(collection)} 条", data={"action": "create_slot", "label": label, "entry_count": len(collection), "activated": True})`

**`_append_to_collection(collection, ctx)`**:
1. 检查 `ctx.active_slot` 是否存在 → 不存在返回 fail
2. 调 `ctx.active_slot.collection.update_from_*()` 或等价合并逻辑
3. 返回 `ToolResult.ok(f"已追加 {added_count} 条到当前集合", data={"action": "append", "added_count": added_count, "total_count": len(ctx.active_slot.collection)})`

### 步骤 3: 接入 HITL 确认

**文件**: `tools/tool_parser.py` + `tool_execution_handler.py`

复用现有 `PermissionGuard` 的 write 权限确认机制。将 6 个工具的 permission 改为 `write` 后，框架层自动在 `before_execute` 中检查并弹确认框。

对于同名 slot 覆盖场景，两种方案：
- **方案 A（推荐）**：`_create_slot()` 检测到冲突时，直接返回 `ToolResult.fail("集合「{label}」已存在，请改用 append 或手动处理")`，让 LLM 自行处理（简单，不需要新 HITL 类型）
- **方案 B**：新增 HITL 类型 `slot_conflict`，框架处理

采用 **方案 A** — 利用 LLM 的推理能力处理冲突，不增加新 HITL 类型。

### 步骤 4: 更新 _PARAM_SCHEMAS 和工具注册

**文件**: `tools/tool_parser.py`

为 6 个工具的 schema 添加 `action` 参数：
```python
"parse_esp": {
    "path": {"type": "str", "required": True, "description": "ESP/ESM 文件路径"},
    "action": {"type": "str", "required": False, "description": "解析后操作: create_slot（创建新槽位并激活，默认）或 append（追加到当前活跃集合）"},
},
# ... 其余 5 工具相同
```

工具注册时 `permission` 从 `"read"` 改为 `"write"`（6 个工具全部）。

### 步骤 5: 更新 plan.md 范围声明

**文件**: `plans/agent-tool-expansion/plan.md`

将"范围外"中的「集合管理 CRUD（创建/移除/迁移源追加）」修改为：
```
- 集合管理 CRUD（移除 slot / 重命名 / 迁移源手动追加）。注：创建 slot 和追加条目已由 Story 24 移入范围内。
```

### 步骤 6: 验证

1. 单元测试：`action=create_slot` 正常创建、已存在时拒绝、`action=append` 正常追加、无集合时 fail
2. 集成测试：通过 LLM 对话调用 `parse_esp action=create_slot path=xxx.esp`，确认新 slot 出现在 `list_collections` 中
3. 权限测试：确认 write 权限触发 PermissionGuard 弹框

## 设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 同名 slot 冲突处理 | 返回 fail，不新增 HITL 类型 | 简单，利用 LLM 推理能力；LLM 收到 fail 后知道要换 action |
| HITL 确认 | 复用 PermissionGuard write 确认 | 已有机制，不需新增框架代码 |
| permission 级别 | `read` → `write` | 创建 slot / 追加条目是写操作，write 级别触发用户确认 |
| 无集合时 append | 返回 fail | 用户明确要求"禁止 append 选项"，LLM 收到 fail 后应改用 create_slot |
| slot key | 文件路径 | 与 UI 端 step1.py 一致（`ctx.add_slot(esp_path, slot)`） |
| append 合并方式 | 按 key 匹配覆盖 | 与 UI 端 import_json 行为一致 (`overwrite=True`) |

## 架构依赖

- **ADR-008**: 工具不直接依赖 UI，HITL 通过 PermissionGuard 协议
- **ADR-002**: Collection 数据中枢 + `ctx.add_slot()` / `ctx.activate_slot()` API
- **Story 01**: `HITLRequest`/`HITLResponse` 协议 + `PermissionGuard` 确认机制
- **Story 12**: 原 P2 解析工具实现（本 Story 在其基础上改造）
