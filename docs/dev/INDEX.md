# TransBridge 代码文档索引

## 项目简介

TransBridge 是一款 SSE (Skyrim Special Edition) Mod 本地化工具，支持 ESP/ESM 插件、EET/XT XML 与 ParaTranz 平台之间的翻译条目转换、上传和同步。内置 AI 自动翻译功能，支持多轮批量翻译。

## 核心数据结构文档

| 文档 | 说明 |
|------|------|
| [DATA_STRUCTURES.md](DATA_STRUCTURES.md) | 核心数据结构详解：TranslationEntry、Collection、TermEntry、PluginStringWithContext、EET_Entry、XT_Entry |

## 模块文档

| 模块 | 职责 | 文档 |
|------|------|------|
| converter | 翻译条目核心数据结构（TranslationEntry/Collection），系统数据中枢 | [converter.md](converter.md) |
| parser | ESP/ESM/ESL文件解析，上下文提取，本地化strings支持 | [parser.md](parser.md) |
| writer | ESP/EET/XT文件写入 | [writer.md](writer.md) |
| paratranz | ParaTranz 平台集成：API 客户端、配置管理、上传/下载/导出工作流 | [paratranz.md](paratranz.md) |
| ai_translator | AI自动翻译：术语库管理、LLM客户端、批次规划、翻译控制器、向量语义检索 | [ai_translator.md](ai_translator.md) |
| post_process_report | AI翻译后处理报告：Excel报告结构、生成机制、UI交互与集成点 | [post_process_report.md](post_process_report.md) |
| ui | PyQt6用户界面：主窗口、工作台（三步流程）、ParaTranz管理面板、AI翻译浮动窗口 | [ui.md](ui.md) |

## 架构概览

| 文档 | 说明 |
|------|------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | 系统架构、模块依赖、数据流、全局状态管理、设计决策 |

## 快速导航

### 入口点

| 入口 | 路径 | 说明 |
|------|------|------|
| 主程序入口 | `src/transbridge/main.py` | CLI入口 |
| UI入口 | `src/transbridge/ui/app.py` | QApplication初始化 |
| 主窗口 | `src/transbridge/ui/main_window.py` | QMainWindow实现 |

### 核心类

