# 系统架构

本文档描述 TransBridge 的整体架构设计，包括模块依赖关系、数据流转、全局状态管理和关键设计决策。

---

## 模块依赖图

```
                    ┌─────────────┐
                    │     ui      │
                    └──────┬──────┘
                           │
         ┌─────────────────┼─────────────────┐
         │                 │                 │
         ▼                 ▼                 ▼
┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│ai_translator│    │   writer    │    │  paratranz  │
└──────┬──────┘    └──────┬──────┘    └──────┬──────┘
       │                  │                  │
       └─────────────────┬┴──────────────────┘
                         │
                         ▼
                  ┌─────────────┐
                  │  converter  │  ← 核心数据层
                  └──────┬──────┘
                         │
                         ▼
                  ┌─────────────┐
                  │   parser    │
                  └─────────────┘
```

### 模块职责

| 模块 | 职责 | 依赖 |
|------|------|------|
| `ui` | PyQt6用户界面，事件处理 | 所有模块 |
| `ai_translator` | AI翻译逻辑，术语管理 | converter |
| `writer` | 文件写入（ESP/EET/XT） | converter |
| `paratranz` | ParaTranz 平台集成（API 客户端、配置管理、上传/下载/导出工作流） | converter |
| `converter` | 核心数据结构（TranslationEntry/Collection/Context分类常量），系统数据中枢 | parser |
| `parser` | ESP/ESM/EET/XT解析，上下文提取，本地化strings支持 | sse-plugin-interface, sse_bsa |

---

## 数据流

### 解析流程

```
ESP/ESM ──PluginParser──> PluginStringWithContext ──create──> TranslationEntry ──┐
                                                                                 │
EET XML ──EET_XmlParser──> EET_Entry ──create──────────────────────────────────> Collection
                                                                                 │
XT XML ──XT_XmlParser──> XT_Entry ──apply───────────────────────────────────────┘
```

详细说明：

#### Plugin解析（ESP/ESM）

```
ESP/ESM文件
    │
    ▼
PluginParser.parse_plugin()
    │
    ├─► SSEPluginWithContext.from_file()
    │
    ├─► PluginStringsLookup.from_plugin()  [本地化插件]
    │       ├─► 松散文件: Data/Strings/{Plugin}_{Lang}.*strings
    │       └─► BSA归档: Skyrim - Interface.bsa 或 {Plugin}.bsa
    │
    ├─► extract_strings_with_context()
    │       ├─► _build_dlbr_map()        # DIAL → (Quest, DLBR)映射
    │       ├─► _extract_npc_context()   # NPC性别/种族/职业
    │       ├─► _extract_dial_context()  # 任务关联
    │       └─► _extract_info_context()  # 对话说话者/情绪
    │
    └─► TranslationEntry.create_from_plugin_entry()
```

**关键点**：
- 本地化插件（如Skyrim.esm）需要加载外部strings文件
- NPC/INFO/DIAL记录会提取额外的上下文信息
- `editor_id=None` 时继承上一个有效值（REFR:FULL除外）

#### EET解析

```python
parser = EET_XmlParser.from_file("translation.xml")
entries = parser.find(grup="NPC_", champ="FULL")
# → TranslationEntry.create_from_eet_entry()
```

#### XT应用

```python
parser = XT_XmlParser.from_file("translation.xml")
# → Collection.apply_xt_entries(parser.entries)
```

### 写入流程

```
Collection ──PluginWriter──> ESP/ESM + .strings
Collection ──EETWriter/EETBuilder──> EET XML (更新/新建)
Collection ──XTWriter/XTBuilder──> XT XML (更新/新建)
Collection ──to_json_file──> JSON
```

#### Plugin 写入（ESP/ESM）

