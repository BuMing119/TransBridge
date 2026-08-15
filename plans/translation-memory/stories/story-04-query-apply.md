# Story 04: 套用到集合与统计

**所属方案**: `plans/translation-memory/plan.md`
**技术模块**: backend
**状态**: 已确认
**创建日期**: 2026-08-14

## 前置依赖

### 上游 Story
- Story 01/02/03（同 plan）：已完成 → 提供 `TranslationMemoryManager.query`

### 跨 Plan 依赖
- `converter/translation_entry_collection.py` → 复用 `TranslationEntryCollection`（套用目标）

### 引用的架构决策
- ADR-014 决策 1（键+文本分层）、决策 3.1（逐级兜底）

## 验收标准

> ⚠ 注：本节残留旧设计（「同级冲突」「单一 hit_count」）。最终设计以 plan.md 与 ADR-014 为准。

- [ ] `apply_to_collection(collection, context) -> ApplyResult`，遍历集合按词典补空译文
- [ ] 键索引优先匹配（entry.id），键未命中再走文本索引（entry.original）
- [ ] 仅填补空译文（overwrite 默认 False）；排除 stage==9/-1；命中回填对应索引 hits
- [ ] 套用结果统计（键命中数/文本命中数/未命中数/实际填充数）+ needs_review（键命中但 STALE 的条目）

## 数据流

```
apply_to_collection(collection, context)
    │
    ├─ 遍历 collection，仅处理 translation 为空（或 overwrite=True）的条目
    ├─ manager.query(entry.id, entry.original, context) → QueryResult
    │    ├─ translation 非空 → entry.translation = 结果；统计 matched_via
    │    ├─ conflicts 非空 → 累计到报告
    │    └─ translation 空 → miss++
    └─ 返回 ApplyResult(key_hits, text_hits, misses, applied, conflicts)
```

## 关键接口

### 数据结构

```python
@dataclass
class ApplyResult:
    key_hits: int = 0     # 键表命中的条数
    text_hits: int = 0    # 文本表命中的条数
    misses: int = 0       # 未命中的条数
    applied: int = 0      # 实际被填充译文的条数
    conflicts: list[dict] = field(default_factory=list)  # 冲突记录聚合
```

### 函数签名

```python
class TranslationMemoryManager:
    def apply_to_collection(self, collection,
                            context: QueryContext | None = None,
                            overwrite: bool = False) -> ApplyResult: ...
```

## 实现步骤

### 步骤 1: `apply_to_collection`

**涉及文件**: `src/transbridge/translation_memory/manager.py`（修改）

**实现要点**:
- 遍历 `for e in collection`，仅处理 `translation` 为空（或 overwrite=True）的条目
- 调 `query(e.id, e.original, context)`，据 `matched_via` 分计 key_hits/text_hits
- 命中 → `e.translation = result.translation`，`applied += 1`；`result.conflicts` 聚合进 `ApplyResult.conflicts`
- 未命中 → `misses += 1`
- **不改 stage**（套用仅填补译文，stage 语义留平台同步，参考 FR2.5）

**边界条件**:
- collection 为 None → ValueError
- 空原文 + 空 id → 跳过（无法匹配）
- overwrite=True 也覆盖已有译文（对命中者）
- 键命中但原文变化（需复核）→ 本 Story 默认仍套用键译文（因键=同 mod 精确），「原文变化复核」留给 fomod migrator 上层；如需在词典层标记，扩展返回字段

**伪代码**:
```python
def apply_to_collection(self, collection, context=None, overwrite=False):
    if collection is None:
        raise ValueError("collection 不能为 None")
    r = ApplyResult()
    for e in collection:
        if (e.translation and not overwrite) or (not e.id and not e.original):
            continue
        res = self.query(e.id, e.original, context)
        if res.translation:
            e.translation = res.translation
            r.applied += 1
            if res.matched_via == "key":
                r.key_hits += 1
            else:
                r.text_hits += 1
            r.conflicts.extend(res.conflicts)
        else:
            r.misses += 1
    return r
```

### 步骤 2: 冲突报告导出

**涉及文件**: `src/transbridge/translation_memory/manager.py`（修改）

**实现要点**:
- 冲突在 query 阶段已记录（Story 02），apply 聚合到 `ApplyResult.conflicts`
- 提供 `manager.list_conflicts()` 返回累计冲突（本次先聚合到 ApplyResult，累计列表后续增强）

**测试策略**:
- 单测：套用填补空译、不覆盖已有译（overwrite=False）、overwrite=True 覆盖、key/text 命中分类统计、miss 统计、conflicts 聚合

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/translation_memory/manager.py` | 修改 | 新增 apply_to_collection + ApplyResult |
| `tests/test_translation_memory.py` | 修改 | 套用测试 |

## 风险与注意事项

- **风险 1**: 误覆盖人工译文 → 缓解：默认 overwrite=False
- **注意 1**: stage 不变更（FR2.5 语义），仅填补 translation 字段
- **注意 2**: 「键命中但原文变化」的复核是 fomod 迁移场景的职责，词典层 apply 保持简单（键命中即套），避免语义过载
