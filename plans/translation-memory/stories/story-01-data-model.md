# Story 01: 词典库与双层存储数据模型

**所属方案**: `plans/translation-memory/plan.md`
**技术模块**: backend
**状态**: 已确认
**创建日期**: 2026-08-14

## 前置依赖

### 上游 Story
- 无（本 Epic 首个 Story）

### 跨 Plan 依赖
- 无外部 plan 依赖

### 引用的架构决策
- ADR-014 决策 3（独立 `translation_memory/` 包）、决策 3.1（多词典库 + 双层存储 + 逐级兜底）、决策 2（精确匹配规范化）

## 验收标准

> ⚠ 注：本节及下文部分内容为议会评审前的旧设计（三级 scope、键表/文本表双层独立副本）。**最终设计以 `plans/translation-memory/plan.md` 与 `docs/adr/014-fomod-translation-memory.md` 决策 3.1 为准**——两档 scope（project/global）+ 单表权威对象 `entries` + 键索引 `key_index` + 文本索引 `text_index`（命中计数落在索引值）。

- [ ] 定义 `Dictionary`（一本词典）：`scope`（project/global）、`scope_id`、`entries`（权威对象表）、`key_index`、`text_index`
- [ ] 定义 `DictionaryEntry`（权威对象）：译文、来源、导入时间、更新时间、词典标签（不含 hit_count）
- [ ] 定义 `TranslationMemoryManager`：管理多本词典、按 `(scope, scope_id)` 定位、`add()`/`save()`/`load()`/`merge()`
- [ ] 原文键规范化复用 `_normalize_text`（统一换行 + 去首尾空白，不剥离游戏标记）
- [ ] JSON 持久化往返无损（含中文文本，`ensure_ascii=False`，顶层 schema_version，原子写）

## 数据流

```
add(scope, scope_id, complete_key, original, translation, tags, source)
    │
    ├─ manager 定位/创建词典（scope+scope_id）
    ├─ 键表：dict[complete_key] = DictionaryEntry(translation,...)
    ├─ 文本表：dict[_normalize_text(original)] = DictionaryEntry(...)
    │
    ▼
save(path) ──► JSON（每本词典一个 section 或一个文件）
load(path) ──► 恢复 manager 的多本词典
```

## 关键接口

### 数据结构

```python
@dataclass
class DictionaryEntry:
    translation: str = ""
    source: str = ""
    hit_count: int = 0
    updated_at: str = ""
    tags: list[str] = field(default_factory=list)
    # 注意：键表与文本表各自的 dict 键（complete_key / 规范化原文）决定归属，
    #      条目本身的 complete_key/original 原值可选择性保留（见步骤 1 边界说明）

    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls, d: dict) -> "DictionaryEntry": ...

@dataclass
class Dictionary:
    scope: str = "global"        # "project" | "game" | "global"
    scope_id: str = ""           # 如 "legacy-dbm" / "skyrim_se"；global 为空
    key_entries: dict[str, DictionaryEntry] = field(default_factory=dict)   # complete_key -> entry
    text_entries: dict[str, DictionaryEntry] = field(default_factory=dict)  # normalized original -> entry

    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls, d: dict) -> "Dictionary": ...
```

### 函数签名

```python
class TranslationMemoryManager:
    def __init__(self, base_dir: Path | None = None) -> None: ...
    def _dict(self, scope: str, scope_id: str = "") -> Dictionary: ...  # 定位或新建
    def add(self, complete_key: str, original: str, translation: str,
            scope: str = "project", scope_id: str = "",
            tags: list[str] | None = None, source: str = "",
            overwrite: bool = False) -> None: ...
    def load(self, base_dir: Path) -> None: ...
    def save(self, base_dir: Path | None = None) -> None: ...
    def merge(self, other: "TranslationMemoryManager") -> int: ...
    @property
    def dictionaries(self) -> dict[tuple[str, str], Dictionary]: ...
```

## 实现步骤

### 步骤 1: 数据类 `DictionaryEntry` 与 `Dictionary`

**涉及文件**: `src/transbridge/translation_memory/model.py`（新建）