```
TranslationEntryCollection
        │
        ▼
PluginWriter.apply_collection()
        │
        ├─► Inline模式（非本地化插件）
        │       │
        │       ├─► 遍历 plugin.extract_strings_with_context()
        │       ├─► 构建 entry_id: "{editor_id}:{form_id}|{index}~{type}"
        │       ├─► collection.get_by_key(entry_id) 匹配
        │       └─► subrecord.set_string(translation)
        │
        └─► Localised模式（本地化插件）
                │
                ├─► 遍历 plugin.extract_group_strings_with_context()
                ├─► 构建 entry_id（同 inline）
                ├─► collection.get_by_key(entry_id) 匹配
                └─► 分流处理：
                        ├─ subrecord.string 是 int → PluginStringsWriter.add()
                        └─ subrecord.string 是 RawString → subrecord.set_string()
        │
        ▼
PluginWriter.write()
        │
        ├─► plugin.save(output_path)
        └─► [Localised] PluginStringsWriter.write()
                → {plugin}_{language}.strings
                → {plugin}_{language}.dlstrings
                → {plugin}_{language}.ilstrings
```

**关键点**：
- 本地化插件可能同时包含 `int` 类型（写 strings 文件）和 `RawString` 类型（写 ESP）
- `apply_collection` 使用 `get_by_key()` 匹配条目
- 需要处理 `editor_id=None` 的继承逻辑（与 Parser 一致）

#### EET XML 写入

**更新已有文件**（EETWriter）：
```
EET_XmlParser (已有解析器)
        │
        ▼
EETWriter(parser)
        │
        ├─► 遍历 .//ESP 节点
        ├─► 按 EDID 匹配 collection.get(edid)
        ├─► 校验 context == "GRUP:CHAMP"
        └─► 更新 <TRADUIT> 和 <STATUS> 节点
        │
        ▼
writer.write(path)
```

**新建文件**（EETBuilder）：
```
TranslationEntryCollection
        │
        ▼
EETBuilder.build(collection, output)
        │
        ├─► 创建 <DocumentElement> 根节点
        └─► 遍历 collection：
                ├─► context.split(":") → GRUP, CHAMP
                ├─► id.split(":") → editor, formid
                └─► 生成 <ESP> 子节点
```

#### XT XML 写入

**更新已有文件**（XTWriter）：
```
XT_XmlParser (已有解析器)
        │
        ▼
XTWriter(parser)
        │
        ├─► 遍历 .//Content/String 节点
        ├─► 按 List 属性匹配：
        │       ├─ List=0: EDID == entry.id 左侧 (editor_id)
        │       └─ List=1: EDID == "[{entry.id 右侧}]"
        ├─► 校验 REC == entry.context
        └─► 更新 <Dest> 节点
        │
        ▼
writer.write(path)
```

**新建文件**（XTBuilder）：
```
TranslationEntryCollection
        │
        ▼
XTBuilder.build(collection, output)
        │
        ├─► 创建 <SSTXMLRessources> 根节点
        └─► 遍历 collection：
                ├─► 生成 String List="0" (EDID=editor_id)
                └─► 生成 String List="1" (EDID=[form_id|index])
```

**双 String 节点**：每个条目生成两个 `<String>` 节点，兼容不同的匹配策略。

### ParaTranz 工作流

#### 上传工作流

```
TranslationEntryCollection
        │
        ▼
ParaTranzUploader.upload_collection()
        │
        ├─► export_to_categorized_json_files(collection, tmp_dir)
        │       → {NPC_}.json, {BOOK}.json, ...
        │
        ├─► 获取已有文件列表 → name → id 映射
        │
        └─► 逐文件处理：
                ├─ 已存在 → reupload_file（更新原文）
                │            └─ [translation_mode != "none"] → update_file_translation
                └─ 不存在 → upload_file（新建）
```

**translation_mode 选项**:
- `"none"`: 仅更新原文，不碰译文（默认）
- `"safe"`: 导入译文，不覆盖人工编辑
- `"force"`: 强制覆盖所有译文

#### 下载工作流

```
ParaTranzDownloader.download_to_collection(project_id, collection)
        │
        ├─► 获取项目文件列表
        │
        └─► 逐文件处理：
                ├─ get_file_translation() → strings
                └─ 遍历 strings：
                        ├─ stage < min_stage → 跳过
                        ├─ key 不匹配本地 → 跳过
                        └─ 匹配成功 → 更新 collection entry
```

**匹配规则**: ParaTranz `key` == 本地 `entry.id`

#### 导出工作流

