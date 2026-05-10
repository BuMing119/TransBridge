# converter 模块

## 职责

翻译条目的核心数据结构与集合管理，是整个系统的数据中枢。

所有解析器（ESP/EET/XT）的输出最终都转换为 `TranslationEntry`，所有后续处理（编辑、导出、AI翻译、ParaTranz同步）都基于 `TranslationEntryCollection`。

---

## 文件清单

| 文件 | 职责 |
|------|------|
| `translation_entry.py` | 翻译条目数据类 |
| `translation_entry_collection.py` | 条目集合管理 |
| `translation_entry_collection_export.py` | 集合导出工具（分类导出JSON） |
| `context_categories.py` | 上下文类型分类常量（供AI翻译批次规划、导出分文件复用） |
| `__init__.py` | 模块导出 |

---

## 核心类

### TranslationEntry

**路径**: `src/transbridge/converter/translation_entry.py`

翻译条目的最小数据单元，使用 `@dataclass` 定义。

#### 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | `str` | 唯一标识，格式详见下文 |
| `key` | `str` | 与 id 相同（历史兼容，避免破坏现有序列化数据） |
| `original` | `str` | 原文 |
| `translation` | `str` | 译文 |
| `stage` | `int` | 翻译状态：`0`=未翻译，`1`=已翻译 |
| `context` | `str` | 上下文，格式详见下文 |
| `string_id` | `int \| None` | 本地化插件的字符串ID，用于精确匹配 strings 文件；非本地化插件为 None |
| `form_id_with_plugin` | `str \| None` | 完整的 FormID\|BaseRecordPlugin 格式，用于 DSD 导出 |
| `dsd_type` | `str` | DSD 类型格式，空格分隔（如 `"NPC_ FULL"`） |
| `dsd_index` | `int` | 原始索引，用于 DSD 索引类型导出，默认为 1 |
| `editor_id` | `str` | 原始 editor_id，用于 DSD GMST 类型导出 |

#### ID 格式

```
{editor_id}:{form_id}|{index}~{TYPE:FIELD}
```

| 部分 | 说明 | 示例 |
|------|------|------|
| `editor_id` | 记录的 Editor ID，可能为 `None` | `MyNPC` |
| `form_id` | FormID 十六进制字符串 | `000123AB` |
| `index` | 同一记录内的字段索引（从1开始） | `1` |
| `TYPE:FIELD` | 记录类型和字段名 | `NPC_:FULL` |

**示例**：
- NPC名称: `MyNPC:000123AB|1~NPC_:FULL`
- 对话文本: `QuestName:000456CD|1~INFO:NAM1|000789EF`（含quest_formid）
- EET条目: `EditorID:000123AB|1~GRUP:CHAMP`

#### Context 格式

```
{TYPE:FIELD}|{extra_info}
```

| TYPE | FIELD | extra_info | 说明 |
|------|-------|------------|------|
| `NPC_` | `FULL`/`SHRT` | 无 | NPC名称/短名 |
| `INFO` | `NAM1` | quest_formid | 对话文本，含所属任务ID |
| `DIAL` | `FULL` | quest_formid | 对话主题，含所属任务ID |
| `BOOK` | `FULL`/`DESC` | 无 | 书名/内容 |
| `QUST` | `FULL`/`NNAM` | 无 | 任务名/日志 |
| ... | ... | ... | 其他类型见 `context_categories.py` |

**关键点**：INFO/DIAL 类型的 context 包含 quest_formid，用于 AI 翻译按任务分组。

#### 关键方法

| 方法 | 说明 |
|------|------|
| `create_from_eet_entry(eet_entry)` | 从 EET_Entry 创建实例 |
| `create_from_plugin_entry(ps)` | 从 PluginStringWithContext 创建实例 |
| `try_update_from_xt(entry, xt)` | 尝试用 XT_Entry 更新，返回新实例或 None |
| `to_dict()` | 序列化为 dict |
| `from_dict(data)` | 从 dict 反序列化 |
| `to_dsd_dict()` | 导出为 DSD 格式 dict |
| `from_dsd_dict(data)` | 从 DSD 格式 dict 创建实例 |

