# Story 06: 数据模型重构（一文件一 mod）

**所属方案**: `plans/translation-memory/plan.md`
**技术模块**: backend
**状态**: 已确认
**创建日期**: 2026-08-14

## 前置依赖

### 上游 Story
- Story 01-05（已实现）：现有 `Dictionary`/`DictionaryEntry`/`TranslationMemoryManager` 骨架，本次在其上重构字段

### 跨 Plan 依赖
- `converter/translation_entry.py` → `TranslationEntry.form_id_with_plugin`（已存在的持久化字段，供词典持久化复用）
- `persistence/_utils.py` → `atomic_write_json`/`validate_name`（保持不变）

### 引用的架构决策
- ADR-014 更新节（2026-08-14）：一文件一 mod、`.tbdict` 后缀、词条主键 `sha1(mod名|原文)` 不含 scope、单值 scope、`source_mod`/`form_id_with_plugin` 字段

## 验收标准

- [ ] `Dictionary` 字段重构为 `{ mod_file_id, scope(单值 project|global), entries, key_index, text_index }`，移除 `scope_id`
- [ ] `DictionaryEntry` 字段重构为 `{ translation, original, source_mod, form_id_with_plugin, imported_at, updated_at, tags }`，移除 `source`（被 `source_mod` 取代）
- [ ] 词条主键 `entry_id = sha1(mod_file_id | 原文)`，不含 scope，验证跨 scope 切换不换 ID
- [ ] 文件后缀 `.tbdict`（内容仍为 JSON，`schema_version` 保留），`to_dict`/`from_dict` 适配新字段
- [ ] scope 单值校验（project/global），保留 `VALID_SCOPES`

## 数据流

```
add(mod_file_id, complete_key, original, translation, scope, form_id_with_plugin, tags)
    │
    ├─ entry_id = sha1(mod_file_id | 原文)        ← 主键，不含 scope
    ├─ entries[entry_id] = DictionaryEntry(...)   ← 权威对象（译文唯一来源）
    ├─ key_index[complete_key] = {entry_id, hits}   ← 键索引
    └─ text_index[_normalize(original)] = {entry_id, hits}  ← 文本索引
         │
         ▼
save() → {mod_file_id}.tbdict（JSON，schema_version 保留）
load() → 扫描 *.tbdict，按 mod_file_id 重建词典
```

> scope 是「词典文件的单值属性标签」，不参与定位键、不参与词条主键、不参与文件名。

## 关键接口

### 数据结构

```python
@dataclass
class DictionaryEntry:
    translation: str = ""          # 译文
    original: str = ""             # 原始英文（供 EXACT/STALE 判定）
    source_mod: str = ""           # 来源 mod 名（取代旧 source）
    form_id_with_plugin: str = ""  # 完整 FormID|插件名（如 0001A2B3|MyMod.esp）
    imported_at: str = ""          # 首次入典时间
    updated_at: str = ""           # 最后变更时间
    tags: list[str] = field(default_factory=list)  # 词典标签（仅管理/筛选）

    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls, d: dict) -> "DictionaryEntry": ...

@dataclass
class Dictionary:
    mod_file_id: str = ""           # 来源 mod 名（定位键，取代 scope_id）
    scope: str = "global"           # 单值标签 project|global
    entries: dict[str, DictionaryEntry] = field(default_factory=dict)   # entry_id -> 权威对象
    key_index: dict[str, dict] = field(default_factory=dict)            # complete_key -> {entry_id, hits}
    text_index: dict[str, dict] = field(default_factory=dict)           # normalized -> {entry_id, hits}

    def __post_init__(self): ...     # scope 校验（VALID_SCOPES）
    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls, d: dict) -> "Dictionary": ...
```

### 函数签名

```python
def _entry_id(mod_file_id: str, original: str) -> str:
    """词条主键 = sha1(mod_file_id | 原文)，不含 scope。"""
    from hashlib import sha1
    return sha1(f"{mod_file_id}|{original}".encode("utf-8")).hexdigest()
```

## 实现步骤

### 步骤 1: 重构 `DictionaryEntry` 字段

**涉及文件**: `src/transbridge/translation_memory/model.py`（修改）

**实现要点**:
- 移除 `source` 字段，新增 `source_mod` 与 `form_id_with_plugin`
- `to_dict`/`from_dict` 对称适配：`source_mod`、`form_id_with_plugin` 均以空串默认
- 保留 `imported_at`/`updated_at`/`tags` 不变