```
ArtifactWorkflow.trigger_and_download(project_id, save_path)
        │
        ├─► 记录当前最新 artifact 的 createdAt (t0)
        │
        ├─► trigger_export() 触发新导出
        │
        ├─► 轮询 get_artifacts()，等待 createdAt > t0
        │
        └─► download_artifacts() → zip 文件
```

### AI翻译流程

```
Collection ──筛选(stage==0)──> 待翻译条目 ──批次规划──> 批次列表
                                                      │
                                                      ▼
批次 ──术语匹配──> Prompt ──LLM调用──> 响应 ──解析──> 译文
                                                      │
                                                      ▼
                                          更新Collection + 术语库
```

### 详细AI翻译流程

```
┌──────────────────────────────────────────────────────────────────┐
│                        AutoTranslator                            │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  1. 筛选待翻译条目                                               │
│     if overwrite: collection 全部条目                           │
│     else: collection.filter(lambda e: not e.translation)        │
│                           │                                      │
│                           ▼                                      │
│  2. 批次规划 (BatchPlanner.plan)                                 │
│     Round1: 命名实体 → 并发执行                                  │
│     Round2: 对话（按 quest 分组）→ quest 间并发，quest 内串行    │
│     Round3: 长文本 → 并发执行                                    │
│                           │                                      │
│                           ▼                                      │
│  3. 执行批次 (_run_batch)                                        │
│     ├─► 术语匹配 (TermDatabaseManager.match_terms)               │
│     ├─► 构建 Prompt (PromptBuilder.build_translation_prompt)     │
│     ├─► LLM 调用 (_monitored_chat，支持暂停/停止中断)            │
│     ├─► 解析响应 (PromptBuilder.parse_translation_response)      │
│     ├─► 更新 Collection (stage=1)                                │
│     ├─► [Round1] 自动写入动态术语库                              │
│     └─► [Round2] 调用 NounExtractor 抽取专有名词                 │
│                           │                                      │
│                           ▼                                      │
│  4. 保存断点 (ProgressCheckpoint.save)                           │
│     每批次完成后保存，翻译完成后删除                              │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

**三轮并发策略**:

| 轮次 | 目标 | 并发模式 |
|------|------|----------|
| Round1 | 命名实体 | 全部并发 |
| Round2 | 对话（按 quest 分组） | quest 间并发，quest 内串行 |
| Round3 | 长文本 | 全部并发 |

---

## 全局状态管理

### AppContext

全局唯一实例，通过Qt信号同步状态。定义于 `src/transbridge/ui/context.py`。

#### CollectionSlot

单次解析结果的数据容器，`AppContext` 通过 `_slots: dict[str, CollectionSlot]` 管理多个集合。

```python
@dataclass
class CollectionSlot:
    label: str                          # ComboBox 显示名（文件 stem）
    collection: TranslationEntryCollection
    esp_path: str | None = None
    eet_path: str | None = None
    xt_path: str | None = None
    migrate_count: int = 0
    plugin: object = None               # Plugin 实例（EET 模式为 None）
    strings_lookup: object = None       # 本地化插件的 PluginStringsLookup
```

#### AppContext 结构

```python
class AppContext(QObject):
    # 信号
    config_changed = pyqtSignal()
    user_changed = pyqtSignal()
    project_selected = pyqtSignal()
    collection_changed = pyqtSignal()   # 活跃集合切换或内容变化
    collection_list_changed = pyqtSignal()  # 集合增删

    # 多集合内部结构
    _slots: dict[str, CollectionSlot]   # key = esp/eet 文件全路径
    _active_key: str | None

    # 向后兼容的委托 property（均委托到 active_slot）
    collection: Collection              # 活跃集合
    esp_path: str                       # 活跃集合的 ESP 路径
    eet_path / xt_path: str             # 活跃集合的 XML 路径
    migrate_count: int
    plugin / strings_lookup             # 活跃集合的解析器实例

    # 多集合管理方法
    def add_slot(key, slot): ...        # 注册槽位 → 激活 → 触发双信号
    def remove_slot(key): ...           # 移除 → 自动切换 → 触发双信号
    def activate_slot(key): ...         # 激活 → 触发 collection_changed