| 类名 | 路径 | 说明 |
|------|------|------|
| `TranslationEntry` | `src/transbridge/converter/translation_entry.py` | 翻译条目数据单元，字段：id/key/original/translation/stage/context/string_id/form_id_with_plugin/dsd_type/dsd_index/editor_id |
| `TranslationEntryCollection` | `src/transbridge/converter/translation_entry_collection.py` | 翻译条目集合，双索引设计，支持导入/导出/更新操作 |
| `context_categories` | `src/transbridge/converter/context_categories.py` | Context分类常量（三轮翻译/导出分文件规则） |
| `TermEntry` | `src/transbridge/ai_translator/term_database.py` | 术语条目 |
| `PluginParser` | `src/transbridge/parser/plugin_parser.py` | ESP/ESM/ESL解析入口 |
| `SSEPluginWithContext` | `src/transbridge/parser/plugin/plugin_with_context.py` | 带上下文的插件解析器 |
| `PluginStringWithContext` | `src/transbridge/parser/plugin/plugin_string_with_context.py` | 插件解析中间结构 |
| `NPCContext` / `InfoContext` / `DialContext` | `src/transbridge/parser/plugin/item.py` | 上下文数据类 |
| `PluginStringsLookup` | `src/transbridge/parser/strings_file.py` | 本地化字符串查表 |
| `PluginStringsWriter` | `src/transbridge/parser/strings_file.py` | 本地化字符串写入器 |
| `EET_Entry` / `EET_XmlParser` | `src/transbridge/parser/eet_parser.py` | EET XML条目与解析器 |
| `XT_Entry` / `XT_XmlParser` | `src/transbridge/parser/xt_parser.py` | XT XML条目与解析器 |
| `PluginWriter` | `src/transbridge/writer/plugin_writer.py` | ESP/ESM写入器，支持inline/localised模式 |
| `EETWriter` / `EETBuilder` | `src/transbridge/writer/eet_xml_*.py` | EET XML更新器/构建器 |
| `XTWriter` / `XTBuilder` | `src/transbridge/writer/xt_xml_*.py` | XT XML更新器/构建器 |
| `AutoTranslator` | `src/transbridge/ai_translator/translator.py` | AI翻译控制器，支持暂停/停止/断点续传 |
| `TranslatorConfig` | `src/transbridge/ai_translator/translator.py` | 翻译配置（llm_config/esp_path/overwrite） |
| `TranslationResult` | `src/transbridge/ai_translator/translator.py` | 翻译结果（success/failed/skipped/new_terms） |
| `ProgressCheckpoint` | `src/transbridge/ai_translator/translator.py` | 断点续传数据类 |
| `TermDatabaseManager` | `src/transbridge/ai_translator/term_database.py` | 四来源术语库管理器，支持向量语义检索 |
| `DynamicTermDatabase` | `src/transbridge/ai_translator/term_database.py` | 动态术语库（按ESP绑定） |
| `TermEntry` | `src/transbridge/ai_translator/term_database.py` | 术语条目数据类 |
| `TermVectorIndex` | `src/transbridge/ai_translator/term_vector_index.py` | FAISS 向量索引，支持语义检索（可选依赖） |
| `VectorSearchResult` | `src/transbridge/ai_translator/term_vector_index.py` | 语义检索结果数据类 |
| `EmbeddingClient` | `src/transbridge/ai_translator/embedding_client.py` | Embedding 客户端抽象基类 |
| `LocalSentenceTransformerClient` | `src/transbridge/ai_translator/embedding_client.py` | 本地 sentence-transformers 模型实现 |
| `OpenAIEmbeddingClient` | `src/transbridge/ai_translator/embedding_client.py` | OpenAI 兼容 API 实现 |
| `create_embedding_client` | `src/transbridge/ai_translator/embedding_client.py` | Embedding 客户端工厂函数 |
| `BatchPlanner` | `src/transbridge/ai_translator/batch_planner.py` | 批次规划器（三轮策略） |
| `Batch` / `BatchPlan` | `src/transbridge/ai_translator/batch_planner.py` | 批次数据类 |
| `PromptBuilder` | `src/transbridge/ai_translator/prompt_builder.py` | Prompt构建器（支持TOML模板） |
| `LLMClient` | `src/transbridge/ai_translator/llm_client.py` | LLM客户端抽象基类 |
| `OpenAICompatibleClient` | `src/transbridge/ai_translator/llm_client.py` | OpenAI兼容客户端 |
| `AnthropicClient` | `src/transbridge/ai_translator/llm_client.py` | Anthropic客户端 |
| `NounExtractor` | `src/transbridge/ai_translator/noun_extractor.py` | 专有名词抽取器 |
| `LLMRefiner` | `src/transbridge/ai_translator/post_processor/llm_refiner.py` | LLM修复智能体（专注修复问题） |
| `LLMPolisher` | `src/transbridge/ai_translator/post_processor/polisher.py` | LLM润色智能体（专注提升质量） |
| `LLMArbiter` | `src/transbridge/ai_translator/post_processor/llm_arbiter.py` | LLM质量裁决智能体 |
| `RefineResult` | `src/transbridge/ai_translator/post_processor/llm_refiner.py` | 修复结果数据类 |
| `PolishResult` | `src/transbridge/ai_translator/post_processor/polisher.py` | 润色结果数据类 |
| `ArbiterDecision` | `src/transbridge/ai_translator/post_processor/llm_arbiter.py` | 裁决结果数据类 |
| `ArbitrationContext` | `src/transbridge/ai_translator/post_processor/llm_arbiter.py` | 裁决上下文数据类 |
| `PostProcessorConfig` | `src/transbridge/ai_translator/post_processor/post_processor.py` | 后处理器配置（14个字段，支持从LLMConfig加载） |
| `PostProcessor` | `src/transbridge/ai_translator/post_processor/post_processor.py` | 后处理主控器，协调五阶段执行 |
| `PostProcessCheckpoint` | `src/transbridge/ai_translator/post_processor/checkpoint.py` | 后处理断点续传数据类 |
| `ParatranzConfig` | `src/transbridge/paratranz/config_manager.py` | ParaTranz API 配置管理器，INI 持久化 |
| `LLMConfig` | `src/transbridge/paratranz/config_manager.py` | AI 翻译配置管理器，共享 INI 文件 [llm] 节 |
| `ParatranzClient` | `src/transbridge/paratranz/paratranz_client.py` | API 客户端基类，HTTP 请求封装 |
| `ParatranzFilesAPI` | `src/transbridge/paratranz/api/paratranz_files_api.py` | 文件上传/下载/管理 API |
| `list_files_with_path` | `src/transbridge/paratranz/api/paratranz_files_api.py` | 返回完整路径（path/name）到 file_id 的映射 |
| `find_file_by_name` | `src/transbridge/paratranz/api/paratranz_files_api.py` | 根据文件名查找所有匹配的文件（支持同名文件在不同路径） |
| `ParatranzStringsAPI` | `src/transbridge/paratranz/api/paratranz_strings_api.py` | 翻译条目 CRUD API |
| `ParatranzTermsAPI` | `src/transbridge/paratranz/api/paratranz_terms_api.py` | 术语管理 API |
| `ParatranzExportAPI` | `src/transbridge/paratranz/api/paratranz_export_api.py` | 导出翻译文件 API |
| `ParaTranzUploader` | `src/transbridge/paratranz/workflow/uploader.py` | 上传工作流（Collection → ParaTranz），支持 `path_mapping`、`file_id_override`、`prefetched_maps` 参数，两阶段冲突检测（单次 API 调用） |
| `ParaTranzDownloader` | `src/transbridge/paratranz/workflow/downloader.py` | 下载工作流（ParaTranz → Collection） |
| `ArtifactWorkflow` | `src/transbridge/paratranz/workflow/artifact.py` | 导出工作流（触发导出 + 下载） |
| `UploadResult` | `src/transbridge/paratranz/workflow/uploader.py` | 上传结果摘要（created/updated/skipped/translation_updated/name_conflicts） |
| `ConflictInfo` | `src/transbridge/paratranz/workflow/uploader.py` | 单个文件同名冲突信息（local_name + candidates 列表）|
| `FileMaps` | `src/transbridge/paratranz/workflow/uploader.py` | 文件映射数据类（existing/path_based/name_to_files），用于两阶段流程传递 |
| `detect_conflicts` | `src/transbridge/paratranz/workflow/uploader.py` | 预检冲突，返回 `(conflicts, FileMaps)` 元组，支持 `progress_callback` |

