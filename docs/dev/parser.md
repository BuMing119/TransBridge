# parser 模块

## 职责

解析ESP/ESM插件文件、EET XML、XT XML，提取可翻译字符串，并提供上下文信息（NPC性别/种族、对话任务关联等）。

---

## 目录结构

```
parser/
├── __init__.py                     # 导出 EET_Entry, EET_XmlParser, XT_Entry, XT_XmlParser
├── plugin_parser.py                # ESP/ESM解析入口，桥接层
├── eet_parser.py                   # EET XML解析
├── xt_parser.py                    # XT XML解析
├── strings_file.py                 # .strings文件读写（本地化插件支持）
├── plugin/
│   ├── __init__.py
│   ├── plugin_with_context.py      # SSEPluginWithContext，带上下文提取
│   ├── plugin_string_with_context.py   # PluginStringWithContext，扩展PluginString
│   └── item.py                     # 上下文数据类（NPCContext, InfoContext等）
└── utils/
    ├── __init__.py
    └── fromid_trans.py             # FormID转换工具函数
```

---

## 核心类

### PluginParser

**路径**: `src/transbridge/parser/plugin_parser.py`

**职责**: 解析ESP/ESM/ESL文件的入口类，作为 `SSEPluginWithContext` 与 `TranslationEntry` 之间的桥接层。

```python
class PluginParser:
    def parse_plugin(
        self,
        path: Path,
        progress_callback: Callable[[int, int, str], None] | None = None,
        skip_empty: bool = True,
        language: str = "english",
    ) -> list[TranslationEntry]
```

**参数说明**:
| 参数 | 类型 | 说明 |
|------|------|------|
| `path` | `Path` | ESP/ESM/ESL文件路径 |
| `progress_callback` | `Callable` | 进度回调 `(current, total, description)` |
| `skip_empty` | `bool` | 是否跳过空字符串（默认True） |
| `language` | `str` | 本地化插件的语言（默认"english"） |

**处理流程**:
1. 加载插件文件到 `SSEPluginWithContext`
2. 尝试加载 `.strings/.dlstrings/.ilstrings` 文件（本地化插件）
3. 调用 `extract_strings_with_context()` 提取带上下文的字符串
4. 修复 `editor_id=None` 的问题（继承上一个有效的editor_id）
5. 转换为 `TranslationEntry` 列表返回

**特殊处理**:
- `REFR:FULL` 类型条目不继承上一个editor_id
- 本地化插件的字符串是int ID，需要通过 `PluginStringsLookup` 查表获取实际文本

---

### SSEPluginWithContext

**路径**: `src/transbridge/parser/plugin/plugin_with_context.py`

**职责**: 扩展 `sse_plugin_interface.SSEPlugin`，提供带上下文的字符串提取。继承自SSEPlugin，增加了NPC、对话等上下文信息的提取能力。

```python
class SSEPluginWithContext(SSEPlugin):
    def extract_strings_with_context(
        self,
        extract_localized: bool = False,
        strings_lookup: PluginStringsLookup | None = None,
    ) -> list[PluginStringWithContext]

    def find_string_subrecord(
        self,
        form_id: str,
        type: str,
        string: str,
        index: int | None,
    ) -> StringSubrecord | None
```

**上下文提取能力**:

| 记录类型 | 提取的上下文 | 来源字段 |
|----------|--------------|----------|
| `NPC_` | NPCContext | ACBS(性别), RNAM(种族), CNAM(职业) |
| `INFO` | InfoContext | CTDA(说话者), TRDT(情绪), NAM2(备注), DIAL(任务) |
| `DIAL` | DialContext | QNAM(任务), DLBR映射(对话分支) |

**内部方法**:
- `_extract_npc_context(record)` → 从NPC_记录提取性别/种族/职业
- `_extract_info_context(record, dial_context)` → 从INFO记录提取对话上下文
- `_extract_dial_context(record, dlbr_map)` → 从DIAL记录提取任务关联
- `_build_dlbr_map()` → 构建DIAL到(Quest, DLBR)的映射表

**情绪映射表**:
```python
EMOTION_MAP = {
    0: "Neutral",
    1: "Anger",
    2: "Disgust",
    3: "Fear",
    4: "Sad",
    5: "Happy",
    6: "Surprise",
    7: "Puzzled",
}
```

---

### PluginStringWithContext

**路径**: `src/transbridge/parser/plugin/plugin_string_with_context.py`

**职责**: 扩展 `sse_plugin_interface.PluginString`，增加上下文字段和 string_id 字段。

