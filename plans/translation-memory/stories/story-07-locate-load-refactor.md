# Story 07: 定位/命名/加载重构

**所属方案**: `plans/translation-memory/plan.md`
**技术模块**: backend
**状态**: 已确认
**创建日期**: 2026-08-14

## 前置依赖

### 上游 Story
- Story 06（已完成）：`Dictionary`/`DictionaryEntry` 字段重构完成，`mod_file_id` 成为词典定位键

### 跨 Plan 依赖
- `converter/translation_entry.py` → `TranslationEntry.form_id_with_plugin`（持久化进词典）
- `persistence/_utils.py` → `atomic_write_json`/`validate_name`

### 引用的架构决策
- ADR-014 更新节（2026-08-14）：定位键 `mod_file_id`、文件名 `{mod}.tbdict`、旧 `.json` 弃置、`source_mod` 从打开文件路径推断

## 验收标准

- [ ] manager 定位键从 `(scope, scope_id)` → `mod_file_id`（`_key`/`_dict`/`dictionaries` 全链路）
- [ ] 文件命名从 `{scope}__{scope_id}.json` → `{mod_file_id}.tbdict`
- [ ] `load()` 扫描 `*.tbdict`，以 `mod_file_id` 唯一索引；同名文件重复 → 硬校验抛错（不静默覆盖）
- [ ] `save_from_collection` 从打开文件路径推断 `source_mod`，并持久化 `form_id_with_plugin`
- [ ] 旧 `*.json` 词典文件弃置（不加载、不迁移）

## 数据流

```
save_from_collection(collection, mod_file_id, scope, ...)
    │  mod_file_id 从「打开插件文件路径」推断（SourcePath.stem 去扩展名）
    │
    ├─ 定位：_dict(mod_file_id)  →  词典（按 mod 名唯一）
    ├─ 每条词条：source_mod = mod_file_id；form_id_with_plugin = entry.form_id_with_plugin
    └─ save() → {mod_file_id}.tbdict

load(base_dir)
    ├─ 扫描 *.tbdict
    ├─ 每文件 from_dict → 取 d.mod_file_id
    ├─ 若 mod_file_id 已存在 → 抛错（同名词典重复）
    └─ *.json 旧文件 → 忽略（弃置）
```

## 关键接口

### 函数签名

```python
class TranslationMemoryManager:
    def _key(self, mod_file_id: str) -> str: ...            # 原 _key(scope, scope_id) 改单参
    def _dict(self, mod_file_id: str) -> Dictionary: ...    # 定位/新建词典
    def _file_for(self, mod_file_id: str) -> str: ...       # {mod_file_id}.tbdict
    def save(self, base_dir: Path | None = None) -> list[Path]: ...
    def load(self, base_dir: Path | None = None) -> int: ...
    def save_from_collection(self, collection, mod_file_id: str,
                             scope: str = "global", ...) -> int: ...
    @property
    def dictionaries(self) -> dict[str, Dictionary]: ...    # mod_file_id -> Dictionary
```

## 实现步骤

### 步骤 1: 定位键与文件命名重构

**涉及文件**: `src/transbridge/translation_memory/manager.py`（修改）

**实现要点**:
- `_key(scope, scope_id)` → `_key(mod_file_id)`（单参，返回 mod_file_id 字符串；保留非空校验）
- `_dict(scope, scope_id)` → `_dict(mod_file_id)`（内部 `self._dicts[mod_file_id]`）
- `_file_for(scope, scope_id)` → `_file_for(mod_file_id)` = `f"{validate_name(mod_file_id)}.tbdict"`
- `dictionaries` 属性返回 `dict[str, Dictionary]`（键为 mod_file_id）

**边界条件**:
- `mod_file_id` 空 → ValueError（与旧 project 强制 scope_id 同理）
- Windows 文件名非法字符 → `validate_name` 清洗（沿用现有，注意 mod 名可能含空格/点）

**伪代码**:
```python
def _key(self, mod_file_id: str) -> str:
    if not mod_file_id or not mod_file_id.strip():
        raise ValueError("mod_file_id 不能为空")
    return mod_file_id.strip()

def _file_for(self, mod_file_id: str) -> str:
    return f"{validate_name(mod_file_id)}.tbdict"
```

