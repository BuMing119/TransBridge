# parser 模块

## 职责

解析ESP/ESM插件文件、ESP-ESM Translator XML、EET v3 二进制、xTranslator XML、SSU8/SSU9 SST，提取可翻译字符串，并提供上下文信息（NPC性别/种族、对话任务关联等）。SST 完整读写由独立 codec 负责，产品发布仍经过 application 的快照身份校验和统一发布流程。

---

## 目录结构

```
parser/
├── __init__.py                     # 导出 EET_Entry, EET_XmlParser, XT_Entry, XT_XmlParser
├── plugin_parser.py                # ESP/ESM解析入口，桥接层
├── eet_parser.py                   # ESP-ESM Translator XML解析
├── xt/
│   ├── xt_parser.py                # xTranslator XML解析
│   ├── sst_codec.py                # SSU8/SSU9完整二进制布局与无损读写
│   ├── sst_parser.py               # 兼容SST_Entry接口，保留document与物理ordinal
│   └── sst_serializer.py           # 明确身份更新、新建、默认不覆盖保存
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

### 四种翻译文件格式

产品格式明确区分 ESP-ESM Translator XML（`.xml`）、ESP-ESM Translator EET（`.eet`）、xTranslator XML（`.xml`）与 xTranslator SST（`.sst`）。对应历史技术 ID 为 `xml.eet`、`binary.eet`、`xml.xt`、`sst.ssu8` / `sst.ssu9`；后两个是 SST 版本，不是另外两种产品格式。技术命名不意味着 EET 与 XML 是同格式的变体。两个 XML 方言和误命名文件均按内容识别；历史 `parse_eet` 可兼容前两种独立格式，但写回严格遵循来源快照 FormatId。

### ESP-ESM Translator EET

`binary.eet` 与 `xml.eet` 按内容区分。`eet_binary_codec.py` 的 `EetDocument/EetRecord` 严格读取
v3 Classique(1)/BDD(2)，`eet_binary_parser.py` 保留查询 facade；`eet_number_cache.py` 维护译文数字缓存。
文件头是 `EET_ + TypeDT:int32 + Version:uint32`；GAME 为 uint16 UTF-8 字符串，LINE 为记录数与
逐条长度限定 payload，PHRA 为可选短语表。记录完整保留原文、译文、定位、状态、注释、原文哈希、
去标点原文、双侧缓存和可选 ICON；不得扫描跳过未知字节或容忍截断后部分成功。

正式接入按 [ADR-053](../adr/053-eet-binary-io-integration.md)实施：完整来源快照投影为命名空间内
`eet-v1:{ordinal}`，保存 codec 版本与记录签名。改变译文时只同步重建译文缓存；未修改字段逐字节保留。
GUI/助手与工程重开使用共享 application 合同；正式 MCP 没有文件读写工具。本地接入与相关回归已完成，
能力保持实验性；布局、.NET IL 证据与真实语料见 [EET 实验](eet-experiment.md)。

### EET_XmlParser

**路径**: `src/transbridge/parser/eet_parser.py`

**职责**: 解析 ESP-ESM Translator XML（`.xml`）；类名 `EET_XmlParser` 是历史 API，不表示该格式属于 EET 二进制格式。

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

### SST 完整读写

**范围与状态**：2026-11-08 按上游源码重写，格式层、工程来源、迁移、GUI 与助手的本地集成已完成，最终验证证据由[正式集成计划](../../plans/sst-integration/plan.md)汇总，边界见 [ADR-052](../adr/052-sst-io-integration.md)。SSU8/SSU9 读取、原格式写回及插件新建 SSU9 保持实验性，不将内部往返测试等同于全面上游兼容。正式 MCP 仍只有 foundation 能力，本轮没有开放文件解析/写回工具，其格式 read/write 能力仍为 unavailable。

**权威实现**：`parser/xt/sst_codec.py` 的 `SstDocument` / `SstRecord` 是唯一二进制实现。实验脚本 `scripts/sst_lab.py` 直接调用该模块，没有平行实验 codec。

文件结构依据 xTranslator commit `9aa38d60860273401f8bb0dd1557c82a295c41b3` 的 [SaveSSTFile/loadSstEdit](https://github.com/MGuffin/xTranslator/blob/9aa38d60860273401f8bb0dd1557c82a295c41b3/TESVT_SSTFunc.pas#L203-L435)：

- 公共头为四字节 magic 和一字节 flag。SSU9 随后存 master 数量及各名称；SSU8 没有 master 表。两者随后都有协作标签表，保存标签 ID 和名称。
- 表数量、标签 ID 和字符串字节长度均为小端 signed int32；字符串内容为 UTF-16LE。没有大端长度、结束符、记录总数或未知填充。
- 每条记录依次保存 `list_index:u8`、`string_id:i32`、`form_id:u32`、四字节 `record_type`、四字节 `field_type`、`index:u16`、`index_max:u16`、`edid_hash:u32`、`collab_id:u8`、`params:u8`，固定部分共 27 字节；后接两个长度前缀字符串，分别为原文与译文。记录一直读到文件末尾，不存在嵌套 subrecord。
- 读取保留空文本、未知四字节签名、全部状态位与原始 UTF-16 code units。负数/奇数字符串长度、非法计数、截断、无效 list index 或剩余不完整记录均拒绝整个输入，不返回“成功的部分内容”。
- `sst_hash()` 对每个 UTF-16 code unit 的低字节计算 FNV-1a；不是对 UTF-8 字节做 Hash。

`SST_Parser.from_file()` / `from_bytes(..., source_name=...)` 保留现有入口，`document` 保存完整文件，`entries` 是兼容投影。每个 `SST_Entry.record` 保存完整 `SstRecord`，`ordinal` 是从 0 开始的物理位置，`index` 对既有调用者仍从 1 开始。历史字段 `group_index`、`global_seq`、`f2` 分别仅兼容 `index_max`、`string_id`、`edid_hash`；不再解释为分组、序号推测或内部记录 ID。`SST_Subrecord` 仅保留类型兼容，不生成伪嵌套条目。

转换统一经 `converter/sst_entries.py`。新来源使用来源命名空间和 `sst-v2:{ordinal}` 物理记录 key，保存 codec 版本 2、记录身份签名、原始快照及来源 SHA-256。同文本、同 FormID、同字段的不同物理记录全部保留。记录签名覆盖原文及其余不可编辑元数据，译文不参与签名。旧来源继续允许打开编辑；没有 v2 身份的内容不能猜测写回，必须先重新导入来源或将已有译文迁移到新来源。

读取、迁移和写回的数据流分别是：

1. 建项/导入：捕获来源 bytes → `SstDocument.from_bytes()` → `document_entries()` → 权威来源快照与物理记录身份。
2. 迁移到插件：解析完整 SST → 用真实 FormID、record/field 与原文进行唯一匹配 → 草稿及冲突诊断 → 用户确认后的迁移提交。旧错误索引与插件业务索引不作等价假设，多义时不自动覆盖。
3. 原格式发布：已保存来源快照 → 验证版本、来源命名空间、ordinal、记录签名及原文 → 仅更新被选物理记录译文 → staging 写出 → 完整回读核验 → 既有覆盖确认/备份/原子发布。
4. 插件新建 SSU9：从保存的真实插件快照读取物理字段、masters、EditorID/Hash fallback 与本地化 StringID，通过确定的字段列表映射构造 `SstRecord`。这是实验性 Skyrim 插件转换，VMAD 属性、非 ASCII master/插件文件名及无法证明的字段映射暂拒绝，不生成猜测身份的文件。

`SST_Serializer.from_document()` / `from_parser()` 支持完整 SSU8/SSU9 原格式保存；`create_new()` 可从完整记录新建。修改接口可指定物理 `ordinal`，或唯一的 `rec + form_id + index`；历史仅 `form_id` 调用存在多义时抛错。空字符串是有效译文更新。底层 `save()` 默认不覆盖，使用同目录唯一临时文件；产品用户路径仍由 application 发布流程管理。

```python
from transbridge.parser.xt import SST_Parser, SST_Serializer

