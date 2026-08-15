# Story 08: 多词典组合查询与冲突仲裁（性能重点）

**所属方案**: `plans/translation-memory/plan.md`
**技术模块**: backend
**状态**: 已确认
**创建日期**: 2026-08-14

## 前置依赖

### 上游 Story
- Story 07（已完成）：定位键已改为 `mod_file_id`，`dictionaries` 返回 `dict[mod_file_id, Dictionary]`

### 跨 Plan 依赖
- 无新增外部依赖

### 引用的架构决策
- ADR-014 更新节（2026-08-14）：多词典全查兜底（同名 mod → project → global）、冲突可视化仲裁、`QueryResult.conflicts` 真正填充

## 验收标准

- [ ] `query` 改为多词典全查兜底：同名 mod 词典（最优先）→ 其余 project → 其余 global
- [ ] 键索引命中即停仅限「同名 mod 词典」内；跨词典文本命中收集候选后仲裁
- [ ] `QueryResult.conflicts` 真正填充（译文 + 来源词典 + 胜者），不再空转
- [ ] 仲裁规则：同名 mod > project > global；同级内命中计数高的优先（可配置）
- [ ] **性能约束**：全查兜底必须避免每次 query 线性扫全部词典——采用「按 mod_file_id 索引 + 惰性加载 + 命中即短路同名 mod」策略，全局词典文本索引预建合并索引或分级缓存；大词典（万级词条）查询耗时需在验收测试中量化
- [ ] `_normalize_cache` 加锁/按词典隔离，避免多词典并发串味

## 数据流

```
query(complete_key, original, context{mod_file_id, ...})
    │
    ├─ 构建激活集（惰性，不预载全部词典）:
    │    同名 mod 词典（O(1) 按 mod_file_id 定位）
    │    + 其余 project 词典（按 scope 遍历，惰性）
    │    + 其余 global 词典（按 scope 遍历，惰性）
    │
    ├─ ① 同名 mod 词典: 键命中 → 即停（EXACT/STALE，最可信，不收集候选）
    │
    ├─ ② 其余词典: 键命中 → 收集候选；文本命中 → 收集候选
    │
    ├─ ③ 仲裁: 同名mod > project > global；同级内 hits 高者优先
    │
    └─ QueryResult{translation, matched_scope, matched_via, match_status, conflicts}
         conflicts = [{translation, mod_file_id, scope, wins: bool}]
```

## 关键接口

### 数据结构

```python
@dataclass
class QueryContext:
    mod_file_id: str = ""          # 当前翻译的 mod 名（同名词典最高优先）
    # 其余 project/global 词典由 manager 内部按 scope 自动纳入（无需显式列表）

@dataclass
class Conflict:
    translation: str
    mod_file_id: str
    scope: str
    wins: bool                      # 是否仲裁胜出

@dataclass
class QueryResult:
    translation: str | None = None
    matched_scope: str = ""
    matched_via: str = ""           # "key" | "text"
    match_status: str = ""          # "EXACT" | "STALE" | ""
    conflicts: list[Conflict] = field(default_factory=list)  # 非空表示存在多译文冲突
```

### 函数签名

```python
def query(self, complete_key, original, context: QueryContext | None = None) -> QueryResult:
    """多词典全查兜底 + 冲突仲裁。"""

def _activation_order(self, mod_file_id: str) -> list[Dictionary]:
    """返回激活集：同名mod词典 → 其余project → 其余global（惰性，不强制加载全部）。"""

def _arbitrate(self, candidates: list[tuple[Dictionary, DictionaryEntry, str, str]]) -> QueryResult:
    """对候选按 同名mod > project > global，同级 hits 高者优先，填充 conflicts。"""
```

## 实现步骤

### 步骤 1: 激活集构建（惰性）

**涉及文件**: `src/transbridge/translation_memory/manager.py`（修改）

**实现要点**:
- 激活集 = 同名 mod 词典（`self._dicts.get(mod_file_id)`）+ 按 scope 分组的其余词典
- **惰性策略**：非预载全部——先查同名 mod，命中即短路返回；未命中的原文才涉及其余词典的文本索引
- scope 遍历顺序：project 优先于 global（仲裁优先级）

**边界条件**:
- `context.mod_file_id` 空 → 跳过同名 mod 词典，直接 project → global
- 同名 mod 词典不存在 → 直接进入其余词典
- 其余词典为 0 本 → 只查同名 mod

**伪代码**:
```python
def _activation_order(self, mod_file_id):
    order = []
    same = self._dicts.get(mod_file_id)
    if same:
        order.append(same)
    # 其余按 scope 分组，project 优先
    rest = [d for k, d in self._dicts.items() if k != mod_file_id]
    order += sorted(rest, key=lambda d: SCOPE_RANK.get(d.scope, 0), reverse=True)
    return order
```

### 步骤 2: 同名 mod 键命中即停 + 其余词典候选收集

**涉及文件**: `src/transbridge/translation_memory/manager.py`（修改）

**实现要点**:
- 先查同名 mod 词典：键命中 → 立即返回（EXACT/STALE）；文本命中 → 返回（不收集候选，因为同名 mod 最可信）
- 同名 mod 未命中 → 遍历其余词典，收集「键命中」与「文本命中」候选
- 键命中在其余词典内不再「即停」（因为可能有多个词典命中同一键，需仲裁）

