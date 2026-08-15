# Story 03: 存为词典（从集合落地）

**所属方案**: `plans/translation-memory/plan.md`
**技术模块**: backend
**状态**: 已确认
**创建日期**: 2026-08-14

## 前置依赖

### 上游 Story
- Story 01（同 plan）：已完成 → 提供 `TranslationMemoryManager`

### 跨 Plan 依赖
- `converter/translation_entry_collection.py` → 复用 `TranslationEntryCollection`（`__iter__` 遍历，不修改）

### 引用的架构决策
- ADR-014 决策 1（原文→译文映射）、决策 2（精确匹配规范化）、决策 3.1（双层存储键表+文本表）

## 验收标准

- [ ] 提供「将 `TranslationEntryCollection` 的已翻译条目写入指定词典」接口
- [ ] 支持全量（整个集合）与选中子集两种粒度
- [ ] 写入时同时填充键表（complete_key=entry.id）与文本表（规范化原文=entry.original）
- [ ] 写入时允许指定目标词典（scope/scope_id）与词典标签
- [ ] 跳过空译文条目；复用现有集合遍历（`for e in collection`）

## 数据流

```
save_from_collection(collection, scope, scope_id, entry_ids, tags, source)
    │
    ├─ 遍历：全量 for e in collection；子集 collection.get(id) for id in entry_ids
    ├─ 过滤：跳过 translation 为空 / original 为空
    ├─ manager.add(
    │      complete_key = e.id,          # 键表键
    │      original     = e.original,    # 文本表键（规范化后）
    │      translation  = e.translation,
    │      scope/scope_id, tags, source)
    └─ 返回新增条数
```

## 关键接口

### 函数签名

```python
class TranslationMemoryManager:
    def save_from_collection(self, collection,
                             scope: str = "project", scope_id: str = "",
                             entry_ids: list[str] | None = None,
                             tags: list[str] | None = None,
                             source: str = "collection") -> int:
        """将集合已译条目写入词典，返回新增键数（键表+文本表去重后）"""
```

## 实现步骤

### 步骤 1: `save_from_collection`

**涉及文件**: `src/transbridge/translation_memory/manager.py`（修改）

**实现要点**:
- 遍历入口：全量 `for e in collection`（`TranslationEntryCollection.__iter__`）；子集 `collection.get(id)`（by key）
- `complete_key` 用 `entry.id`（即 `EditorID:FormID|index~context`）；`original` 用 `entry.original`
- 调 `manager.add(...)`，双表写入见 Story 01
- 返回「实际新增」——用 add 前 dict 长度的差累计

**边界条件**:
- collection 为 None → ValueError
- entry_ids 含不存在 id → `get` 返回 None，跳过
- 空译文/空原文 → 跳过（空原文仍可写键表，但本 Story 统一跳过简化）
- 重复保存 → 键表/文本表已存在，overwrite=False 不覆盖（返回计数不计重复）

**伪代码**:
```python
def save_from_collection(self, collection, scope="project", scope_id="",
                         entry_ids=None, tags=None, source="collection"):
    if collection is None:
        raise ValueError("collection 不能为 None")
    if entry_ids is not None:
        targets = [collection.get(i) for i in entry_ids]
        targets = [e for e in targets if e is not None]
    else:
        targets = list(collection)   # __iter__ 返回 entry values
    added = 0
    for e in targets:
        if not e.translation or not e.original:
            continue
        before = self._count_keys(scope, scope_id)
        self.add(e.id, e.original, e.translation, scope=scope,
                 scope_id=scope_id, tags=tags, source=source)
        after = self._count_keys(scope, scope_id)
        added += (after - before)
    return added
```

### 步骤 2: 新增计数辅助

**涉及文件**: `src/transbridge/translation_memory/manager.py`（修改）

**实现要点**:
- `_count_keys(scope, scope_id)` 返回 `len(key_entries) + len(text_entries)`（近似新键数；实际一条会写两表，计数语义为「新写入的键数」）

**边界条件**:
- 词典不存在 → 返回 0

**测试策略**:
- 单测：mock collection（含已译/未译/空原文条目），验证全量/子集、空译跳过、双表写入、返回计数、scope/tags 正确

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/translation_memory/manager.py` | 修改 | 新增 save_from_collection + _count_keys |
| `tests/test_translation_memory.py` | 修改 | 存词典测试 |

## 风险与注意事项

- **风险 1**: 依赖 `TranslationEntryCollection.__iter__` 是否真的返回 entry values → 已确认（`__iter__` 返回 `self._entries.values()`），安全
- **注意 1**: 子集 `entry_ids` 语义是「entry.id」还是「entry.key」——`get()` 按 key 查，需确认 entry_ids 传的是 key。因现行 `entry.id == entry.key`（见 translation_entry.py 注释「key 现在存储原来的id值」与「id==key」），实际等价，但编码时注释清楚
- **注意 2**: 双表写入导致一条产生 2 个"键"，返回计数可能被理解为"条数"；文档中明确「返回新增键数」而非条数
