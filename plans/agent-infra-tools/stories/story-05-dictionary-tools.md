# Story 05: 词典套用与存词典 Agent 工具注册

**所属方案**: `plans/agent-infra-tools/plan.md`
**技术模块**: backend
**状态**: 已确认
**创建日期**: 2026-08-14

## 前置依赖

### 上游 Story
- Story 04（词条键对齐）：二者序贯使用——键对齐先（精确），词典套用后（兜底）

### 跨 Plan 依赖
- translation_memory（已实现）: TranslationMemoryManager.apply_to_collection() / save_from_collection()

### 引用的架构决策
- ADR-015: 词典套用/存词典 Agent 工具挂 translator namespace（补齐 Agent 工具缺口），复用 translation_memory 不重复实现
- ADR-014: 词典套用 = 键索引 + 文本索引两级匹配

## 验收标准

- [ ] 复用已有 translation_memory/manager.py 的 apply_to_collection/save_from_collection，不重复实现
- [ ] 新建 Agent 工具模块（或扩展 tool_translator.py），注册 apply_dictionary / save_dictionary 到 translator namespace
- [ ] apply_dictionary 调用 TranslationMemoryManager.apply_to_collection()，返回 ApplyResult 统计
- [ ] save_dictionary 调用 TranslationMemoryManager.save_from_collection()
- [ ] 两个工具 permission=write（可能修改集合/词典）

## 关键接口（复用，不改动）

```python
# translation_memory/manager.py（已有，复用）
def apply_to_collection(collection, context=None, overwrite=False) -> ApplyResult: ...
def save_from_collection(collection, mod_file_id="", scope=SCOPE_GLOBAL, entry_ids=None, tags=None) -> int: ...
def query(e.id, original, context) -> QueryResult: ...  # 注意用 e.id

# 新增 Agent 工具
def _tool_apply_dictionary(args: dict, ctx) -> ToolResult: ...
def _tool_save_dictionary(args: dict, ctx) -> ToolResult: ...
```

## 数据流

```
apply_dictionary(args, ctx) → manager.apply_to_collection(collection, context) → ApplyResult → ToolResult(data=统计)
save_dictionary(args, ctx) → manager.save_from_collection(collection, mod_file_id, scope) → int → ToolResult
```

## 实现步骤

### 步骤 1: tool_translator.py 追加词典工具

**涉及文件**: `src/transbridge/smart_assistant/tools/tool_translator.py`（修改）

**实现要点**:
- apply_dictionary: 获取当前 collection（ctx.app_context），调 apply_to_collection，返回 applied/key_hits/text_hits/misses/needs_review/conflicts 统计
- save_dictionary: 参数含 mod_file_id/scope/entry_ids，调 save_from_collection，返回新增条目数
- 两个工具挂 translator namespace，@require_collection 装饰器，permission=write

**边界条件**:
- 无集合加载 → @require_collection 自动返回失败
- 词典目录为空 → apply 返回全 misses

### 步骤 2: 确认 id/key 语义一致性

**实现要点**: 若 S04 确认应统一匹配键，本 Story 的 apply_dictionary 也需与此一致（translation_memory 当前用 e.id，若需改 key 则 manager 层统一改 + 测试回归）

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| src/transbridge/smart_assistant/tools/tool_translator.py | 修改 | 追加 apply_dictionary/save_dictionary |
| tests/smart_assistant/tools/test_dictionary_tools.py | 新建 | 单测 |

## 风险与注意事项

- 注意: 纯 Python 能力复用 translation_memory，本 Story 仅做 Agent 注册，不要重复实现匹配逻辑
- 注意: ApplyResult 含 conflicts（冲突候选），Agent 工具应把 conflicts 摘要放进 ToolResult.data 供 LLM 后续仲裁