**边界条件**:
- 其余词典键命中多个 → 全部计入 candidates
- 其余词典文本命中多个 → 全部计入 candidates
- 无任何命中 → 返回空 QueryResult

**伪代码**:
```python
def query(self, complete_key, original, context=None):
    ctx = context or QueryContext()
    # ① 同名 mod 词典（键命中即停）
    same = self._dicts.get(ctx.mod_file_id)
    if same:
        r = self._hit(same, complete_key, original)
        if r.translation:
            return r  # 同名 mod 最可信，直接返回
    # ② 其余词典收集候选
    candidates = []
    for d in self._activation_rest(ctx.mod_file_id):
        if complete_key and complete_key in d.key_index:
            candidates.append((d, d.entries[...], "key", status))
        nk = _normalize(original)
        if nk and nk in d.text_index:
            candidates.append((d, d.entries[...], "text", ""))
    # ③ 仲裁
    return self._arbitrate(candidates)
```

### 步骤 3: 冲突仲裁 + conflicts 填充

**涉及文件**: `src/transbridge/translation_memory/manager.py`（修改）

**实现要点**:
- 对 candidates 排序：同名 mod > project > global（`SCOPE_RANK`），同级按 `hits`（命中计数）降序
- 胜者为排序后第一项；其余候选译文不同的 → 计入 `conflicts`（`wins=False`）
- 译文相同的多条 → 不视为冲突，不重复计

**边界条件**:
- 仅 1 个候选 → 无冲突，conflicts 空
- 多候选译文全同 → 无冲突（去重）
- 多候选译文不同 → 全部入 conflicts，胜者 wins=True

**伪代码**:
```python
def _arbitrate(self, candidates):
    if not candidates:
        return QueryResult()
    # 排序：scope 优先级 → hits 降序
    candidates.sort(key=lambda c: (
        SCOPE_RANK.get(c[0].scope, 0),
        _idx_hits(c[0], c[1]),
    ), reverse=True)
    winner = candidates[0]
    conflicts = []
    seen_trans = {winner[1].translation}
    for d, e, via, status in candidates:
        if e.translation not in seen_trans:
            conflicts.append(Conflict(e.translation, d.mod_file_id, d.scope, wins=False))
            seen_trans.add(e.translation)
    return QueryResult(
        translation=winner[1].translation,
        matched_scope=winner[0].scope,
        matched_via=via,
        match_status=status,
        conflicts=conflicts,
    )
```

### 步骤 4: 性能优化（同名 mod 短路 + 缓存隔离）

**涉及文件**: `src/transbridge/translation_memory/manager.py`（修改）

**实现要点**:
- **同名 mod 短路**已由步骤 2 实现（命中即返回，不扫其余词典）——这是最核心的性能保障
- **`_normalize_cache` 加锁**：用 `threading.Lock` 保护，避免多线程读写竞争
- **分级缓存**：其余 global 词典的文本索引，可选预建「合并文本索引 → 来源词典」映射（惰性，首次查询时构建，LRU 上限）

**边界条件**:
- 缓存超阈值清空（沿用现有 100k 上限）
- 词典变更（add/save）后缓存失效

**伪代码**:
```python
_normalize_cache: dict[str, str] = {}
_normalize_lock = threading.Lock()

def _normalize(text):
    with _normalize_lock:
        if text not in _normalize_cache:
            _normalize_cache[text] = _normalize_text(text)
            if len(_normalize_cache) > 100_000:
                _normalize_cache.clear()
        return _normalize_cache[text]
```

**测试策略**:
- 单测：同名 mod 键命中即停（不扫其余）；其余词典多命中收集 + 仲裁胜者；conflicts 填充（译文不同则列出）；译文相同去重不离散；空结果。
- 性能测试：构造万级词典，量化 query 耗时（同名 mod 命中 O(1)、跨词典文本命中耗时在可接受范围），记录基线。

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/translation_memory/manager.py` | 修改 | QueryContext 改 mod_file_id、query 全查兜底+仲裁、conflicts 填充、缓存加锁 |
| `tests/test_translation_memory.py` | 修改 | 多词典查询/仲裁/冲突/性能基线测试 |

## 风险与注意事项

- **风险 1（性能）**: 全查兜底最坏情况 = 同名 mod 未命中 + 全部其余词典文本索引遍历。缓解：①同名 mod 命中即短路（覆盖绝大多数 mod 更新复译场景）；②其余词典惰性加载；③global 文本索引分级缓存。若仍不达标，回退为「仅同名 mod + project，不查 global」（降低跨 mod 复用之广度）。
- **风险 2**: 仲裁优先级「同级按 hits」——hits 是运行态计数，会随使用变化导致结果不稳定。缓解：hits 仅作 tie-breaker，主优先级仍为 scope；如需确定性，可改用「updated_at 更新者优先」。
- **注意 1**: `SCOPE_RANK` 之前定义了却没用，本次必须真正落地（`SCOPE_RANK = {project: 2, global: 1}`）。
- **注意 2**: `conflicts` 字段此前零赋值，本次需在 `ApplyResult.conflicts` 同步填充（套用时展示冲突），而非仅 `QueryResult`。