```

**向后兼容设计**：所有下游组件仍通过 `ctx.collection`、`ctx.esp_path` 等访问数据，无需感知多集合机制。新集合注册或切换时 `collection_changed` 信号照常广播，下游行为不变。

### 信号流程

```
┌───────────────────────────────────────────────────────────────┐
│                         AppContext                             │
├───────────────────────────────────────────────────────────────┤
│                                                               │
│  config_changed ──────────────> MainWindow._on_config_changed │
│                                ProjectListPanel._on_config_changed
│                                更新UI配置显示                  │
│                                                               │
│  user_changed ───────────────> MainWindow._on_user_changed    │
│                                更新状态栏用户标签              │
│                                                               │
│  project_selected ───────────> MainWindow._on_project_selected│
│                                Step3OpsWidget._on_project_changed
│                                各Tab加载项目数据               │
│                                                               │
│  collection_changed ─────────> MainWindow._on_collection_changed
│                                Step2PreviewWidget.refresh      │
│                                CollectionStatsPanel.refresh    │
│                                Step3OpsWidget._on_collection_changed
│                                                               │
│  collection_list_changed ───> Step1SourceWidget._refresh_combo│
│                                （集合 ComboBox 重建）          │
│                                                               │
│  navigate_to ────────────────> MainWindow._on_navigate_to     │
│                                切换主Tab                       │
│                                                               │
│  project_list_changed ───────> ProjectListPanel.load_projects │
│                                刷新项目列表                    │
│                                                               │
└───────────────────────────────────────────────────────────────┘
```

### 信号总线

```python
# HTTP错误总线（集中处理认证错误）
_http_error_bus.http_error ──(status, msg)──> MainWindow._on_http_error
                                             401 → 弹出配置对话框
                                             403 → 显示权限不足提示

# API状态总线（状态栏指示器）
_api_status_bus.request_started  ──> _ApiStatusIndicator.on_request_started
_api_status_bus.request_finished ──> _ApiStatusIndicator.on_request_finished
                                      绿点/转圈动画/红点
```

---

## 配置管理

### 配置文件结构

```ini
; data/paratranz_config.ini

[api]
token = your_paratranz_token
base_url = https://paratranz.cn/api
timeout = 30
user_id =

[llm]
provider = openai              ; openai | anthropic | local
api_key = your_api_key
model = gpt-4o
base_url =                     ; 可选，用于本地模型
max_concurrent = 3
max_retries = 3
batch_size = 20
term_priority = dynamic,paratranz,json,excel
local_json_path =
local_excel_path =
excel_original_col = A
excel_translation_col = B
```

### 配置类

| 类 | 路径 | 管理的section |
|----|------|---------------|
| `ParatranzConfig` | `paratranz/config_manager.py` | `[api]` |
| `LLMConfig` | `paratranz/config_manager.py` | `[llm]` |

**关键特性**:
- 两个配置类共享同一 INI 文件，互不干扰
- `ParatranzConfig.get_data_dir()` 自动适应打包/开发环境
- `create_or_load()` 工厂方法处理文件不存在的情况

### 数据目录

| 环境 | 路径 |
|------|------|
| 开发环境 | `{项目根}/data/` |
| 打包环境 | `%APPDATA%/TransBridge/data/` |

获取数据目录：
```python
data_dir = ParatranzConfig.get_data_dir()
```

---

## 设计决策

### 1. TranslationEntry作为统一数据模型

所有来源（ESP/EET/XT）的翻译条目统一转换为 `TranslationEntry`，后续处理无需关心来源格式。

**好处**：
- 解耦解析器和后续处理逻辑
- 便于扩展新的数据源
- 统一的序列化/反序列化接口

详见 [DATA_STRUCTURES.md](DATA_STRUCTURES.md#translationentry)

### 2. Collection作为数据中枢

`TranslationEntryCollection` 是唯一的数据容器，所有操作（编辑、导出、AI翻译）都基于它。

**好处**：
- 单一数据源，避免状态不一致
- 统一的事件通知机制
- 便于持久化和恢复

详见 [DATA_STRUCTURES.md](DATA_STRUCTURES.md#translationentrycollection)

### 3. 双索引设计

Collection 同时维护 `id索引` 和 `key索引`，支持两种查找方式：

```python
# 内部结构
class TranslationEntryCollection:
    _entries: dict[str, TranslationEntry]    # id → entry 主索引
    _key_index: dict[str, TranslationEntry]  # key → entry 辅助索引

