# 核心数据结构

本文档详细描述 TransBridge 系统中的核心数据结构，包括字段定义、ID/Context 格式规范、以及数据流转关系。

---

## TranslationEntry

翻译条目的基础数据单元，是系统中所有翻译数据的核心载体。

### 类定义

```python
@dataclass
class TranslationEntry:
    id: str           # 唯一标识符
    key: str          # 历史ID备份（与id相同）
    original: str     # 原文
    translation: str  # 译文
    stage: int        # 翻译状态：0=未翻译, 1=已翻译
    context: str      # 上下文信息，存储原key值
    string_id: int | None = None  # 本地化插件的字符串ID
    form_id_with_plugin: str | None = None  # 完整 FormID|Plugin 格式

    # DSD 兼容字段
    dsd_type: str = ""           # DSD 类型格式："NPC_ FULL"
    dsd_index: int = 1           # 原始索引
    editor_id: str = ""          # 原始 editor_id
```

### 字段详解

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | str | 唯一标识符，格式见下文"ID格式详解" |
| `key` | str | 与 `id` 相同，保留用于向后兼容 |
| `original` | str | 源语言文本（英文） |
| `translation` | str | 目标语言文本（中文），未翻译时为空字符串 |
| `stage` | int | 翻译状态码 |
| `context` | str | 上下文标识，存储原key值，格式见"Context格式详解" |
| `string_id` | int \| None | 本地化插件的字符串ID，用于精确匹配 strings 文件；非本地化插件为 None |
| `form_id_with_plugin` | str \| None | 完整的 FormID\|BaseRecordPlugin 格式，用于 DSD 导出 |
| `dsd_type` | str | DSD 类型格式，空格分隔（如 `"NPC_ FULL"`） |
| `dsd_index` | int | 原始索引，用于 DSD 索引类型导出 |
| `editor_id` | str | 原始 editor_id，用于 DSD GMST 类型导出 |

### Stage 状态码

| 值 | 含义 | 说明 |
|----|------|------|
| 0 | 未翻译 | `translation` 为空或无效 |
| 1 | 已翻译 | `translation` 有内容 |

### ID格式详解

TranslationEntry 的 `id` 字段采用统一格式：

```
{editor_id}:{form_id}|{index}~{TYPE:FIELD}
```

| 组成部分 | 说明 | 示例 |
|----------|------|------|
| `editor_id` | 记录的 EditorID | `MyNPC`, `QuestName` |
| `form_id` | FormID（十六进制） | `000123AB` |
| `index` | 同类型内的序号（1起始） | `1`, `2`, `3` |
| `TYPE:FIELD` | 记录类型:字段名 | `NPC_:FULL`, `INFO:NAM1` |

#### 不同来源的ID示例

| 来源 | ID示例 | 说明 |
|------|--------|------|
| Plugin NPC | `MyNPC:000123AB\|1~NPC_:FULL` | NPC全名 |
| Plugin对话 | `QuestName:000456CD\|1~INFO:NAM1\|000789EF` | 对话文本，含quest_formid |
| Plugin任务 | `MyQuest:000789EF\|1~QUST:CNAM` | 任务名称 |
| EET XML | `EditorID:000123AB\|1~GRUP:CHAMP` | EET条目 |

> **注意**: 对话类型（INFO/DIAL）的ID会在末尾附加 quest_formid，格式为 `~TYPE:FIELD|quest_formid`

### Context格式详解

`context` 字段存储原 key 值，用于XT匹配和上下文识别。

```
{TYPE:FIELD}|{extra_info}
```

| 类型 | Context示例 | 说明 |
|------|-------------|------|
| NPC名称 | `NPC_:FULL` | NPC全名字段 |
| 书籍 | `BOOK:CNAM` | 书籍标题 |
| 对话 | `INFO:NAM1\|000789EF` | 对话文本，含quest_formid |
| 任务 | `QUST:CNAM` | 任务名称 |
| EET条目 | `GRUP:CHAMP` | EET分组和字段 |

### 工厂方法

```python
# 从 EET_Entry 创建
@classmethod
def create_from_eet_entry(cls, eet_entry: EET_Entry) -> TranslationEntry

# 从 PluginStringWithContext 创建
@classmethod
def create_from_plugin_entry(cls, ps: PluginStringWithContext) -> TranslationEntry

# 从 XT_Entry 更新（类方法，返回新实例或None）
@classmethod
def try_update_from_xt(cls, entry: TranslationEntry, xt: XT_Entry) -> TranslationEntry | None
```