**边界条件**:
- `from_dict` 读旧字段 `source` → 兼容映射到 `source_mod`（仅防御，不影响新写入）；读不到则空串
- `tags` None → 空 list（沿用现状）
- `form_id_with_plugin` 可为空（txt 等非 plugin 来源无此字段）

**伪代码**:
```python
@dataclass
class DictionaryEntry:
    translation: str = ""
    original: str = ""
    source_mod: str = ""
    form_id_with_plugin: str = ""
    imported_at: str = ""
    updated_at: str = ""
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data):
        return cls(
            translation=data.get("translation", "") or "",
            original=data.get("original", "") or "",
            source_mod=(data.get("source_mod") or data.get("source") or ""),
            form_id_with_plugin=data.get("form_id_with_plugin", "") or "",
            imported_at=data.get("imported_at", "") or "",
            updated_at=data.get("updated_at", "") or "",
            tags=list(data.get("tags") or []),
        )
```

### 步骤 2: 重构 `Dictionary` 字段与 scope 单值校验

**涉及文件**: `src/transbridge/translation_memory/model.py`（修改）

**实现要点**:
- 移除 `scope_id`，新增 `mod_file_id`（作为词典定位键）
- `scope` 保持单值二选一（project/global），`__post_init__` 校验保留 `VALID_SCOPES`
- `entries`/`key_index`/`text_index` 结构不变（单表权威对象 + 双索引保留）

**边界条件**:
- `scope` 非法 → ValueError（沿用现有信息）
- `mod_file_id` 空串 → 允许（load 前临时对象），但 save 时需非空（在 Story 07 定位逻辑中强制）
- `from_dict` 读旧字段 `scope_id` → 忽略（不迁移，旧数据弃置）

**伪代码**:
```python
@dataclass
class Dictionary:
    mod_file_id: str = ""
    scope: str = "global"
    entries: dict = field(default_factory=dict)
    key_index: dict = field(default_factory=dict)
    text_index: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.scope not in VALID_SCOPES:
            raise ValueError(f"非法 scope: {self.scope!r}")
```

### 步骤 3: 词条主键重定义 + 序列化适配

**涉及文件**: `src/transbridge/translation_memory/model.py`（修改）

**实现要点**:
- 新增模块级 `_entry_id(mod_file_id, original)` = `sha1(f"{mod_file_id}|{original}")`
- `Dictionary.to_dict` 输出 `schema_version`（保留）、`mod_file_id`、`scope`、`entries`、`key_index`、`text_index`
- `Dictionary.from_dict` 读入新字段

**边界条件**:
- 主键不含 scope → 同一 `(mod_file_id, 原文)` 在 project/global 之间切换 scope，entry_id 不变
- `original` 未规范化时直接参与 sha1（与 `_normalize` 区分：主键用原始原文保证稳定，规范化只用于文本索引匹配）

**伪代码**:
```python
def _entry_id(mod_file_id: str, original: str) -> str:
    from hashlib import sha1
    return sha1(f"{mod_file_id}|{original}".encode("utf-8")).hexdigest()
```

**测试策略**:
- 单测：`DictionaryEntry.to_dict/from_dict` 新字段往返；`Dictionary` scope 校验；`_entry_id` 不含 scope（改 scope 后主键不变）；`to_dict` 含 `mod_file_id` 与 `scope`、无 `scope_id`

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/translation_memory/model.py` | 修改 | DictionaryEntry 字段重构（source→source_mod + form_id_with_plugin）、Dictionary 字段重构（scope_id→mod_file_id）、新增 `_entry_id` |
| `tests/test_translation_memory.py` | 修改 | 适配新字段 + 主键 scope 解耦测试 + .tbdict 往返测试 |

## 风险与注意事项

- **风险 1**: 主键 `sha1(mod名|原文)` 用「原始原文」而非「规范化原文」参与哈希——若 mod 内原文带不同换行风格会导致主键不同。缓解：主键用「规范化原文」（`_normalize(original)`）而非原始原文，与文本索引一致；在 Story 08 编码时统一确认此点。
- **风险 2**: 旧 `source` 字段的兼容读取——虽旧数据整体弃置，但 `from_dict` 保留 `source → source_mod` 兜底防意外坏文件。
- **注意 1**: `mod_file_id` 作为词典定位键，同一 mod 只会有一本词典（同名即同词典），这符合「同名 mod 只存一本」的决策。
- **注意 2**: Story 06 只改 model 层字段与主键，manager 的定位逻辑（`(scope,scope_id)`→`mod_file_id`）留给 Story 07，避免本 Story 跨层改动过大。