#### try_update_from_xt 匹配逻辑

XT 工具对缺 EditorID 的记录处理方式不固定，`list_id` 与 `edid` 格式无严格绑定关系。匹配时检查三种候选形式：

```python
valid_edids = {id_left, id_right, f"[{id_right}]"}
# id_left = entry.id 冒号左侧（editor_id）
# id_right = entry.id 冒号与竖线之间（bare form_id）
# [id_right] = form_id 带方括号
```

`rec` 字段与 `entry.context` 比较时取 context 基础部分（去掉 INFO/DIAL 的 `|quest_formid` 后缀）：

```python
context_base = entry.context.split("|")[0]
if xt.rec != context_base:
    return None
```

#### 创建流程

```
EET_Entry ──────────────────────> TranslationEntry.create_from_eet_entry()
                                        │
PluginStringWithContext ────────────────> TranslationEntry.create_from_plugin_entry()
                                        │
XT_Entry ───────────────────────────────> TranslationEntry.try_update_from_xt()
                                    (仅更新，不创建)
```

---

### TranslationEntryCollection

**路径**: `src/transbridge/converter/translation_entry_collection.py`

管理多个 `TranslationEntry` 的集合，是系统唯一的数据容器。

#### 内部结构

```python
class TranslationEntryCollection:
    _entries: dict[str, TranslationEntry]    # id → entry 主索引
    _key_index: dict[str, TranslationEntry]  # key → entry 辅助索引
```

双索引设计支持两种查找方式，兼容历史数据。

#### 基本操作

| 方法 | 说明 |
|------|------|
| `add(entry, overwrite=True)` | 添加/更新条目（**注意：无独立 update 方法**） |
| `get(entry_id)` | 按 id 获取 |
| `get_by_key(key)` | 按 key 获取 |
| `remove(entry_id)` | 按 id 删除 |
| `filter(predicate)` | 条件过滤，返回列表 |

#### 批量操作

| 方法 | 说明 |
|------|------|
| `add_many(entries, overwrite=True)` | 批量添加 |
| `merge(other, overwrite=True)` | 合并另一集合 |

#### 文件导入

| 方法 | 说明 |
|------|------|
| `from_eet_xml(path)` | 从 EET XML 文件导入（类方法） |
| `from_plugin(path)` | 从 ESP/ESM 文件导入（类方法） |
| `from_json_file(path)` | 从 JSON 文件恢复（类方法） |
| `from_entries(entries)` | 从已有条目构建（类方法） |

#### 更新操作

四个更新方法均采用**两阶段匹配**（`update_from_strings_lookup` 除外），增强对 EditorID 补全差异的容错：

| 方法 | Phase 1 | Phase 2 回退 |
|------|---------|-------------|
| `update_from_eet_xml(path)` | 按完整 entry.id 精确匹配 + original 校验 | 按 `(original, grup:champ)` 匹配 |
| `apply_xt_entries(xt_entries)` | 按 edid 桶查找（三候选）+ rec/source/index 校验 | 按 `(original, rec)` 匹配 |
| `update_from_translated_plugin(path)` | 按 entry.id 精确匹配 | 按 `(original, type_field_base)` 匹配 |
| `update_from_strings_lookup(strings_lookup)` | 按 entry.string_id 精确匹配 | 无（strings 文件无原文信息） |

**背景**：EET/XT 工具对缺 EditorID 的记录有自己的补全逻辑，可能与 PluginParser 解析出的 `editor_id` 不同，导致 id 对不上。Phase 2 以原文和记录类型字段为锚点进行回退匹配。

**update_from_strings_lookup 说明**：此方法仅适用于本地化插件（有 string_id 的条目），通过整数 ID 精确匹配 strings 文件中的翻译。由于 strings 文件只存储翻译文本，无原文信息，无法进行文本回退匹配。

#### 内部工具方法

| 方法 | 说明 |
|------|------|
| `_type_field_base(context)` | 从 context 提取基础 TYPE:FIELD（去掉 INFO/DIAL 的 `\|quest_formid` 后缀） |
| `_form_id_from_entry_id(entry_id)` | 从 entry.id 提取 form_id（冒号与竖线之间的部分） |