### UI 核心类

| 类名 | 路径 | 说明 |
|------|------|------|
| `AppContext` | `src/transbridge/ui/context.py` | 全局状态持有者，通过Qt信号广播状态变化 |
| `CollectionSlot` | `src/transbridge/ui/context.py` | 单次解析结果槽位，含 collection/paths/plugin/strings_lookup |
| `ApiWorker` | `src/transbridge/ui/workers.py` | 后台线程执行器，所有API请求必须通过此类 |
| `_http_error_bus` | `src/transbridge/ui/workers.py` | 全局HTTP错误信号总线（401/403统一处理） |
| `_api_status_bus` | `src/transbridge/ui/workers.py` | 全局API状态信号总线（状态栏指示器） |
| `MainWindow` | `src/transbridge/ui/main_window.py` | 主窗口，工作台+ParaTranz管理双Tab |
| `WorkbenchWidget` | `src/transbridge/ui/workbench/widget.py` | 翻译工作台主界面（左统计+右三步） |
| `Step1SourceWidget` | `src/transbridge/ui/workbench/step1.py` | 步骤1：源文件解析面板（支持批量选择ESP、已加载集合追加迁移源） |
| `Step2PreviewWidget` | `src/transbridge/ui/workbench/step2.py` | 步骤2：词条预览与选择面板（多选/筛选） |
| `Step3OpsWidget` | `src/transbridge/ui/workbench/step3.py` | 步骤3：操作面板（上传/下载/写回三卡片） |
| `CollectionStatsPanel` | `src/transbridge/ui/workbench/stats_panel.py` | 左侧集合统计面板（分类树形统计） |
| `OpCard` | `src/transbridge/ui/workbench/cards/base.py` | 操作卡片基类，含主按钮和可选批量按钮 |
| `UploadCard` | `src/transbridge/ui/workbench/cards/upload_card.py` | 上传卡片（分类/普通模式 + 批量上传含模式选择） |
| `DownloadCard` | `src/transbridge/ui/workbench/cards/download_card.py` | 下载卡片（单文件/批量下载，支持分割文件自动合并） |
| `WriteCard` | `src/transbridge/ui/workbench/cards/write_card.py` | 写回卡片（ESP/EET/XT + 批量写回） |
| `_SlotSelectDialog` | `src/transbridge/ui/workbench/cards/*.py` | 批量操作插件选择对话框（全选/全不选） |
| `_BatchUploadModeDialog` | `src/transbridge/ui/workbench/cards/upload_card.py` | 批量上传模式选择对话框（已存在文件处理方式） |
| `_BatchConfirmDialog` | `src/transbridge/ui/workbench/cards/*.py` | 批量操作滚动确认对话框（最大高度 400px） |
| `_BatchResultDialog` | `src/transbridge/ui/workbench/cards/*.py` | 批量操作滚动结果对话框（最大高度 400px） |
| `_ConflictResolveDialog` | `src/transbridge/ui/workbench/cards/upload_card.py` | 分类上传前冲突解决对话框（每个冲突文件一个下拉框，让用户选择目标） |
| `AITranslatorWindow` | `src/transbridge/ui/tools/ai_translator/ai_translator_window.py` | AI翻译配置窗口（QTabWidget 三标签页布局：LLM 与模型 / 术语库 / 后处理，窗口高度 520 px） |
| `_TranslationProgressWindow` | `src/transbridge/ui/tools/ai_translator/_translation_progress_window.py` | AI翻译进度窗口（暂停/停止/后台） |
| `_TranslationWorker` | `src/transbridge/ui/tools/ai_translator/_translation_worker.py` | 翻译后台线程 |
| `_LLMLogViewer` | `src/transbridge/ui/tools/ai_translator/_llm_log_viewer.py` | LLM 原始响应日志查看窗口 |
| `_TermEditorDialog` | `src/transbridge/ui/tools/ai_translator/_term_editor_dialog.py` | 动态术语库查看对话框 |
| `_TranslationTargetDialog` | `src/transbridge/ui/tools/ai_translator/_translation_target_dialog.py` | 翻译目标选择对话框（单插件/批量） |
| `_BatchTranslationDialog` | `src/transbridge/ui/tools/ai_translator/_batch_translation_dialog.py` | 批量翻译对话框（插件排序+勾选） |
| `_BatchConfigDialog` | `src/transbridge/ui/tools/ai_translator/_batch_config_dialog.py` | 批量翻译配置对话框 |
| `_BatchTranslationWorker` | `src/transbridge/ui/tools/ai_translator/_batch_translation_worker.py` | 批量翻译后台线程 |
| `_BatchTranslationProgressWindow` | `src/transbridge/ui/tools/ai_translator/_batch_translation_progress_window.py` | 批量翻译进度窗口 |
| `_BatchLLMLogViewer` | `src/transbridge/ui/tools/ai_translator/_batch_llm_log_viewer.py` | 批量翻译 LLM 日志查看窗口 |
| `PluginTranslationResult` | `src/transbridge/ui/tools/ai_translator/_batch_translation_worker.py` | 单插件翻译结果数据类 |
| `BatchTranslationSummary` | `src/transbridge/ui/tools/ai_translator/_batch_translation_worker.py` | 批量翻译总结数据类 |
| `ParaTranzWidget` | `src/transbridge/ui/paratranz/widget.py` | ParaTranz管理面板主界面 |
| `ProjectListPanel` | `src/transbridge/ui/paratranz/project_panel.py` | 项目列表面板（全部/我参与的视图） |
| `ConfigDialog` | `src/transbridge/ui/paratranz/config_dialog.py` | API Token配置对话框 |
| `NewProjectDialog` | `src/transbridge/ui/paratranz/project_panel.py` | 新建项目对话框 |

