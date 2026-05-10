# writer 模块

## 职责

将 `TranslationEntryCollection` 的翻译内容写回各种格式的文件：
- ESP/ESM 插件文件（支持 inline 和 localised 模式）
- EET XML 文件（更新已有文件）
- XT XML 文件（更新已有文件）

## 文件清单

| 文件 | 职责 |
|------|------|
| `plugin_writer.py` | 写入 ESP/ESM 插件文件 + strings 文件 |
| `eet_xml_writer.py` | 更新已有 EET XML 文件 |
| `xt_xml_writer.py` | 更新已有 XT XML 文件 |

---

## 核心类

### PluginWriter

**路径**: `src/transbridge/writer/plugin_writer.py`

**职责**: 将 `TranslationEntryCollection` 的翻译内容写回 ESP/ESM 插件文件。

#### 构造函数

```python
def __init__(
    self,
    plugin: SSEPluginWithContext,
    strings_lookup: PluginStringsLookup | None = None,
    language: str = "english",
) -> None
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `plugin` | `SSEPluginWithContext` | 已读取的插件实例 |
| `strings_lookup` | `PluginStringsLookup \| None` | 本地化插件的字符串查表；`None` 表示非本地化模式 |
| `language` | `str` | 输出 strings 文件的语言标签，默认 `"english"` |

#### 关键方法

| 方法 | 返回值 | 说明 |
|------|--------|------|
| `apply_collection(collection)` | `int` | 应用翻译到插件，返回实际更新的字符串数 |
| `write(output_path)` | `dict` | 保存插件文件；本地化模式下输出 strings 文件。返回 `{"esp_saved": bool, "strings_written": list[Path]}` |

#### 工作模式

**1. Inline 模式（非本地化插件）**

```
Plugin.extract_strings_with_context()
        │
        ▼
遍历每个 PluginStringWithContext
        │
        ├─► 构建 entry_id: "{editor_id}:{form_id}|{index}~{type}"
        │
        ├─► collection.get_by_key(entry_id) 匹配翻译条目
        │
        └─► plugin.find_string_subrecord() → subrecord.set_string(translation)
```

**特点**：
- 直接将译文写入记录中的字符串子记录
- 使用 `subrecord.set_string()` 修改原始数据

**2. Localised 模式（本地化插件）**

```
Plugin.extract_group_strings_with_context()
        │
        ▼
遍历每个 {PluginStringWithContext: StringSubrecord} 对
        │
        ├─► 构建 entry_id（同 inline 模式）
        │
        ├─► collection.get_by_key(entry_id) 匹配翻译条目
        │
        └─► 根据子记录类型分流：
              ├─ subrecord.string 是 int → PluginStringsWriter.add()
              └─ subrecord.string 是 RawString → subrecord.set_string()
```

**特点**：
- `subrecord.string` 是 `int`（string_id）时，写入外部 strings 文件
- `subrecord.string` 是 `RawString` 时，仍写入 inline（混合模式）
- 最终调用 `PluginStringsWriter.write()` 输出 `.strings/.dlstrings/.ilstrings`
- **纯本地化模式**（无 inline 修改）：仅输出 strings 文件，不保存 ESP 文件

#### 输出文件

| 模式 | 输出文件 |
|------|----------|
| Inline | `{plugin_name}.esp/.esm` |
| Localised（纯） | `Strings/{plugin_name}_{language}.*strings`（原 ESP 不修改） |
| Localised（混合） | `{plugin_name}.esp/.esm` + `Strings/{plugin_name}_{language}.*strings` |

#### write() 返回值

```python
{
    "esp_saved": bool,           # 是否保存了 ESP 文件
    "strings_written": list[Path], # 已写入的 strings 文件路径列表
}
```

- **非本地化模式**：`esp_saved=True`，`strings_written=[]`
- **纯本地化模式**：`esp_saved=False`，`strings_written=[...]`
- **混合模式**：`esp_saved=True`，`strings_written=[...]`

#### editor_id 继承逻辑

与 Parser 保持一致，处理 `editor_id=None` 的情况：

```python
if editor_id is None:
    if ps.type and ps.type.replace(" ", ":") != "REFR:FULL":
        if last_editor_id is not None:
            editor_id = last_editor_id