```python
@dataclass
class PluginStringWithContext(PluginString):
    context: ContextUnion | None = None  # 上下文数据
    string_id: int | None = None         # 本地化插件的字符串ID
```

**继承的字段**（来自PluginString）:
- `editor_id: str | None` — 编辑器ID
- `form_id: str` — FormID（格式：`XXXXXXXX|PluginName.esp`）
- `type: str` — 记录类型（如 `NPC_ FULL`）
- `string: str` — 字符串内容
- `index: int` — 子记录索引

**新增字段**:
- `string_id: int | None` — 本地化插件的字符串整数ID，用于精确匹配 strings 文件；非本地化插件为 None

---

### 上下文数据类

**路径**: `src/transbridge/parser/plugin/item.py`

#### ContextType（枚举）

```python
class ContextType(StrEnum):
    NPC = "NPC"       # NPC记录
    INFO = "INFO"     # 对话INFO记录
    DIAL = "DIAL"     # 对话主题DIAL记录
    GENERIC = "GENERIC"  # 通用/未知类型
```

#### NPCContext

```python
class NPCContext(BaseContext):
    type: Literal[ContextType.NPC] = ContextType.NPC
    npc_sex: str | None       # "Male" / "Female"
    npc_race: str | None      # 种族FormID，如 "00013745|Skyrim.esm"
    npc_class: str | None     # 职业FormID
```

#### InfoContext

```python
class InfoContext(BaseContext):
    type: Literal[ContextType.INFO] = ContextType.INFO
    quest: str | None           # 关联任务FormID
    dialogue_topic: str | None  # 所属DIAL FormID
    speaker: str | None         # 发言NPC FormID
    emotion: str | None         # 情绪类型（字符串）
    response_note: str | None   # 响应备注（NAM2字段）
```

#### DialContext

```python
class DialContext(BaseContext):
    type: Literal[ContextType.DIAL] = ContextType.DIAL
    quest: str | None            # 关联任务FormID
    dialogue_branch: str | None  # 所属DLBR FormID
```

#### ContextUnion

```python
ContextUnion = NPCContext | InfoContext | DialContext | GenericContext
```

---

### FormID转换工具

**路径**: `src/transbridge/parser/utils/fromid_trans.py`

```python
def formid_bytes_to_hex(data: bytes) -> str:
    """
    将4字节FormID转为十六进制字符串。
    例：b'\x00\x00\x01\x00' → "00010000"
    """

def formid_with_plugin_name(formid_hex: str, plugin_name: str) -> str:
    """
    组合FormID和插件名为完整格式。
    例：("00010000", "Skyrim.esm") → "00010000|Skyrim.esm"
    """

def formid_bytes_to_complete(
    data: bytes,
    masters: list[str],
    plugin_name: str,
) -> str:
    """
    将FormID字节转为完整FormID字符串。
    根据首字节（master index）确定所属插件。
    """
```

**FormID格式说明**:
- 原始格式：`XXYYZZZZ`（XX为master index）
- 完整格式：`XXYYZZZZ|PluginName.esp`

---

### EET_XmlParser

**路径**: `src/transbridge/parser/eet_parser.py`

**职责**: 解析EET格式的XML翻译文件。

```python
@dataclass(frozen=True)
class EET_Entry:
    grup: str           # 记录组
    id: str             # FormID（十六进制）
    edid: str           # EditorID
    champ: str          # 字段名
    original: str       # 原文
    traduit: str        # 译文
    perso: str          # 个人标记
    index: int | None   # 索引
    status: int | None  # 状态码
    idstexte: int | None
    commentaire: str    # 注释
    icon: int | None

    @property
    def key(self) -> tuple[str, str, str, str]:
        """唯一键：(GRUP, ID, EDID, CHAMP)"""
```

**主要方法**:

```python
class EET_XmlParser:
    @classmethod
    def from_file(cls, path: str | Path, encoding: str | None = None) -> "EET_XmlParser"

    @classmethod
    def from_string(cls, xml_text: str) -> "EET_XmlParser"

    def find(
        self,
        *,
        grup: str | None = None,
        id: str | None = None,
        edid: str | None = None,
        champ: str | None = None,
        original_contains: str | None = None,
        traduit_contains: str | None = None,
        status: int | None = None,
    ) -> list[EET_Entry]

    def get_by_key(self, grup: str, id: str, edid: str, champ: str) -> list[EET_Entry]
    def get_by_grup(self, grup: str) -> list[EET_Entry]
    def get_by_id(self, id: str) -> list[EET_Entry]
    def get_by_edid(self, edid: str) -> list[EET_Entry]
    def to_dicts(self) -> list[dict]
```

