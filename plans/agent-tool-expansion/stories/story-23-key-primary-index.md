# Story 23: TranslationEntry.key 升为主索引

**Epic**: agent-tool-expansion
**优先级**: P0
**风险**: 高（改核心数据模型）
**依赖**: ADR-002 更新 2026-05-18
**架构决策**: 主索引 id → key，保留 _id_index 辅助索引
**状态**: 已方案

## 范围

将 `TranslationEntryCollection` 的主索引从 `entry.id` 切换为 `entry.key`，工具层参数语义同步更新。

## 子 Story

### Story 23a: Collection 索引改造（阻塞级）

**依赖**: 无
**涉及文件**: `src/transbridge/converter/translation_entry_collection.py`

**验收标准**:
- [ ] `_entries: dict[str, TranslationEntry]` — key 由 `entry.id` 改为 `entry.key`
- [ ] `_key_index` 更名为 `_id_index: dict[str, TranslationEntry]`（辅助索引，保留供内部合并逻辑）
- [ ] `get(key)` — 主查找，按 key 索引
- [ ] `get_by_id(id)` — 新增，按 id 辅助查找
- [ ] `get_by_key(key)` — 标记 deprecated，转发到 `get(key)`
- [ ] `add(entry)` — 主索引 key 由 `entry.key` 构建
- [ ] `remove(key)` — 按 key 移除
- [ ] `__contains__(key)` — 按 key 检查
- [ ] `add_many(entries)` — 同步调整
- [ ] 所有内部方法（`update_from_eet_xml`/`apply_xt_entries`/`apply_sst_entries` 等）通过 `_id_index` 反查

**实现步骤**:
1. 重命名 `_key_index` → `_id_index`，`get_by_key` 改为 deprecated 转发到 `get`
2. `add()` — `self._entries[entry.key]` 替代 `self._entries[entry.id]`，同步构建 `_id_index[entry.id]`
3. `get()` — 按 key 查找
4. `get_by_id()` — 新增，按 id 查找（供 EET/XT/SST apply 内部逻辑）
5. `remove()` / `__contains__()` — 改为按 key
6. 内部 apply 方法 — `collection.get(eid)` 调用点中，若 eid 是 id 格式 → 改为 `get_by_id(eid)`
7. 运行 converter 单元测试确认

### Story 23b: 工具 + get_visible_entries 适配

**依赖**: 23a（Collection 新 API 可用）
**涉及文件**:
- `src/transbridge/smart_assistant/tools/tool_editor.py`
- `src/transbridge/smart_assistant/tools/tool_translator.py`
- `src/transbridge/smart_assistant/tools/tool_writer.py`
- `src/transbridge/smart_assistant/tools/tool_parser.py`
- `src/transbridge/smart_assistant/tools/tool_proofreader.py`
- `src/transbridge/smart_assistant/tools/tool_paratranz.py`
- `src/transbridge/smart_assistant/tools/tool_default.py`

**验收标准**:
- [ ] 所有 `collection.get(entry_id)` 调用点确认传入的是 key 值
- [ ] `set_stage` / `edit_translation` / `select_entries` 等按 entry_ids 操作的工具 — 确保传递 key
- [ ] `get_visible_entries` 返回格式中 `key` 字段突出（LLM 应使用 key 作为标识符）
- [ ] `manage_entry_labels` — entry_ids 语义更新
- [ ] tool_parser 中所有 `collection.add()` 调用确认 key 正确填充
- [ ] 所有 45 非废弃工具注册不受影响（只改描述和参数语义，不改注册结构）

**实现步骤**:
1. `get_visible_entries` — 返回 dict 中将 `key` 置为首个字段，添加注释标注为 LLM 标识符
2. 逐工具检查 `collection.get(eid)` — 确认 eid 来源是 `get_visible_entries` 返回的 key
3. `_resolve_label_id` — 确认 label 查找逻辑兼容
4. 工具描述适配（S22 中完成，此处只需确认参数语义正确）

### Story 23c: 解析层 + 测试全量适配

**依赖**: 23a
**涉及文件**:
- `src/transbridge/parser/` — 所有 parser 模块中 `collection.add()` 调用
- `tests/converter/test_translation_entry_collection.py`
- `tests/test_agent_tool_integration.py`
- `tests/smart_assistant/test_tool_consolidation.py`

**验收标准**:
- [ ] 所有 parser 模块 `add(entry)` — 确认 `entry.key` 已正确填充
- [ ] converter 单元测试 — 新增 key 主索引查找测试
- [ ] 集成测试 — 所有 entry_id/entry_ids 引用改为 key 值
- [ ] 回归测试 — 现有 120 测试保持通过（适配后）

**实现步骤**:
1. parser 模块审查 — 确认每个 `TranslationEntry` 构造时 `key` 字段赋值
2. 新建 `test_translation_entry_collection.py` — key 索引查找/唯一性/双索引一致性
3. 适配 `test_agent_tool_integration.py` — MockAppContext 的 get() 按 key
4. 适配 `test_tool_consolidation.py` — entry_ids 断言改为 key 值
5. 运行全量测试 `pytest tests/` 确认通过

## 架构依赖

- **ADR-002** 更新 2026-05-18: Collection 主索引 id → key，保留 _id_index 辅助索引

## 风险与回退

| 风险 | 缓解 |
|------|------|
| 内部 apply 方法依赖 id 格式解析 | `_id_index` 保留，apply 逻辑不变 |
| 工具调用方传 id 导致 get() 返回 None | 23b 逐工具审查调用点 |
| key 值重复（理论上不应出现） | add() 日志警告覆盖 |
| 测试大面积断裂 | 23c 专门处理，分步适配 |