else:
    last_editor_id = editor_id
```

**注意**：REFR:FULL 不继承上一个 editor_id。

---

### EETWriter

**路径**: `src/transbridge/writer/eet_xml_writer.py`

**职责**: 根据已有的 `EET_XmlParser` 更新 EET XML 文件中的翻译内容。

#### 构造函数

```python
def __init__(self, parser: EET_XmlParser):
    self.parser = parser
    self.tree: ET.ElementTree = parser._tree
    self.root: ET.Element = self.tree.getroot()
```

#### 关键方法

| 方法 | 返回值 | 说明 |
|------|--------|------|
| `apply_collection(collection)` | `int` | 更新 `<TRADUIT>` 和 `<STATUS>` 节点，返回成功更新的条数 |
| `write(path)` | `None` | 保存更新后的 XML 文件（可写到任意路径） |

#### 匹配逻辑（两阶段）

**Phase 1**：读取 XML 节点的 `EDID`、`ID`（form_id）、`INDEX`、`GRUP`、`CHAMP`，调用 `TranslationEntry._build_eet_id()` 构建完整 id 后在 Collection 中精确查找。

**Phase 2**：若 Phase 1 未命中，按 `(ORIGINAL, grup:champ)` 在预构建的回退索引中查找有译文的条目。

```python
# Phase 1
full_id = TranslationEntry._build_eet_id(edid, form_id, index, grup, champ)
entry = collection.get(full_id)

# Phase 2（Phase 1 未命中时）
entry = fallback_index.get((original, f"{grup}:{champ}"))
```

#### 更新的节点

| 节点 | 更新内容 |
|------|----------|
| `<TRADUIT>` | `entry.translation` |
| `<STATUS>` | `"99"`（已翻译）或 `"0"`（未翻译） |

---


### XTWriter

**路径**: `src/transbridge/writer/xt_xml_writer.py`

**职责**: 根据已有的 `XT_XmlParser` 更新 XT XML 文件中的翻译内容。

#### 构造函数

```python
def __init__(self, parser: XT_XmlParser):
    self.parser = parser
    self.tree: ET.ElementTree = parser._tree
    self.root: ET.Element = self.tree.getroot()
```

#### 关键方法

| 方法 | 返回值 | 说明 |
|------|--------|------|
| `apply_collection(collection)` | `int` | 更新 `<Dest>` 节点，返回成功更新的条数 |
| `write(path)` | `None` | 保存更新后的 XML 文件（可写到任意路径） |

#### 匹配逻辑（两阶段）

**Phase 1**：对每个 XML `<String>` 节点，以其 `EDID` 值在三张预构建索引中依次查找：

| 索引 | 键 | 说明 |
|------|-----|------|
| `by_editid` | `editor_id`（entry.id 冒号左侧） | XT 填 editid 时命中 |
| `by_formid` | bare `form_id`（冒号与竖线之间） | XT 对缺 editid 的记录填 bare formid 时命中 |
| `by_bracket_formid` | `[form_id]` | XT 填方括号 formid 时命中 |

命中候选后再校验 `rec == entry.context.split("|")[0]`（取 context 基础部分，去掉 INFO/DIAL 的 quest_formid 后缀）和 `source == entry.original`。

**Phase 2**：若 Phase 1 未命中，按 `(source, rec)` 在预构建的回退索引中查找有译文的条目。

#### XT EDID 格式说明

XT 工具的 `EDID` 字段格式与 `List` 属性无严格绑定：

| 实际格式 | 含义 |
|----------|------|
| `MyNPC` | editor_id |
| `000123AB` | bare form_id（记录无 EditorID 时） |
| `[000123AB]` | 方括号 form_id |

---

## 使用示例

### 写入 ESP 插件

```python
from src.transbridge.writer.plugin_writer import PluginWriter
from src.transbridge.parser.plugin_parser import PluginParser

# 解析插件
plugin, strings_lookup = PluginParser.parse_plugin("MyMod.esp")

# 创建写入器
writer = PluginWriter(plugin, strings_lookup, language="chinese")