### 序列化

```python
# 转为字典
entry.to_dict() -> dict[str, Any]

# 从字典恢复
TranslationEntry.from_dict(data: dict) -> TranslationEntry
```

### DSD 格式转换

DSD (Dynamic String Dumper) 是 Skyrim Mod 翻译的外部格式，用于 xEdit 脚本等工具。

#### DSD 格式变体

| 变体 | 字段 | 适用类型 |
|------|------|----------|
| 基础格式 | `form_id`, `type`, `string` | FULL/DESC/SHRT/TNAM/RNAM/DNAM/RDMP 等 |
| QUST CNAM | `form_id`, `type`, `original`, `string` | QUST CNAM（需要原文匹配） |
| 索引格式 | `form_id`, `type`, `index`, `string` | INFO NAM1, QUST NNAM, MESG ITXT, PERK EPF2/EPFD |
| GMST DATA | `form_id`, `editor_id`, `type`, `string` | GMST DATA |

#### 方法

```python
# 导出为 DSD 格式字典
entry.to_dsd_dict() -> dict[str, Any]

# 从 DSD 格式字典创建
TranslationEntry.from_dsd_dict(data: dict) -> TranslationEntry
```

---

## TranslationEntryCollection

翻译条目集合，系统核心数据容器，管理所有 TranslationEntry 实例。

### 类定义

```python
class TranslationEntryCollection:
    _entries: dict[str, TranslationEntry]    # id → entry 主索引
    _key_index: dict[str, TranslationEntry]  # key → entry 辅助索引
```

### 双索引设计

Collection 维护两个索引：
- **id索引** (`_entries`): 按 `entry.id` 查找，主索引
- **key索引** (`_key_index`): 按 `entry.key` 查找，用于向后兼容

### 核心操作

#### 添加与更新

```python
def add(self, entry: TranslationEntry, *, overwrite: bool = True) -> None
```

添加条目到集合。`overwrite=True` 时覆盖已存在的条目。

> **重要**: 没有 `update()` 方法，更新条目使用 `add(entry, overwrite=True)`。

#### 查询

| 方法 | 用途 | 返回值 |
|------|------|--------|
| `get(entry_id)` | 按id精确获取 | `TranslationEntry \| None` |
| `get_by_key(key)` | 按key获取 | `TranslationEntry \| None` |
| `filter(predicate)` | 条件过滤 | `list[TranslationEntry]` |

#### 删除

```python
def remove(self, entry_id: str) -> None
```

按id删除条目，不存在则忽略。

### 批量操作

```python
# 批量添加
def add_many(self, entries: Iterable[TranslationEntry], *, overwrite: bool = True) -> None

# 合并另一个集合
def merge(self, other: TranslationEntryCollection, *, overwrite: bool = True) -> None
```

### 数据源加载

| 类方法 | 来源 | 说明 |
|--------|------|------|
| `from_plugin(path)` | ESP/ESM插件 | 解析插件生成条目 |
| `from_eet_xml(path)` | EET XML文件 | 解析EET格式XML |
| `from_json_file(path)` | JSON文件 | 从序列化JSON加载 |
| `from_entries(entries)` | 条目列表 | 通用构造方法 |

### 数据更新方法

```python
# 从EET XML更新译文
def update_from_eet_xml(self, path: str | Path) -> int

# 从XT XML更新译文
def apply_xt_entries(self, xt_entries: Iterable[XT_Entry]) -> int

# 从已翻译插件更新译文
def update_from_translated_plugin(self, path: str | Path, *, overwrite: bool = False) -> int

# 从Strings文件更新译文（本地化插件）
def update_from_strings_lookup(self, strings_lookup: PluginStringsLookup, *, overwrite: bool = False) -> int
```

### 序列化

```python
# 转为字典列表
def to_dict(self) -> list[dict[str, Any]]

# 导出JSON字符串
def to_json(self, *, ensure_ascii: bool = False, indent: int = 2) -> str

# 保存到JSON文件
def to_json_file(self, path: str | Path, **kwargs) -> None

# 从JSON文件加载（类方法）
@classmethod
def from_json_file(cls, path: str | Path, **kwargs) -> TranslationEntryCollection
```

### DSD 格式导入/导出