### 配置与数据文件

| 文件 | 路径模式 | 说明 |
|------|----------|------|
| 配置文件 | `data/paratranz_config.ini` | API配置 + LLM配置 + Embedding配置 + 后处理配置 |
| AI翻译数据 | `data/ai_translator/{esp_stem}/` | 按插件隔离的AI翻译数据目录 |
| 动态术语库 | `data/ai_translator/{esp_stem}/{esp_stem}_terms.json` | 按插件绑定的术语库（AI翻译自动维护） |
| 向量索引 | `data/ai_translator/{esp_stem}/{esp_stem}_terms.faiss` | FAISS语义检索索引 |
| 向量元数据 | `data/ai_translator/{esp_stem}/{esp_stem}_terms_meta.json` | 向量索引元数据 |
| 翻译进度 | `data/ai_translator/{esp_stem}/{esp_stem}_progress.json` | AI翻译断点续传（完成后自动删除） |
| 翻译缓存 | `data/{esp_stem}_translation.json` | Collection序列化 |
| 游戏配置 | `data/prompts/games/{profile}.toml` | 游戏专属Prompt配置 |
| 语言档案 | `data/prompts/langs/{lang}.toml` | 源/目标语言名称与可选示例数据 |
| 通用翻译Prompt | `data/prompts/translation/default.toml` | 各目标语言共享的翻译模板 |
| 通用抽取Prompt | `data/prompts/extraction/default.toml` | 各目标语言共享的术语抽取模板 |