#### 序列化

| 方法 | 说明 |
|------|------|
| `to_dict()` | 转为 dict 列表 |
| `to_json()` | 转为 JSON 字符串 |
| `to_json_file(path)` | 保存为 JSON 文件 |
| `to_export_dict()` | 导出为 DSD 格式 dict 列表（仅包含有译文的条目） |
| `to_dsd_json()` | 导出为 DSD 格式 JSON 字符串 |
| `to_dsd_json_file(path)` | 导出为 DSD 格式 JSON 文件 |
| `from_dsd_json_file(path)` | 从 DSD 格式 JSON 文件导入（类方法） |

---

### context_categories.py

定义 Skyrim ESP record type 的语义分类常量，供 AI 翻译批次规划和导出分文件复用。

#### 常量定义

```python
# 第一轮：专有名词（适合批量翻译并写入术语库）
ROUND1_CATEGORIES: dict[str, set[str]] = {
    "人名": {"NPC_:FULL", "NPC_:SHRT", "TACT:FULL"},
    "地名": {"LCTN:FULL", "WRLD:FULL", "CELL:FULL", ...},
    "书名": {"BOOK:FULL"},
    "物品": {"ACTI:FULL", "ALCH:FULL", "ARMO:FULL", ...},
    "法术技能": {"ENCH:FULL", "MGEF:FULL", "SPEL:FULL", ...},
    "任务名": {"QUST:FULL"},
    "互动": {"FLOR:RNAM", "FURN:FULL", "HAZD:FULL"},
}

# 第二轮：对话类，按 quest_formid 分组
ROUND2_PREFIXES: set[str] = {"INFO", "DIAL"}

# 第三轮：长文本（书籍内容/任务日志）
ROUND3_CONTEXTS: set[str] = {"BOOK:DESC", "QUST:NNAM", "QUST:CNAME"}

# 翻译完成后自动写入动态术语库的 context
AUTO_TERM_CONTEXTS: set[str] = {
    "NPC_:FULL", "NPC_:SHRT", "LCTN:FULL", ...
}

# 导出分文件规则
EXPORT_CATEGORIES: dict[str, list[str]] = {
    "书籍_书名.json": ["BOOK:FULL"],
    "书籍_内容.json": ["BOOK:DESC"],
    "人名.json": ["NPC_:FULL", "NPC_:SHRT", "TACT:FULL"],
    ...
}
```

---

## 导出函数

### export_to_categorized_json_files

**路径**: `src/transbridge/converter/translation_entry_collection_export.py`

将集合按 context 分类导出到多个 JSON 文件。

```python
export_to_categorized_json_files(
    collection: TranslationEntryCollection,
    output_dir: str | Path,
    *,
    ensure_ascii: bool = False,
    indent: int = 2,
) -> None
```

### get_categorized_file_names

**路径**: `src/transbridge/converter/translation_entry_collection_export.py`

预计算分类上传时会生成的文件名及条目数，**不写入磁盘**，用于在上传前向用户展示文件列表。

```python
get_categorized_file_names(
    collection: TranslationEntryCollection,
) -> list[tuple[str, int]]
# 返回 (filename, entry_count) 列表，仅包含非空文件
# 顺序：固定分类在前，对话文件（对话_[任务名].json）在后
```

#### 分类规则

| 文件名 | context 列表 |
|--------|--------------|
| `书籍_书名.json` | `BOOK:FULL` |
| `书籍_内容.json` | `BOOK:DESC` |
| `互动.json` | `FLOR:RNAM`, `FURN:FULL`, `HAZD:FULL` |
| `人名.json` | `NPC_:FULL`, `NPC_:SHRT`, `TACT:FULL` |
| `任务日志.json` | `QUST:FULL`, `QUST:NNAM`, `QUST:CNAME` |
| `地名与门.json` | `CELL:FULL`, `DOOR:FULL`, `LCTN:FULL`, `REFR:FULL`, `WRLD:FULL` |
| `法术_龙吼_技能.json` | `ENCH:FULL`, `MGEF:FULL`, ... |
| `物品.json` | `ACTI:FULL`, `ALCH:FULL`, ... |
| `对话_[任务名].json` | `INFO:*`, `DIAL:*`（按 quest_formid 动态命名） |