parsed = SST_Parser.from_file("source.sst")
writer = SST_Serializer.from_parser(parsed)
writer.update_record(0, translated_text="")  # 物理第一个记录，明确清空译文
writer.save("new-output.sst")               # 已有目标将被拒绝
```

聚焦测试：`tests/parser/test_sst_lab_codec.py`、`test_sst_parser_serializer.py`、`test_sst_lab_cli.py`。真实本地语料 19 个输入、9,690 条记录完整读到 EOF 且零修改字节一致；`hlioremi` 为 8,604 条，`ccvsvsse002-pets` 为 171 条。历史两份 `_test*.sst` 实验输出因错误文件头被拒绝。历史 changelog/旧测试报告保留当时结论，不作为当前格式规范。

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
ESP-ESM Translator XML ──► EET_XmlParser.from_file() ──► list[EET_Entry]
                                              │
                                              ▼
                                    TranslationEntry.create_from_eet_entry()

xTranslator XML ──► XT_XmlParser.from_file() ──► list[XT_Entry]
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

### 解析ESP-ESM Translator XML

```python
from transbridge.parser.eet_parser import EET_XmlParser

parser = EET_XmlParser.from_file("translation.xml")

# 查询NPC名称
npc_names = parser.find(grup="NPC_", champ="FULL")

# 按EditorID查询
entries = parser.get_by_edid("MyNPC")
```

### 解析xTranslator XML

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