## 数据结构概览

### TranslationEntry ID格式

```
{editor_id}:{form_id}|{index}~{TYPE:FIELD}
```

| 部分 | 说明 |
|------|------|
| `editor_id` | 记录的 Editor ID，可能为 `None` |
| `form_id` | FormID 十六进制字符串 |
| `index` | 同一记录内的字段索引（从1开始） |
| `TYPE:FIELD` | 记录类型和字段名 |

示例：
- NPC名称: `MyNPC:000123AB|1~NPC_:FULL`
- 对话文本: `QuestName:000456CD|1~INFO:NAM1|000789EF`
- EET条目: `EditorID:000123AB|1~GRUP:CHAMP`

### TranslationEntry Context格式

```
{TYPE:FIELD}|{extra_info}
```

| TYPE | 说明 | extra_info |
|------|------|------------|
| `NPC_` | NPC名称 | 无 |
| `INFO` | 对话文本 | quest_formid |
| `DIAL` | 对话主题 | quest_formid |
| `BOOK` | 书籍 | 无 |
| `QUST` | 任务 | 无 |

示例：
- NPC: `NPC_:FULL`
- 对话: `INFO:NAM1|000789EF`（含quest_formid）
- 书籍: `BOOK:CNAM`

### Collection 关键方法

| 方法 | 说明 |
|------|------|
| `add(entry, overwrite=True)` | 添加/更新条目（无独立update方法） |
| `from_plugin(path)` | 从 ESP/ESM 导入 |
| `from_eet_xml(path)` | 从 EET XML 导入 |
| `apply_xt_entries(xt_entries)` | 应用 XT 译文 |
| `update_from_translated_plugin(path)` | 从已翻译插件提取译文 |
| `to_json_file(path)` | 保存为 JSON |
| `to_dsd_json_file(path)` | 导出为 DSD 格式 JSON |
| `from_dsd_json_file(path)` | 从 DSD 格式 JSON 导入 |

### DSD JSON 格式

DSD (Dynamic String Dumper) 是 Skyrim Mod 翻译的外部格式，用于 xEdit 脚本等工具。

| 变体 | 字段 | 适用类型 |
|------|------|----------|
| 基础格式 | `form_id`, `type`, `string` | FULL/DESC/SHRT/TNAM/RNAM/DNAM/RDMP 等 |
| QUST CNAM | `form_id`, `type`, `original`, `string` | QUST CNAM |
| 索引格式 | `form_id`, `type`, `index`, `string` | INFO NAM1, QUST NNAM, MESG ITXT, PERK EPF2/EPFD |
| GMST DATA | `form_id`, `editor_id`, `type`, `string` | GMST DATA |

示例：
```json
[
    {"form_id": "000123AB|Skyrim.esm", "type": "NPC_ FULL", "string": "角色名"},
    {"form_id": "000456CD|Skyrim.esm", "type": "INFO NAM1", "index": 1, "string": "对话文本"}
]
```

### TermEntry来源优先级

```
manual > auto_name/auto_dialogue > paratranz > json > excel
```

## 文件统计

- Python文件: 98个
- 主要模块: 7个（converter, parser, writer, paratranz, ai_translator, ui）
- 核心数据类: 12个
- 可选依赖: faiss-cpu (向量语义检索), sentence-transformers (本地 embedding 模型)


### 批量翻译功能

- 新增 `_TranslationTargetDialog`：翻译目标选择（当前插件/批量翻译）
- 新增 `_BatchTranslationDialog`：批量翻译对话框，支持插件拖拽排序、勾选、覆盖选项
- 新增 `_BatchConfigDialog`：简化版 LLM 配置对话框
- 新增 `_BatchTranslationWorker`：批量翻译后台线程，支持暂停/停止/断点续传
- 新增 `_BatchTranslationProgressWindow`：批量翻译进度窗口，显示总体进度和插件进度
- 新增 `_BatchLLMLogViewer`：批量翻译 LLM 日志查看窗口（两级 Tab 结构）
- 增强 `AutoTranslator`：支持外部注入共享 in-flight 术语缓存，实现多插件间术语实时共享
- 新增数据类：`PluginTranslationResult`、`BatchTranslationSummary`