**XML结构**:
```xml
<DocumentElement>
  <ESP>
    <GRUP>NPC_</GRUP>
    <ID>000123AB</ID>
    <EDID>MyNPC</EDID>
    <CHAMP>FULL</CHAMP>
    <ORIGINAL>Hello</ORIGINAL>
    <TRADUIT>你好</TRADUIT>
    ...
  </ESP>
</DocumentElement>
```

---

### XT_XmlParser

**路径**: `src/transbridge/parser/xt_parser.py`

**职责**: 解析XT格式的XML翻译文件（SSTXMLRessources格式）。

```python
@dataclass(frozen=True)
class XT_Entry:
    list_id: int | None  # 列表ID（String节点的List属性）
    edid: str            # EditorID
    rec: str             # 记录类型
    source: str          # 原文
    dest: str            # 译文
    index: int = 1       # REC节点的id属性（从1开始）
```

**主要方法**:

```python
class XT_XmlParser:
    params: dict[str, str]  # Params节点内容

    @classmethod
    def from_file(cls, xml_path: str) -> "XT_XmlParser"

    def get_by_edid(self, edid: str) -> list[XT_Entry]
    def find(self, predicate: Callable[[XT_Entry], bool]) -> list[XT_Entry]
    def iter(self) -> Iterator[XT_Entry]
    def to_json(self, ensure_ascii: bool = False, indent: int = 2) -> str
    def to_json_file(self, path: str, ensure_ascii: bool = False, indent: int = 2) -> None
    def to_csv_file(self, path: str) -> None
```

**XML结构**:
```xml
<SSTXMLRessources>
  <Params>
    <Addon>MyMod.esp</Addon>
    <Version>1</Version>
  </Params>
  <Content>
    <String List="0">
      <EDID>MyNPC</EDID>
      <REC id="1">NPC_ FULL</REC>
      <Source>Hello</Source>
      <Dest>你好</Dest>
    </String>
  </Content>
</SSTXMLRessources>
```

---

### Strings文件处理

**路径**: `src/transbridge/parser/strings_file.py`

#### SkyrimStringsFile

读取单个 `.strings` / `.dlstrings` / `.ilstrings` 文件。

```python
class SkyrimStringsFile:
    @classmethod
    def from_file(cls, path: Path) -> "SkyrimStringsFile"

    @classmethod
    def from_bytes(cls, data: bytes, ext: str, source: str = "") -> "SkyrimStringsFile"

    def get(self, string_id: int) -> str | None
```

**文件格式**:
```
[Count: UInt32][DataSize: UInt32]           ← 8字节头
[StringID: UInt32, Offset: UInt32] × Count  ← 目录
<string data>                                ← 数据区
```

- `.strings`: null结尾字符串
- `.dlstrings/.ilstrings`: 长度前缀字符串

#### PluginStringsLookup

聚合插件的所有strings文件，提供统一查询接口。

```python
class PluginStringsLookup:
    @classmethod
    def from_plugin(
        cls,
        plugin_path: Path,
        language: str = "english",
    ) -> "PluginStringsLookup | None"

    def get(self, string_id: int) -> str | None
```

**发现顺序**:
1. 松散文件：`<plugin_dir>/Strings/<stem>_<lang>.*`
2. BSA归档：`<plugin_dir>/<stem>.bsa` 或 `Skyrim - Interface.bsa`

**基础游戏插件的BSA规则**:
```python
_BASE_GAME_PLUGINS = {
    "skyrim", "update", "hearthfires", "dragonborn", "dawnguard",
}
# 这些插件 → Skyrim - Interface.bsa
# _ResourcePack.esl (AE) → _ResourcePack.bsa（有自己的BSA）
# 其他插件 → <PluginStem>.bsa
```

#### SkyrimStringsWriter / PluginStringsWriter

写入strings文件。

```python
class PluginStringsWriter:
    def add(self, string_id: int, text: str, subrecord_type: str) -> None
    def write(self, output_dir: Path, plugin_stem: str, language: str = "english") -> list[Path]
```

**子记录类型与文件映射**:

| 文件类型 | 子记录类型 |
|----------|------------|
| `.strings` | FULL, SHRT |
| `.dlstrings` | DESC, CNAM, NNAM, DNAM, RNAM, ONAM, TNAM, INAM, ANAM, BNAM, ENAM, GNAM, ZNAM |
| `.ilstrings` | NAM1, NAM2, NAM3, NAM4 |