### 步骤 2: `save_from_collection` 推断 source_mod + 持久化 form_id_with_plugin

**涉及文件**: `src/transbridge/translation_memory/manager.py`（修改）

**实现要点**:
- `save_from_collection` 新增 `mod_file_id` 参数（取代 `scope/scope_id` 定位），从调用方传入（GUI 从打开文件路径推断）
- 遍历词条时，`add()` 传入 `source_mod=mod_file_id`、`form_id_with_plugin=e.form_id_with_plugin`
- `add()` 签名相应扩展 `source_mod`/`form_id_with_plugin` 参数

**边界条件**:
- `form_id_with_plugin` 为 None → 存空串
- `mod_file_id` 未传（旧调用方）→ 需要显式传，不设隐式默认（否则无法定位词典）

**伪代码**:
```python
def save_from_collection(self, collection, mod_file_id, scope="global",
                         entry_ids=None, tags=None) -> int:
    for e in targets:
        if not e.translation or not e.original:
            continue
        if e.stage in (9, -1):
            continue
        self.add(e.id, e.original, e.translation, mod_file_id=mod_file_id,
                 scope=scope, tags=tags,
                 source_mod=mod_file_id,
                 form_id_with_plugin=e.form_id_with_plugin or "")
```

### 步骤 3: `load()` 扫描 .tbdict + 硬校验 + 旧 json 弃置

**涉及文件**: `src/transbridge/translation_memory/manager.py`（修改）

**实现要点**:
- `load()` 仅 `glob("*.tbdict")`，忽略 `*.json`
- 每文件 `Dictionary.from_dict` → 以 `d.mod_file_id` 为键存入 `_dicts`
- 若 `mod_file_id` 已存在 → 抛错（同名词典重复，不静默覆盖）

**边界条件**:
- 目录不存在 → 返回 0
- `.tbdict` 损坏 → 保留现场（沿用 `.corrupt-{ts}` 改名）+ RuntimeError
- 同名 `.tbdict`（如 `A.tbdict` 与 `a.tbdict` 清洗后同键）→ 抛错

**伪代码**:
```python
def load(self, base_dir=None) -> int:
    target = base_dir or self._base_dir or self.default_dir()
    if not target.exists():
        return 0
    count = 0
    for f in sorted(target.glob("*.tbdict")):
        data = json.loads(f.read_text(encoding="utf-8"))
        d = Dictionary.from_dict(data)
        if d.mod_file_id in self._dicts:
            raise RuntimeError(f"词典 mod_file_id 重复: {d.mod_file_id}")
        self._dicts[d.mod_file_id] = d
        count += 1
    return count
```

**测试策略**:
- 单测：`_file_for` 生成 `.tbdict`；save/load 往返（`.tbdict`）；同名词典 load 抛错；`*.json` 被忽略；`save_from_collection` 持久化 source_mod 与 form_id_with_plugin

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/translation_memory/manager.py` | 修改 | 定位键/文件名/load/save_from_collection/add 全链路改 mod_file_id |
| `tests/test_translation_memory.py` | 修改 | 适配定位键改单参 + .tbdict 往返 + 同名抛错 + json 忽略 |

## 风险与注意事项

- **风险 1**: 定位键从双参 `(scope, scope_id)` 改单参 `mod_file_id` 影响所有 `_dict(key[0], key[1])` 调用点，需全链路同步，漏改会导致 KeyError。缓解：编码时全局 grep `scope_id` / `_key(` 逐一检查。
- **风险 2**: 同名 mod 词典唯一性——`validate_name` 清洗可能让 `A.esp` 与 `A ESP` 落同名。缓解：`validate_name` 后若冲突，load 抛错提示用户。
- **注意 1**: 旧 `*.json` 不加载，但目录里若残留旧文件会一直留着；可在 GUI 层提示，或保留现状（不主动删除用户文件）。
- **注意 2**: `save_from_collection` 的 `source` 参数语义被 `source_mod` 取代，旧 `source="collection"` 硬编码需一并删除。