# 精确匹配（推荐）
entry = collection.get("MyNPC:000123AB|1~NPC_:FULL")

# 兼容历史数据
entry = collection.get_by_key("MyNPC:000123AB|1~NPC_:FULL")
```

`key` 字段与 `id` 相同，仅为避免破坏现有序列化数据。新代码应统一使用 `id`。

### 4. 三轮翻译策略

AI翻译按专有名词→对话→长文本分三轮执行：

| 轮次 | 目标 | 目的 |
|------|------|------|
| Round1 | 命名实体（NPC名、书籍名等） | 先翻译专有名词，建立术语基础 |
| Round2 | 对话（按quest分组） | 同一任务的对话上下文连贯 |
| Round3 | 长文本（描述、日志等） | 最后处理需要完整上下文的文本 |

### 5. 断点续传

翻译进度持久化到 `data/{esp_stem}_progress.json`：

```json
{
  "esp_stem": "MyMod",
  "target_entry_ids": null,
  "overwrite": false,
  "completed_fingerprints": [
    ["id1", "id2", "id3"],
    ["id4", "id5"]
  ],
  "result_so_far": {
    "success_count": 350,
    "failed_count": 12,
    "new_dynamic_terms": 89
  }
}
```

**断点生命周期**:
- 翻译开始时加载（如果存在）
- 每批次完成后保存
- 翻译完成后自动删除
- 中断后重新启动会恢复

### 6. ApiWorker后台线程

所有API请求和耗时操作在后台执行，避免阻塞UI：

```python
class ApiWorker(QThread):
    result = pyqtSignal(object)        # 成功时发射结果
    error = pyqtSignal(str)            # 失败时发射错误信息（401/403除外）
    progress = pyqtSignal(int, int, str)  # 进度回调

    def run(self):
        _api_status_bus.request_started.emit()
        try:
            result = self._fn(*self._args, **self._kwargs)
            self.result.emit(result)
            _api_status_bus.request_finished.emit(True)
        except Exception as exc:
            status = _parse_http_status(str(exc))
            if status in {401, 403}:
                # 路由到全局错误总线，不触发worker.error
                _http_error_bus.http_error.emit(status, str(exc))
            else:
                self.error.emit(str(exc))
            _api_status_bus.request_finished.emit(False)