# 应用翻译
updated = writer.apply_collection(collection)
print(f"更新了 {updated} 个字符串")

# 保存文件
result = writer.write("Output/MyMod.esp")
if result["esp_saved"]:
    print(f"ESP 已保存")
if result["strings_written"]:
    print(f"Strings 文件: {result['strings_written']}")
# 纯本地化模式：仅生成 Output/Strings/MyMod_chinese.*strings，原 ESP 不修改
```

### 更新 EET XML

```python
from src.transbridge.writer.eet_xml_writer import EETWriter
from src.transbridge.parser.eet_parser import EET_XmlParser

# 解析已有 XML
parser = EET_XmlParser.from_file("translation.xml")

# 创建写入器
writer = EETWriter(parser)

# 应用翻译（两阶段匹配，自动处理 edid 差异）
updated = writer.apply_collection(collection)
print(f"更新了 {updated} 个条目")

# 保存（可写到新路径）
writer.write("translation_updated.xml")
```

### 更新 XT XML

```python
from src.transbridge.writer.xt_xml_writer import XTWriter
from src.transbridge.parser.xt_parser import XT_XmlParser

# 解析已有 XML
parser = XT_XmlParser.from_file("translation.xml")

# 创建写入器
writer = XTWriter(parser)

# 应用翻译（两阶段匹配，自动处理 edid 格式差异）
updated = writer.apply_collection(collection)
print(f"更新了 {updated} 个条目")

# 保存（可写到新路径）
writer.write("translation_updated.xml")
```

---

## 坑点与注意事项

### 1. 本地化模式下 string 类型判断

```python
if isinstance(subrecord.string, int):
    # → 写入 strings 文件
elif isinstance(subrecord.string, RawString):
    # → 写入 inline
```

本地化插件可能同时包含两种类型的字符串。

### 2. EET/XT Writer 只能更新已有 XML

Writer 系列在加载的原始 XML 树上修改节点值，`write(path)` 可写到任意路径，但不能凭空生成 XML 结构。

### 3. INFO/DIAL context 含 quest_formid 后缀

EET/XT 的 rec 字段只含基础类型（如 `INFO:NAM1`），而 entry.context 对 INFO/DIAL 包含 quest_formid 后缀（如 `INFO:NAM1|000789EF`）。Writer 内部已处理此差异，外部调用无需关注。

### 4. editor_id 继承逻辑（PluginWriter）

需要处理 `editor_id=None` 的情况，与 Parser 保持一致：

- 非 REFR:FULL 记录：继承上一个有效 editor_id
- REFR:FULL 记录：不继承（保持 None）

### 5. 混合模式插件

某些本地化插件可能同时包含：
- `int` 类型的 string_id → 写入 strings 文件
- `RawString` 类型的内联字符串 → 写入 ESP 文件

`PluginWriter` 已处理这种情况。

### 6. 纯本地化模式下 ESP 不保存

对于纯本地化插件（所有字符串都是 string_id），`write()` 不会保存 ESP 文件，仅输出 strings 文件。调用方需根据返回值 `esp_saved` 判断是否需要提示用户关于 ESP 文件的处理。

### 7. Strings 文件总是输出三种类型

`PluginStringsWriter.write()` 总是输出三种 strings 文件（`.strings`、`.dlstrings`、`.ilstrings`），即使某个类型没有任何条目也会写入空文件（仅含 8 字节文件头：count=0, data_size=0）。这确保了本地化插件的所有 strings 文件都存在，避免游戏因缺少某个文件而报错。

---

## 依赖关系

```
writer
├── converter (TranslationEntry, TranslationEntryCollection)
├── parser (SSEPluginWithContext, PluginStringsLookup, EET_XmlParser, XT_XmlParser)
└── sse-plugin-interface (RawString, StringSubrecord)

被依赖:
└── ui/workbench (step3 导出功能)
```

---

## 相关文档

- [parser.md](parser.md) - 解析器模块（输入端）
- [converter.md](converter.md) - TranslationEntry 和 Collection
- [ARCHITECTURE.md](ARCHITECTURE.md) - 数据流架构