**空文件处理**:

`write()` 方法总是输出三种 strings 文件，即使某个类型没有任何条目也会写入空文件（仅含 8 字节文件头：count=0, data_size=0）。这确保了本地化插件的所有 strings 文件都存在，避免游戏因缺少某个文件而报错。

---

## 数据流

### ESP解析流程

```
ESP/ESM文件
    │
    ▼
SSEPluginWithContext.from_file()
    │
    ├──► PluginStringsLookup.from_plugin()  [本地化插件]
    │         │
    │         ├─► 松散文件 (Data/Strings/*.strings)
    │         └─► BSA归档 (Skyrim - Interface.bsa)
    │
    ▼
extract_strings_with_context()
    │
    ├──► _build_dlbr_map()  [构建DIAL→Quest映射]
    │
    ├──► 遍历所有Group
    │       │
    │       ├──► NPC_记录 → _extract_npc_context()
    │       ├──► DIAL记录 → _extract_dial_context()
    │       └──► INFO记录 → _extract_info_context()
    │
    ▼
list[PluginStringWithContext]
    │
    ▼ (PluginParser._create_item)
TranslationEntry.create_from_plugin_entry()
    │
    ▼
list[TranslationEntry]
```

### EET/XT解析流程

```
EET XML ──► EET_XmlParser.from_file() ──► list[EET_Entry]
                                              │
                                              ▼
                                    TranslationEntry.create_from_eet_entry()

XT XML ──► XT_XmlParser.from_file() ──► list[XT_Entry]
                                             │
                                             ▼
                                   Collection.apply_xt_entries()
```

---

## 使用示例

### 解析ESP插件

```python
from pathlib import Path
from transbridge.parser.plugin_parser import PluginParser

parser = PluginParser()
entries = parser.parse_plugin(
    Path("Data/MyMod.esp"),
    skip_empty=True,
    language="english",
)

for entry in entries:
    print(f"{entry.id}: {entry.original}")
    if entry.context:
        print(f"  Context: {entry.context}")
```

### 解析本地化插件

```python
# Skyrim.esm 等本地化插件会自动加载strings文件
entries = parser.parse_plugin(
    Path("Data/Skyrim.esm"),
    language="english",
)
```

### 解析EET XML

```python
from transbridge.parser.eet_parser import EET_XmlParser

parser = EET_XmlParser.from_file("translation.xml")

# 查询NPC名称
npc_names = parser.find(grup="NPC_", champ="FULL")

# 按EditorID查询
entries = parser.get_by_edid("MyNPC")
```

### 解析XT XML

```python
from transbridge.parser.xt_parser import XT_XmlParser

parser = XT_XmlParser.from_file("translation.xml")

# 获取参数
print(parser.params.get("Addon"))  # 插件名

# 导出为JSON
parser.to_json_file("translation.json")
```

---

## 坑点与注意事项

### 1. editor_id继承

当 `editor_id=None` 时，默认继承上一个有效的editor_id，**但 `REFR:FULL` 类型除外**：

```python
# plugin_parser.py 第80行
if ps.editor_id is None and ps.type and ps.type.replace(" ", ":") != "REFR:FULL":
    if last_editor_id is not None:
        setattr(ps, "editor_id", last_editor_id)
```

### 2. 本地化插件处理

本地化插件的字符串存储为整数ID，需要通过 `PluginStringsLookup` 解析：

```python
# 如果strings_lookup为None，本地化字符串会被跳过
if strings_lookup is not None:
    resolved = strings_lookup.get(string)  # int → str
```

### 3. FormID格式

完整FormID格式为 `XXXXXXXX|PluginName.esp`：

- 前2位（XX）是master index，用于确定所属插件
- 后6位是记录ID

### 4. BSA优先级

对于基础游戏插件（Skyrim.esm等），strings文件在 `Skyrim - Interface.bsa` 中；其他插件在 `<PluginStem>.bsa` 中。

---

## 依赖关系

```
parser
  │
  ├──► sse-plugin-interface (外部依赖)
  │       ├── SSEPlugin
  │       ├── PluginString
  │       ├── StringSubrecord
  │       └── ...
  │
  ├──► sse_bsa (外部依赖，用于BSA解析)
  │
  ├──► pydantic (用于上下文数据类)
  │
  └──► converter
          └── TranslationEntry
```

**被依赖**:
- `converter` — TranslationEntry.create_from_plugin_entry()
- `writer` — 需要原始解析结果进行写回
- `ui` — 用户界面调用解析功能