```

**关键约定**：
- `self._workers.append(worker)` 必须保留引用，否则会被GC
- 401/403错误通过全局总线处理，不会触发`error`信号
- 支持`make_progress_callback()`返回可在线程中调用的进度回调

### 7. 术语库优先级

术语合并遵循优先级规则，确保人工干预不被自动覆盖：

```
manual > auto_name/auto_dialogue > paratranz > json > excel
```

详见 [DATA_STRUCTURES.md](DATA_STRUCTURES.md#termentry)

---

## 扩展点

### 新增解析器

1. 在 `parser/` 下实现解析逻辑
2. 定义解析结果的dataclass（如 `XXX_Entry`）
3. 在 `TranslationEntry` 添加 `create_from_xxx()` 工厂方法
4. 在 `TranslationEntryCollection` 添加 `from_xxx()` 类方法或 `update_from_xxx()` 方法

### 新增LLM提供商

1. 在 `ai_translator/llm_client.py` 实现客户端类：
   - 继承 `LLMClient` 抽象基类
   - 实现 `chat(messages, max_tokens)` 方法
   - 实现 `cancel()` 方法（中断请求）
2. 在 `create_llm_client()` 工厂方法中注册新 provider
3. 在 `LLMConfig` 添加相关配置项（`paratranz/config_manager.py`）
4. 如需自定义 Prompt 模板，在 `data/prompts/` 下添加配置文件

### 新增导出格式

1. 在 `writer/` 下实现写入器类：
   - 更新模式：需要 `__init__(parser)` + `apply_collection(collection)` + `write(path)`
   - 新建模式：提供静态 `build(collection, output)` 方法
2. 在 `ui/workbench/step3.py` 添加导出选项
3. 更新 `INDEX.md` 和本文档的写入流程部分

### 新增术语来源

1. 在 `TermDatabaseManager` 添加 `_load_xxx()` 方法，返回 `list[TermEntry]`
2. 在 `_load_all_with_metadata()` 的 `loaders` 字典中注册新来源
3. 在 `LLMConfig.term_priority` 默认值中添加新来源（低优先级在前）
4. 在 `LLMConfig` 添加相关配置项（文件路径等）

---

## 文件组织

```
src/transbridge/
├── __init__.py
├── main.py                    # CLI入口
│
├── converter/                 # 核心数据结构
│   ├── __init__.py
│   ├── translation_entry.py           # 翻译条目数据类
│   ├── translation_entry_collection.py # 条目集合管理
│   ├── translation_entry_collection_export.py # 分类导出工具
│   └── context_categories.py          # Context分类常量
│
├── parser/                    # 文件解析
│   ├── __init__.py
│   ├── plugin_parser.py       # ESP/ESM解析入口
│   ├── eet_parser.py          # EET XML解析
│   ├── xt_parser.py           # XT XML解析
│   ├── strings_file.py        # .strings/.dlstrings/.ilstrings读写
│   ├── plugin/                # ESP/ESM解析核心
│   │   ├── __init__.py
│   │   ├── plugin_with_context.py      # SSEPluginWithContext
│   │   ├── plugin_string_with_context.py
│   │   └── item.py            # NPCContext, InfoContext, DialContext
│   └── utils/
│       ├── __init__.py
│       └── fromid_trans.py    # FormID转换工具
│
├── writer/                    # 文件写入
│   ├── __init__.py
│   ├── plugin_writer.py       # ESP/ESM写入器（inline/localised模式）
│   ├── eet_xml_writer.py      # EET XML更新器
│   ├── eet_xml_builder.py     # EET XML构建器（新建）
│   ├── xt_xml_writer.py       # XT XML更新器
│   └── xt_xml_builder.py      # XT XML构建器（新建）
│
├── paratranz/                 # ParaTranz集成
│   ├── __init__.py
│   ├── config_manager.py      # 配置管理
│   ├── api/                   # API客户端
│   └── workflow/              # 工作流
│
├── ai_translator/             # AI翻译
│   ├── __init__.py
│   ├── term_database.py       # 术语库
│   ├── llm_client.py          # LLM客户端
│   ├── prompt_builder.py      # Prompt构建
│   ├── batch_planner.py       # 批次规划
│   ├── noun_extractor.py      # 名词提取
│   └── translator.py          # 翻译控制器
│
└── ui/                        # 用户界面
    ├── __init__.py
    ├── app.py                 # QApplication
    ├── context.py             # AppContext
    ├── main_window.py         # 主窗口
    ├── workers.py             # ApiWorker + 全局信号总线
    ├── workbench/             # 翻译工作台
    │   ├── widget.py          # 工作台主Widget
    │   ├── step1.py           # Step1: 解析插件
    │   ├── step2.py           # Step2: 词条预览（多选/筛选）
    │   ├── step3.py           # Step3: 操作面板
    │   ├── stats_panel.py     # 左侧统计面板
    │   ├── project_prompt_overlay.py
    │   └── cards/             # 操作卡片
    │       ├── base.py
    │       ├── upload_card.py
    │       ├── download_card.py
    │       └── write_card.py
    ├── tools/                 # 浮动工具窗口
    │   └── ai_translator_window.py  # AI翻译配置/进度窗口
    └── paratranz/             # ParaTranz管理
        ├── widget.py          # 管理面板主Widget
        ├── config_dialog.py   # API配置对话框
        ├── project_panel.py   # 项目列表面板
        ├── overview_tab.py    # 概览Tab
        ├── files_tab.py       # 文件管理Tab
        ├── strings_tab.py     # 词条管理Tab
        ├── terms_tab.py       # 术语管理Tab
        ├── members_tab.py     # 成员管理Tab
        ├── history_tab.py     # 历史记录Tab
        ├── contribution_tab.py # 贡献统计Tab
        ├── export_tab.py      # 导出管理Tab
        └── issues_tab.py      # 讨论Tab
```

---

## 相关文档

- [DATA_STRUCTURES.md](DATA_STRUCTURES.md) - 核心数据结构详解
- [INDEX.md](INDEX.md) - 文档索引
