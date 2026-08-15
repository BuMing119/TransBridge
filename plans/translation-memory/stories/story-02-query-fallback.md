# Story 02: 查询与逐级兜底

**所属方案**: `plans/translation-memory/plan.md`
**技术模块**: backend
**状态**: 已确认
**创建日期**: 2026-08-14

## 前置依赖

### 上游 Story
- Story 01（同 plan）：已完成 → 提供 `Dictionary`/`DictionaryEntry`/`TranslationMemoryManager`

### 跨 Plan 依赖
- 无

### 引用的架构决策
- ADR-014 决策 1（键+文本分层）、决策 3.1（逐级兜底 project→game→global + 冲突处理）

## 验收标准

> ⚠ 注：本节及下文为议会评审前的旧设计（三级 scope、game→global、同级冲突仲裁、键表/文本表独立副本）。**最终设计以 plan.md 与 ADR-014 决策 3.1 为准**——两档 scope、键索引命中即停返回 EXACT/STALE、文本索引兜底。

- [ ] `query(complete_key, original, context) -> QueryResult`，键索引优先（命中即停），文本索引兜底
- [ ] 逐级兜底：当前 project 词典 → global 词典
- [ ] 键命中返回原文一致性判定：EXACT（原文一致）/ STALE（原文已变，需复核）
- [ ] 文本命中收集候选后按 scope 优先级（project 优先于 global）仲裁
- [ ] 命中计数落在索引值上，键/文本两条路径独立计数（不设单一 hit_count）

## 数据流

```
query(complete_key, original, context)
    │
    ├─ 依次尝试词典序列（由 context 决定顺序）：
    │    L = [project_dict, game_dict, global_dict]
    │
    ├─ 对每本词典：
    │    ├─ 键表：complete_key 命中？
    │    │    ├─ 命中且 entry 原译文属同一 key → 返回（键命中优先）
    │    │    └─ 未命中 → 文本表
    │    └─ 文本表：normalize(original) 命中？ → 记录候选译文 + 命中词典 scope
    │
    ├─ 收集多词典文本候选：
    │    ├─ 不同 scope → 取最高优先级（project>game>global）
    │    └─ 同级不同词典 → 保留最早（updated_at 最早）+ 记录冲突
    │
    └─ 命中 → hit_count++ → 返回译文；未命中 → None
```

## 关键接口

### 数据结构

```python
@dataclass
class QueryResult:
    translation: str | None = None
    matched_scope: str = ""       # 命中的 scope（project/game/global）
    matched_via: str = ""         # "key" | "text"
    conflicts: list[dict] = field(default_factory=list)  # 同级冲突记录

@dataclass
class QueryContext:
    project_id: str = ""   # 当前项目 scope_id
    game_id: str = ""      # 当前游戏 scope_id
```

### 函数签名

```python
class TranslationMemoryManager:
    def query(self, complete_key: str, original: str,
              context: QueryContext | None = None) -> QueryResult: ...

    def _query_dict(self, d: Dictionary, complete_key: str,
                    original_norm: str) -> tuple[str | None, str]: ...
        # 返回 (译文或 None, 匹配途径 key/text/"")

    def list_conflicts(self) -> list[dict]: ...   # 导出累计冲突
```

## 实现步骤

### 步骤 1: `query` 逐级兜底

**涉及文件**: `src/transbridge/translation_memory/manager.py`（修改）

**实现要点**:
- 由 `QueryContext` 构建词典序列 `[project, game, global]`（缺失的 scope 跳过）
- 每本词典先查键表（complete_key），命中即返回（键命中优先，无需看原文）
- 键表未命中再查文本表（normalize(original)），收集候选（译文 + scope + updated_at）

**边界条件**:
- context 为 None → 退回 global 词典（或全部词典按 global 兜底）
- complete_key 空 → 跳过键表，只查文本表
- 原文规范化后空 → 跳过文本表

**伪代码**:
```python
def query(self, complete_key, original, context=None):
    ctx = context or QueryContext()
    order = [("project", ctx.project_id), ("game", ctx.game_id), ("global", "")]
    result = QueryResult()
    candidates = []  # (scope, scope_id, translation, updated_at, via)
    for scope, sid in order:
        key = (scope, sid)
        if key not in self._dicts:
            continue
        d = self._dicts[key]
        if complete_key and complete_key in d.key_entries:
            e = d.key_entries[complete_key]
            e.hit_count += 1
            return QueryResult(e.translation, scope, "key", [])
        nk = _normalize_text(original)
        if nk and nk in d.text_entries:
            e = d.text_entries[nk]
            candidates.append((scope, sid, e.translation, e.updated_at, "text"))
    if not candidates:
        return result
    # 优先级取高 + 同级冲突
    chosen = max(candidates, key=lambda c: _scope_rank(c[0]))
    top_rank = _scope_rank(chosen[0])
    same_rank = [c for c in candidates if _scope_rank(c[0]) == top_rank]
    if len(same_rank) > 1:
        distinct = {c[2] for c in same_rank}
        if len(distinct) > 1:
            chosen = min(same_rank, key=lambda c: c[3])  # 保留最早
            result.conflicts = [{"scope": c[0], "scope_id": c[1], "translation": c[2]}
                                for c in same_rank]
    result.translation = chosen[2]
    result.matched_scope = chosen[0]
    result.matched_via = "text"
    return result
```

### 步骤 2: 键命中原文变化的「需复核」标记

**涉及文件**: `src/transbridge/translation_memory/manager.py`（修改）

**实现要点**:
- 键命中时，若调用方关心「原文是否变化」，由上层（Story 04 / fomod migrator）比较新旧原文决定是否套用
- 本层 query 不存储原文，因此「需复核」判定放在**上层**：上层拿 complete_key 键命中结果 + 自己知道的新原文，与词典记录的原文（如有）或与旧版集合原文比对
- 因此 `Dictionary.key_entries` 的 `DictionaryEntry` 需**额外保存 original 原文**（供上层比对），补入 Story 01 的 DictionaryEntry（增加 `original` 字段）

**边界条件**:
- 键命中但 entry.original 与传入 original 规范化后不等 → 上层标记「需复核」，本层仍返回译文（由上层决定是否用）

**测试策略**:
- 单测：键命中等价于文本命中优先；逐级兜底顺序；原文变化标记；多词典冲突优先级与同级保留最早；conflicts 记录

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/translation_memory/model.py` | 修改 | DictionaryEntry 增 `original` 字段 |
| `src/transbridge/translation_memory/manager.py` | 修改 | 新增 query/QueryResult/QueryContext/冲突记录 |
| `tests/test_translation_memory.py` | 修改 | 查询与兜底测试 |

## 风险与注意事项

- **风险 1**: 逐级兜底 + 键/文本双表 + 冲突，逻辑层次多易错 → 缓解：把「单词典查询」「多词典仲裁」拆成两个纯函数，单测覆盖
- **注意 1**: 键命中优先级高于文本命中——即使同级 text 命中更"近"，也以键命中为准（键=同 mod 精确，最可信）
- **注意 2**: `_scope_rank` 映射 project=3/game=2/global=1，取 max；同级用 updated_at 最早（min）裁决