**实现要点**:
- `DictionaryEntry` 只存译文+元数据，键（complete_key / 原文）由所属 dict 的 key 承载
- `Dictionary` 双层 dict：`key_entries`、`text_entries`
- `to_dict`/`from_dict` 对称；中文不转义由 JSON 层处理

**边界条件**:
- `scope` 非法值 → 校验（限定 project/game/global），非法抛 ValueError
- `from_dict` 缺 key_entries/text_entries → 空 dict
- `tags` None → 空 list

**伪代码**:
```python
@dataclass
class Dictionary:
    scope: str = "global"
    scope_id: str = ""
    key_entries: dict = field(default_factory=dict)
    text_entries: dict = field(default_factory=dict)
    def to_dict(self):
        return {"scope": self.scope, "scope_id": self.scope_id,
                "key_entries": {k: v.to_dict() for k, v in self.key_entries.items()},
                "text_entries": {k: v.to_dict() for k, v in self.text_entries.items()}}
```

### 步骤 2: `TranslationMemoryManager` 与定位

**涉及文件**: `src/transbridge/translation_memory/manager.py`（新建）

**实现要点**:
- `_dict(scope, scope_id)` 用 `(scope, scope_id)` 作为字典库 key 定位，不存在则新建
- `add` 同时写 key_entries（complete_key）与 text_entries（`_normalize_text(original)`），overwrite=False 时已存在的键不覆盖
- `_normalize_text` 复用 `converter.translation_entry._normalize_text`
- `merge` 遍历对方词典逐键 add

**边界条件**:
- 空原文（规范化后 ""）→ 只写键表（若 complete_key 非空），文本表跳过
- 空译文 → 跳过，不写
- 键表与文本表 key 冲突 → 独立 dict，互不影响

**伪代码**:
```python
def add(self, complete_key, original, translation, scope="project", scope_id="", ...):
    if not translation:
        return
    d = self._dict(scope, scope_id)
    e = DictionaryEntry(translation=translation, tags=list(tags or []), source=source, updated_at=now())
    if complete_key:
        if complete_key not in d.key_entries or overwrite:
            d.key_entries[complete_key] = e
    nk = _normalize_text(original)
    if nk:
        if nk not in d.text_entries or overwrite:
            d.text_entries[nk] = e
```

### 步骤 3: JSON 持久化

**涉及文件**: `src/transbridge/translation_memory/manager.py`（修改）

**实现要点**:
- `save(base_dir)`：每本词典写一个文件 `{scope}_{scope_id or "global"}.json`（或单库多 section，本次按 scope 分子文件）；`json.dump(..., ensure_ascii=False, indent=2)`
- `load(base_dir)`：扫描目录下词典 json 读回
- 文件布局：`data/translation_memory/{scope}/{scope_id}.json` 或 `data/translation_memory/{scope}__{scope_id}.json`（编码时确定，保持简单）

**边界条件**:
- base_dir 不存在 → 创建
- 词典文件损坏 → 抛异常并传递（不静默吞）
- scope_id 为空的 global 词典 → 文件名 `global.json`

**测试策略**:
- 单测：add 双表写入、normalize 生效、scope 校验、to_dict/from_dict 往返、save/load 往返（含中文）、overwrite 语义

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/translation_memory/__init__.py` | 新建 | 包导出 |
| `src/transbridge/translation_memory/model.py` | 新建 | DictionaryEntry + Dictionary |
| `src/transbridge/translation_memory/manager.py` | 新建 | TranslationMemoryManager |
| `tests/test_translation_memory.py` | 新建 | 单元测试 |

## 风险与注意事项

- **风险 1**: `_normalize_text` 是私有函数，跨包引用不优雅 → 缓解：本地浅封装 `_norm()`，加注释；后续可上移公共 util
- **注意 1**: 键表与文本表存的是「同一个 DictionaryEntry 引用」还是「两个副本」——建议同日期的两个独立 entry（简单），后续再优化共享
- **注意 2**: `ensure_ascii=False` 必须设置，否则中文变 `\uXXXX`