#### 特殊处理

INFO/DIAL 类型条目按 `quest_formid` 分组到独立文件，文件名使用对应任务的 `original` 字段。

---

## 使用示例

### 从 ESP 导入并保存

```python
from pathlib import Path
from src.transbridge.converter import TranslationEntryCollection

# 从 ESP 导入
collection = TranslationEntryCollection.from_plugin("MyMod.esp")

# 保存为 JSON
collection.to_json_file("MyMod_translation.json")

# 统计
print(f"总条目: {len(collection)}")
print(f"已翻译: {len(collection.filter(lambda e: e.stage == 1))}")
```

### 从 EET XML 更新译文

```python
collection = TranslationEntryCollection.from_plugin("MyMod.esp")

# 应用 EET XML 中的译文
updated = collection.update_from_eet_xml("translations.xml")
print(f"更新了 {updated} 条")
```

### 应用 XT 译文

```python
from src.transbridge.parser.xt_parser import XT_XmlParser

collection = TranslationEntryCollection.from_plugin("MyMod.esp")
xt_parser = XT_XmlParser.from_file("xt_translations.xml")

updated = collection.apply_xt_entries(xt_parser.entries)
print(f"应用了 {updated} 条 XT 译文")
```

### 分类导出

```python
from src.transbridge.converter.translation_entry_collection_export import export_to_categorized_json_files

export_to_categorized_json_files(
    collection,
    output_dir="output/",
)
```

---

## 坑点与注意事项

### 1. add() 无独立 update 方法

```python
# 错误：collection.update(entry)  # 不存在此方法
# 正确：
collection.add(entry, overwrite=True)
```

### 2. apply_xt_entries() 不能创建新条目

XT 只能更新已存在的 entry，不能创建新 entry。调用前需确保 collection 已包含对应条目。

### 3. 遍历时不要直接修改 dict

```python
# 错误：
for entry in collection:
    entry.translation = "新译文"  # 不会更新集合内的对象

# 正确：
for entry in list(collection):
    updated = TranslationEntry(
        id=entry.id,
        key=entry.key,
        original=entry.original,
        translation="新译文",
        stage=1,
        context=entry.context,
    )
    collection.add(updated, overwrite=True)
```

### 4. INFO/DIAL context 包含 quest_formid

```python
# context 格式: "INFO:NAM1|000789EF"
# 提取 quest_formid:
context, quest_formid = entry.context.split("|", 1)
```

### 5. key 字段的历史兼容性

`key` 字段与 `id` 相同，仅为避免破坏现有序列化数据。新代码应统一使用 `id`。

### 6. 两阶段匹配与 Phase 2 碰撞风险

Phase 2 回退以 `(original, type_field_base)` 为 key。同一记录类型下出现相同原文的概率极低（form_id 唯一标识一条 ESP 记录），但对极短公共字符串（如 `"Yes"` `"No"`）理论上存在误匹配风险。Phase 2 仅在 Phase 1 完全未命中时触发，不影响正常匹配路径。

---

## 依赖关系

```
converter
    │
    ├── 依赖
    │   ├── parser.EET_Entry
    │   ├── parser.EET_XmlParser
    │   ├── parser.XT_Entry
    │   ├── parser.PluginParser
    │   └── parser.plugin.PluginStringWithContext
    │
    └── 被依赖
        ├── ai_translator.translator
        ├── ui.workbench.step1/step2/step3
        ├── writer.*_writer
        └── paratranz.workflow.*
```

---

## 相关文档

- [ARCHITECTURE.md](ARCHITECTURE.md) - 系统架构与数据流
- [DATA_STRUCTURES.md](DATA_STRUCTURES.md) - 核心数据结构详解
- [parser.md](parser.md) - 解析器模块
- [writer.md](writer.md) - 写入器模块