### 向量语义术语检索

- 新增 `term_vector_index.py`：FAISS 向量索引，支持语义检索
- 新增 `embedding_client.py`：Embedding 客户端抽象层，支持本地模型和 API 服务
  - `LocalSentenceTransformerClient`：本地 sentence-transformers 模型
  - `OpenAIEmbeddingClient`：OpenAI 兼容 API（支持 OpenAI/DeepSeek/阿里云等）
- 增强 `match_terms()`：正向子串 + 冠词规范化 + 反向前缀/后缀
- 新增 `match_terms_enhanced()`：两阶段召回（精确 + 语义）
- 新增 `_in_flight_terms` 缓存：并发批次间术语实时共享
- 新增配置项：`enable_semantic_match`, `semantic_similarity_threshold`, `semantic_top_k`, `max_terms_per_batch`
- 新增后处理配置项：`enable_post_process` - 控制翻译完成后是否进行质量检查（默认启用）
- 新增后处理细粒度配置项（14个字段）：
  - 检测阶段：`pp_enable_consistency_check`, `pp_enable_format_validation`, `pp_enable_quality_gate`
  - 修复阶段：`pp_enable_refinement`, `pp_refinement_batch_size`
  - 润色阶段：`pp_enable_polish`, `pp_polish_scope`, `pp_polish_level`, `pp_polish_batch_size`
  - 裁决阶段：`pp_enable_arbitration`, `pp_strict_arbitration`, `pp_arbitration_batch_size`
- 新增 Embedding 配置项：`embedding_provider`, `embedding_model`, `embedding_api_key`, `embedding_base_url`, `embedding_local_model`
- 数据目录迁移：`data/` → `data/ai_translator/{esp_stem}/`

### 批量下载分割文件支持

- 增强 `DownloadCard`：批量下载时自动检测并合并分割文件
  - 新增 `_find_split_files()`：查找 `Plugin.json`、`Plugin_1.json`、`Plugin_2.json` 等分割文件
  - 批量下载现在会下载所有匹配的分割文件并合并到同一集合
  - 结果详情显示所有合并的文件名列表

### AI翻译后处理五阶段系统

- 新增 `LLMRefiner`：LLM修复智能体
  - 专注修复检测到的问题
  - 输出 confidence 信心度和 needs_arbitration 标记
  - 支持批量处理减少LLM调用
- 新增 `LLMPolisher`：LLM润色智能体（独立阶段）
  - 专注提升译文流畅度和风格
  - 支持三种润色范围：all/passed/has_issues
  - 支持三种润色级别：light/moderate/aggressive
  - 无需前置问题检测，可独立启用
- 新增 `LLMArbiter`：LLM质量裁决智能体
  - 对修复和润色结果做最终裁决（pass/reject/pending）
  - 快速判定规则：无需LLM即可处理明确场景
  - 支持严格模式（uncertain→reject）和普通模式（uncertain→pending）
- 新增提示词配置目录：
  - `data/prompts/refinement/default.toml`：通用修复提示词
  - `data/prompts/polish/default.toml`：通用润色提示词
  - `data/prompts/arbitration/default.toml`：通用裁决提示词
- 五阶段协作流程：
  - 阶段1: 检测（QualityGate + Consistency + Format）
  - 阶段2: 修复（LLMRefiner）
  - 阶段3: 润色（LLMPolisher，可选）
  - 阶段4: 裁决（LLMArbiter）
  - 阶段5: 执行（更新 stage）
- 译文优先级：润色结果 > 修复结果 > 原始译文
- UI配置：AI翻译配置窗口采用 `QTabWidget` 三标签页横向布局（LLM 与模型 / 术语库 / 后处理），窗口高度从 680 px 压缩至 520 px，避免超出屏幕。
  - 原纵向堆叠的 LLM 配置、Embedding 配置、术语库来源、后处理配置分别收纳到对应标签页
  - 翻译范围和开始翻译按钮常驻可见，无需滚动
  - 各阶段独立开关和参数配置保留在后处理标签页内
    - 总开关控制是否启用后处理
    - 检测/修复/润色/裁决各阶段可独立启用/禁用
    - 润色范围可选：全部/仅通过/仅问题
    - 润色强度可选：轻微/适中/深度
    - 裁决严格模式开关