```python
# 导出为 DSD 格式字典列表（仅包含有译文的条目）
def to_export_dict(self) -> list[dict[str, Any]]

# 导出为 DSD 格式 JSON 字符串
def to_dsd_json(self, *, ensure_ascii: bool = False, indent: int = 2) -> str

# 导出为 DSD 格式 JSON 文件
def to_dsd_json_file(self, path: str | Path, **kwargs) -> None

# 从 DSD 格式 JSON 文件导入（类方法）
@classmethod
def from_dsd_json_file(cls, path: str | Path, **kwargs) -> TranslationEntryCollection
```

### DSD 格式导入/导出

```python
# 导出为 DSD 格式字典列表（仅包含有译文的条目）
def to_export_dict(self) -> list[dict[str, Any]]

# 导出为 DSD 格式 JSON 字符串
def to_dsd_json(self, *, ensure_ascii: bool = False, indent: int = 2) -> str

# 导出为 DSD 格式 JSON 文件
def to_dsd_json_file(self, path: str | Path, **kwargs) -> None

# 从 DSD 格式 JSON 文件导入
@classmethod
def from_dsd_json_file(cls, path: str | Path, **kwargs) -> TranslationEntryCollection
```

### 容器协议

```python
len(collection)      # 条目数量
entry in collection  # 检查id是否存在
for entry in collection: ...  # 迭代所有条目
```

---

## TermEntry

术语条目，用于AI翻译时的术语库管理。

### 类定义

```python
@dataclass
class TermEntry:
    term: str           # 原术语（英文）
    translation: str    # 译文（中文）
    source: str         # 来源标识
    context: str = ""   # 上下文（可选）
    created_at: str = ""        # 创建时间（ISO格式）
    case_sensitive: bool = False  # 是否区分大小写
    variants: list[str] = field(default_factory=list)  # 术语变体列表（单复数、缩写等）
```

### Source来源标识

| 值 | 说明 | 优先级 |
|----|------|--------|
| `auto_name` | AI从NPC名称自动提取 | 高 |
| `auto_dialogue` | AI从对话自动提取 | 高 |
| `manual` | 用户手动添加 | 最高 |
| `paratranz` | 从ParaTranz平台同步 | 中 |
| `json` | 从本地JSON文件加载 | 低 |
| `excel` | 从本地Excel文件加载 | 低 |

### 术语优先级规则

TermDatabaseManager 按 `LLMConfig.term_priority` 配置的顺序合并术语，后加载的覆盖先加载的。默认优先级（从低到高）：

```
excel → json → paratranz → dynamic(auto_name/auto_dialogue/manual)
```

### 术语变体 (variants)

`variants` 字段存储术语的变体形式，支持单复数、缩写、不同拼写等：

```python
# 示例：术语及其变体
TermEntry(
    term="apple",
    translation="苹果",
    source="paratranz",
    variants=["apples", "Apple"],  # 复数和大写变体
)
```

#### 变体匹配行为

| 匹配类型 | 行为 |
|----------|------|
| 子串匹配 (`match_terms`) | 变体参与正向子串、冠词规范化、反向前缀/后缀匹配 |
| 精确匹配 (`exact_match`) | 原文精确匹配变体时，返回主术语的译文 |
| 语义检索 (`semantic_match`) | 变体加入向量索引，检索到变体时返回主术语的译文 |

#### 变体使用规则

- **匹配**: 变体参与所有匹配类型，但始终返回**主术语**的译文（保证术语表一致性）
- **优先级**: 变体继承主术语的 `case_sensitive` 设置
- **去重**: 多个变体匹配同一主术语时，结果中去重
- **来源**: ParaTranz API 和本地 JSON 都支持加载 `variants` 字段

---

## PluginStringWithContext

从ESP/ESM插件提取的带上下文字符串，是解析阶段的中间数据结构。

### 类定义

```python
@dataclass
class PluginStringWithContext(PluginString):
    form_id: str        # FormID，可能含插件名: "000123AB|Skyrim.esm"
    editor_id: str      # EditorID
    type: str           # 类型: "NPC_ FULL" / "INFO NAM1"
    string: str         # 字符串内容
    index: int          # 同类型内的索引
    context: ContextUnion | None  # 额外上下文，包含quest等
    string_id: int | None         # 本地化插件的字符串ID（int），非本地化插件为 None
```

### 继承关系

```
PluginStringWithContext
    └── PluginString (from sse_plugin_interface)
```

### Context字段

`context` 是一个 Pydantic 模型，包含：

```python
class DialogueContext(BaseModel):
    quest: str = ""         # 任务FormID
    # ... 其他对话相关字段

class NPCContext(BaseModel):
    # ... NPC相关字段
```

### 数据转换

```
PluginStringWithContext ──create_from_plugin_entry──> TranslationEntry
```

---

## EET_Entry

EET XML格式的翻译条目。

### 类定义

```python
@dataclass(frozen=True)
class EET_Entry:
    grup: str          # 分组类型
    id: str            # FormID
    edid: str          # EditorID
    champ: str         # 字段名
    original: str      # 原文
    traduit: str       # 译文
    perso: str         # 个人标注
    index: int | None  # 序号
    status: int | None # 状态码（99=已翻译）
    idstexte: int | None
    commentaire: str   # 注释
    icon: int | None
```

### 状态码

| 值 | 含义 |
|----|------|
| 99 | 已完成翻译 |
| 其他 | 未完成/其他状态 |

### Key属性

```python
@property
def key(self) -> tuple[str, str, str, str]:
    """唯一键：(GRUP, ID, EDID, CHAMP)"""
    return (self.grup, self.id, self.edid, self.champ)
```

### 数据转换

```
EET_Entry ──create_from_eet_entry──> TranslationEntry
```

---

## XT_Entry

XT XML格式的翻译条目。

### 类定义

```python
@dataclass(frozen=True)
class XT_Entry:
    list_id: int | None  # 列表ID（0或1）
    edid: str            # EditorID
    rec: str             # 记录类型（格式: "TYPE:FIELD"）
    source: str          # 原文
    dest: str            # 译文
    index: int = 1       # 序号
```

### list_id含义

| 值 | 含义 | edid匹配规则 |
|----|------|--------------|
| 0 | 主列表 | `edid == id前半部分` |
| 1 | 副列表 | `edid == [id后半部分]` |

### 数据转换

XT_Entry 不能直接创建 TranslationEntry，只能更新已存在的条目：

```
XT_Entry + TranslationEntry ──try_update_from_xt──> TranslationEntry（更新后）
```

---

## 数据流转图

### 解析流程

```
ESP/ESM ──PluginParser──> PluginStringWithContext ──create_from_plugin_entry──> TranslationEntry
                                                                                        │
EET XML ──EET_XmlParser──> EET_Entry ──create_from_eet_entry──────────────────────> TranslationEntry ──add──> Collection
                                                                                        │
XT XML ──XT_XmlParser──> XT_Entry ──apply_xt_entries────────────────────────────────> TranslationEntry
```

### 更新流程

```
                    ┌─────────────────────────────────────┐
                    │     TranslationEntryCollection      │
                    └─────────────────┬───────────────────┘
                                      │
            ┌─────────────────────────┼─────────────────────────┬─────────────────────┐
            │                         │                         │                     │
            ▼                         ▼                         ▼                     ▼
update_from_eet_xml()      apply_xt_entries()      update_from_translated_plugin()  update_from_strings_lookup()
            │                         │                         │                     │
            ▼                         ▼                         ▼                     ▼
      匹配id+original          匹配edid+rec+source         匹配id              匹配string_id
      更新translation          更新translation            更新translation        更新translation
```

### 导出流程

```
Collection ──PluginWriter──> ESP/ESM + .strings文件
Collection ──EET_XML_Writer──> EET XML
Collection ──XT_XML_Writer──> XT XML
Collection ──to_json_file──> JSON
```

### AI翻译流程

```
Collection ──filter(stage==0)──> 待翻译条目 ──批次规划──> 批次列表
                                                            │
                                                            ▼
批次 ──TermDatabase.match──> 匹配术语 ──PromptBuilder──> Prompt
                                                            │
                                                            ▼
Prompt ──LLM调用──> 响应 ──解析──> 译文 ──add(overwrite)──> Collection
                                    │
                                    ▼
                          TermDatabase.add──> 术语库更新
```

---

## 设计原则

### 1. TranslationEntry作为统一数据模型

所有来源（ESP/EET/XT）的翻译条目统一转换为 `TranslationEntry`，后续处理（编辑、导出、AI翻译）无需关心来源格式。

### 2. Collection作为数据中枢

`TranslationEntryCollection` 是唯一的数据容器：
- UI展示基于Collection
- 导出操作基于Collection
- AI翻译基于Collection

### 3. 双索引设计

同时维护 `id索引` 和 `key索引`：
- `get(entry_id)`: 精确匹配，主查询方式
- `get_by_key(key)`: 兼容历史数据

### 4. 不可变更新模式

`try_update_from_xt` 等方法返回新实例，不修改原对象，保证数据一致性。

### 5. 术语库优先级

术语合并遵循优先级规则，手动添加 > 自动提取 > 外部来源，确保人工干预不被覆盖。
