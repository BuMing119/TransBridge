# ADR-014: FOMOD 安装包翻译流水线 + 通用翻译记忆系统

- **状态**: 已接受
- **日期**: 2026-08-14
- **决策者**: BuMing
- **对应需求**: [FR15](../requirements.md)

## Context

mod 更新时，译者需重复执行三项手工劳动：(1) 用 XTranslator/EET 刷写全部 patch 插件（ESP/ESM/ESL）的翻译词条；(2) 重翻 fomod 安装界面文本（`ModuleConfig.xml` 的 moduleName/步骤/组/插件/描述）；(3) 手工删除 mod 自带的 BSA/贴图/模型等非翻译资源以规避侵权。其中大部分内容每次并未变化，却需整包重做。

需要新增「FOMOD 安装包翻译」能力，并抽象出一个可被 FOMOD 翻译与后续批量翻译复用的「翻译记忆」系统。现有能力（`PluginParser`/`PluginWriter`/`TranslationEntryCollection`/`XT_XmlParser`/`EET_XmlParser`/`ParaTranzDownloader`/LLM 客户端）均需复用，不重造。

关键约束：解包/打包必须通过 Python 库自包含实现，**不依赖用户环境是否安装了 7-Zip**。

## Decision

### 1. 翻译记忆匹配粒度：键匹配优先 + 文本词典兜底（分层）

翻译复用采用两级策略：

1. **键匹配（精确，优先）**：以 `TranslationEntry.id`（`EditorID:FormID|index~context`）为匹配键，将旧版同插件译文迁移到新版同键条目。命中且原文文本未变的条目直接继承译文（stage=已翻译）；命中但原文被 mod 修改的条目标记为「需复核」，不直接套用。
2. **文本词典兜底（覆盖面）**：键未命中的条目（新增 / FormID 漂移）通过翻译记忆的「原文文本 → 译文」映射套用。

### 2. 文本规范化规则：精确匹配（D1）

翻译记忆的原文键按如下规则规范化后精确匹配：

- 统一换行符：`\r\n`、`\r` → `\n`
- 去除首尾空白

**不**剥离游戏标记语言（`<font>`/颜色/占位符 `<Alias=…>` 等），保持原文逐字精确比对，避免误配。复用现有 `converter/translation_entry.py::_normalize_text` 的语义。

### 3. 模块归属与目录结构：独立双包

新建两个独立包，职责正交：

```
src/transbridge/translation_memory/   # 通用翻译记忆（可被任何翻译场景复用）
    ├── __init__.py
    ├── model.py          # DictionaryEntry / Dictionary（单表权威对象 + 双索引）
    └── manager.py        # TranslationMemoryManager：定位/查询/写入/持久化

src/transbridge/fomod/                  # FOMOD 安装包翻译流水线（FR16 后瘦身）
    ├── __init__.py
    ├── fomod_xml.py      # ModuleConfig.xml / info.xml 解析与翻译（UTF-16LE 处理）
    ├── builder.py        # 输出组装编排（复用 fileops/filter_rules.py 的过滤 + 目录复制/打包）
    └── pipeline.py       # 流水线编排（解包→diff→迁移→翻译→组装→打包）
```

> 勘误（2026-08-14）：本决策初版目录写了 `entry.py`/`store.py`/`sources/` 子包，经议会评审后收敛为 `model.py`（数据类）+ `manager.py`（逻辑），以代码实际落地为准。`sources/` 可插拔抽取器子包本次不实现（「导入翻译」能力已由现有 parser/paratranz 提供，翻译记忆仅负责「保存 + 使用」）。
>
> 勘误（2026-08-14，FR16）：原 fomod 包中的通用能力已独立为 FR16 通用工具——`archive.py`→`fileops/archive.py`、`differ.py`→`fileops/differ.py`、过滤规则→`fileops/filter_rules.py`、键对齐迁移→`migrator/key_migrator.py`。fomod 包瘦身为仅保留 FOMOD 特有逻辑（`fomod_xml.py` + `builder.py` 组装编排 + `pipeline.py`）。见 ADR-015。

### 3.1 翻译记忆词典模型：两档 scope + 单表权威对象 + 双索引 + 逐级兜底

翻译记忆（`translation_memory/`）的内部模型：

**两档 scope（词典定位）**：
- `scope` ∈ {`project`（项目专属）、`global`（全局共享）}
- `global` 词典可带 `scope_id` 作为「游戏/领域」标签（如 `skyrim_se`），用于全局词典内按游戏过滤；`project` 词典的 `scope_id` 为项目名
- 定位键为 `(scope, scope_id)`，manager 内唯一索引

**每本词典内部：单表权威对象 + 双索引**：
- `entries: dict[entry_id, DictionaryEntry]` —— 权威对象表（译文只存一份）
- `key_index: dict[complete_key, {entry_id, hits}]` —— 键索引（`EditorID:FormID|index~context` → 权威对象 + 键命中计数）
- `text_index: dict[normalized_original, {entry_id, hits}]` —— 文本索引（规范化原文 → 权威对象 + 文本命中计数）
- 命中计数 `hits` 落在索引值上（键/文本两条路径各自独立计数），权威对象不含 hit_count，彻底消除双表共享可变对象在 save/load 后分裂的问题

**查询顺序（逐级兜底，键表优先）**：
```
query(complete_key, original, context)
  当前 project 词典 → 键索引命中 → 返回（EXACT/STALE）
                    → 文本索引命中 → 收集候选
  global 词典      → 同上
  未命中 → None
```
- **键命中即停**（键=同 mod 精确，可信度最高，不做候选收集），返回时携带**原文一致性**判定：`EXACT`（键表记录的 original 与传入原文一致）/ `STALE`（原文已变化，需复核，不直接套用）
- **文本命中收集候选**后统一仲裁：project 词典优先于 global 词典
- 匹配键的取值来源是 `TranslationEntry.id`，但存储层把 complete_key 当**不透明字符串**处理，不解析其内部 FormID 结构

**冲突处理**：不同词典命中同一原文但译文不同 → project 优先于 global；同级（现仅两档下不会出现）保留最早导入 + 冲突报告。

**词典标签**：词条的自由管理标签（字段名 `tags`），仅用于词典面板内的**筛选与查看**，**不参与翻译匹配**。与 FR7.11 的「词条标签」（标记翻译工作状态、作用于 Step2 表格词条）是两个独立概念：「词条标签」面向待翻译词条的工作流标记，「词典标签」面向词典库内已存译文的归组筛选。

这一模型面向三个真实场景：单一 mod（键索引）、跨 mod 复用通用文本（global 文本索引）、跨游戏共享（global + scope_id 标签过滤）。

> 勘误（2026-08-14）：本决策初版为「三级 scope（project/game/global）+ 双层独立副本存储 + 同级多词典仲裁」，经议会评审收敛为「两档 scope + 单表权威对象 + 双索引 + 键原文一致性 EXACT/STALE」。`game` 档降级为 global 词典的 `scope_id` 标签；删除同级多词典仲裁（两档下每作用域内仅一本词典，同级冲突在数据模型中不存在）。

### 4. 归档解包/打包实现：Python 库自包含（不依赖用户环境装 7-Zip）

归档处理通过 Python 库实现，**不依赖用户环境是否安装了 7-Zip/RAR 工具**。三种格式均自动支持：

| 格式 | 库 | 说明 |
|------|-----|------|
| `.7z` | `py7zr` | 纯 Python 解压/打包 7z |
| `.zip` | `zipfile`（标准库） | 标准库内置 |
| `.rar` | `rarfile` + 捆绑 `unrar.exe` | RAR 为专有格式；`rarfile` 仅解析目录，解压委托给 RARLAB `unrar.exe`（~1MB 二进制，随 PyInstaller 分发） |

合规说明：`unrar.exe` 为 RARLAB 专有（非自由）软件，许可证允许免费使用与再分发，但禁止逆向工程与用其构建 RAR 压缩功能。本项目仅用于**解压**用户 mod 源文件，不违反许可；分发时在 LICENSE/NOTICE 明确声明并保留 unrar 许可证文本。

`archive.py` 提供统一的 `extract(archive_path, dest_dir)` / `pack(src_dir, archive_path)` 接口，内部按扩展名分派到对应后端，对上层隐藏格式差异。打包输出统一采用 `.7z`（py7zr）或 `.zip`（zipfile），不产出 `.rar`。

### 5. fomod 界面文本处理：键匹配复用 + LLM 翻译新增

`ModuleConfig.xml` 的 moduleName/installStep/group/plugin/description 文本按名称层级键与旧版对齐：同名文本直接复用旧译；新增/变化的文本通过现有 LLM 客户端翻译为中文。XML 读写正确处理 UTF-16LE 编码与 BOM（fomod 元数据约定为 UTF-16）。

### 6. 输出组装与侵权规避：扩展名白名单过滤

组装输出时按文件扩展名分类：剔除侵权资源（`.bsa`、`.dds`、`.png`、`.jpg`、`.nif`、`.wav`、`.fuz`、`.xwm` 等），保留可翻译脚本（`.pex`/`.psc`）、fomod 必需元数据（`info.xml`/`ModuleConfig.xml`/界面图片）。保留/剔除规则集中在 builder 的可配置扩展名清单中，供不同 mod 复用。

## Alternatives Considered

| 决策点 | 方案 | 选择 | 理由 |
|--------|------|------|------|
| 匹配粒度 | 纯键 / 纯文本 / **键+文本分层** | 键+文本分层 | 键匹配保证精确无歧义，文本兜底覆盖新增与 FormID 漂移 |
| 文本规范化 | **精确匹配** / 忽略标记匹配 / 忽略换行+颜色保占位符 | 精确匹配 | 避免误配；mod 大改标记的场景极罕 |
| 模块归属 | **独立双包** / 并入 fomod 包 | 独立双包 | 翻译记忆是通用能力，供批量翻译复用；职责正交 |
| 归档实现 | 系统 7z 命令行 / py7zr+zipfile / **py7zr+zipfile+rarfile(捆绑 unrar)** | py7zr+zipfile+rarfile | 自包含不依赖用户环境；RAR 经 rarfile+unrar.exe 解压（仅解压，合规），打包只出 7z/zip |
| GUI 承载 | CLI / **GUI 面板** / CLI+GUI | GUI 面板 | 用户为 GUI 用户，复用现有集合/词典/进度/确认弹窗生态 |

## Consequences

- **依赖变更**: 新增 `py7zr`（7z 解压/打包）、`rarfile`（RAR 读取，解压委托给捆绑的 `unrar.exe`）；zip 用标准库 `zipfile`。需随分发捆绑 `unrar.exe`（~1MB，专有许可证，见决策 4 合规说明）
- **目录变更**: 新增 `src/transbridge/translation_memory/`（+sources 子包）、`src/transbridge/fomod/`
- **接口变更**:
  - 新增 `TranslationMemoryManager`：`add()`、`query(complete_key, original, context) -> QueryResult`、`save_from_collection()`、`apply_to_collection()`、`load()`、`save()`、`merge()`
  - 新增 `QueryResult`（含 `translation`/`matched_scope`/`matched_via`/`match_status`）、`QueryContext`（`project_id`/`game_id`）、`ApplyResult`（`key_hits`/`text_hits`/`misses`/`applied`/`needs_review`/`conflicts`）
  - 新增 `FomodArchive.extract()/pack()` 统一归档接口（本次 fomod 包不实现，预留）
  - 复用（不改动）`PluginParser`/`PluginWriter`/`TranslationEntry`/`XT_XmlParser`/`EET_XmlParser`/`ParaTranzDownloader`/LLM 客户端
  - 复用 `persistence/_utils.py::atomic_write_json`、`validate_name`；规范化复用 `converter/translation_entry.py::_normalize_text`（本地浅封装，暂不上移）
- **正面**: 解除对用户环境 7-Zip 的依赖；翻译复用自动化，将译者从重复劳动中解放；翻译记忆成为可扩展的通用能力
- **负面**: 引入 `py7zr` 与 `rarfile` 两个新依赖；RAR 解压需捆绑专有 `unrar.exe` 二进制（分发 LICENSE/NOTICE 需声明）；翻译记忆匹配为精确比对，mod 调整标记语言时命中率下降

---

### 更新: 2026-08-14 - 词典粒度重构（一文件一 mod + 多词典组合查询）

**决策**: 本更新**替换**决策 3.1 确立的词典数据模型。经技术议会（5 专家 × 2 轮）评审 + 用户逐条确认，词典系统从「(scope, scope_id) 定位的扁平大库」重构为「一文件一 mod」粒度：

1. **词典粒度**：一本词典 = 一个模组文件（.esp/.esl/.esm，未来 .txt）的词条集合，**严格一一对应**。词典文件名为 mod 名（去扩展名），后缀 `.tbdict`（内容仍为 JSON）。scope 不再参与定位键与文件名，改为**词典文件内的单值属性标签**（project / global），支持通过 GUI 切换。
2. **数据模型**：`Dictionary{ mod_file_id, scope, entries, key_index, text_index }`；`DictionaryEntry{ translation, original, source_mod, form_id_with_plugin, imported_at, updated_at, tags }`。移除 `source` 字段（被 `source_mod` 取代）与 `scope_id` 字段（被 `mod_file_id` + 单值 scope 取代）。
3. **词条主键**：`entry_id = sha1(mod_file_id | 原文)`，**不含 scope**，保证词条身份与词典位置解耦，跨 scope 切换不换 ID。
4. **更新语义**：同名 mod 只存一本词典；重复写入时「新增追加、已存在原文覆盖新译文、从不删除」（同名 mod 不同版本合并为同一本，不保留版本历史）。mod 重名不处理（实际场景不会出现）。
5. **多词典组合查询**：翻译某 mod 时全查兜底——同名 mod 词典最优先，其次其余 project 词典，最后其余 global 词典；多词典命中同一原文且译文不同时收集冲突候选并做仲裁，提供**可视化仲裁界面**。
6. **分享与导入**：词典统一存放 `data/translation_memory/`；提供「导入词典」「导出」「打开词典目录」三个 GUI 能力。
7. **旧数据弃置**：旧 `global__skyrim_se.json` 等混装多 mod 的旧格式数据**不迁移**，重构从零开始（无存量数据需保留）。

**原因**: 用户核心诉求是「词典大家共享使用、一个 mod 一个词典文件」；当前 `entries` 混装多 mod 词条、词条缺乏「来源 mod」身份字段，无法按 mod 隔离/导出/分享。议会确认「词条缺 mod 身份」为根本缺陷，需结构性重构而非局部修补。

**影响**:
- **数据模型变更**: `model.py` 的 `Dictionary`/`DictionaryEntry` 字段重构（`source`→`source_mod`，`scope_id`→`mod_file_id`，新增 `form_id_with_plugin`）
- **定位与命名变更**: `manager.py` 的定位键从 `(scope, scope_id)` → `mod_file_id`；文件命名从 `{scope}__{scope_id}.json` → `{mod_file_id}.tbdict`
- **接口变更**: `TranslationMemoryManager` 新增 `mod_file_id` 维度；`QueryContext` 的「激活集规则」（同名 mod → project → global 全查）；`QueryResult.conflicts` 需真正填充（冲突仲裁）；新增 scope 修改、导入/导出/打开目录能力
- **废弃语义**: 决策 3.1 的「每 scope 仅一本词典」「同级冲突不存在」「game_id 过滤」均被本更新替换
- **无新依赖**: 本重构不引入新依赖；`.tbdict` 后缀为文件扩展名约定，序列化仍用现有 JSON 工具

---

### 更新: 2026-08-14 - FOMOD 流水线翻译来源与词条迁移架构（议会评审定论）

**决策**: 为 FR15.2-15.7 FOMOD 翻译流水线确立翻译来源优先级与词条迁移的接口复用原则。

#### 1. 翻译来源优先级（多级降序）

FOMOD 翻译的词条译文复用，按以下优先级从高到低依次尝试：

| 优先级 | 来源 | 匹配方式 | 说明 |
|--------|------|---------|------|
| ① | **项目已有翻译**（用户手动指定，可多选） | 键匹配 + 文本匹配 | 从用户选中的 TransBridge 项目（`data/projects/{project}/{variant}/current.json`）提取译文 |
| ② | **翻译记忆词典**（`.tbdict`） | 键匹配 + 文本匹配 | 现有词典系统，同名 mod 词典优先，再 project → global 逐级兜底 |
| ③ | **旧归档译文** | 键匹配 + 文本匹配 | **仅当 ①+② 均未命中时才直接查询**；正常情况下旧归档仅作为「词典原料」，灌入词典后即退休 |
| ④ | **AI 翻译** | 直接生成 | 最终兜底 |

**旧归档的定位**：旧版 FOMOD 归档中的译文，主要在**首次翻译时灌入词典**（持久化为「同名 mod 词典」，一文件一 mod，一个 FOMOD 含多个 ESP 则拆多本词典）。灌入后旧归档译文成为词典资产，日常翻译靠词典复用；只有当词典缺失时才回头直接查旧归档。

#### 2. 词条迁移接口复用原则（做法 1 + 键优先级）

所有翻译来源（项目译文、旧归档译文）在参与匹配前，统一**归一化灌入翻译记忆词典**，整个迁移流程**只通过 `TranslationMemoryManager.apply_to_collection()` 一个接口**完成译文套用。

**关键约束**：灌词典时必须**保留完整的 `entry.key`**（`EditorID:FormID|index~context`），不得只存「原文→译文」。这样 `apply_to_collection()` 内部自动走「键索引精确命中 → 文本索引兜底」的两级策略，天然保留键匹配的精确性（避免文本匹配误配），同时统一了接口。

`fomod/` 包**不重复实现词典匹配逻辑**，仅做「新旧归档间的键对齐迁移」这一 FOMOD 特有之事；其余一律委托 `TranslationMemoryManager`，与主工作台「套用到集合」共用同一份代码。

#### 3. fomod 界面文本翻译来源

`ModuleConfig.xml` 的 moduleName/step/group/plugin/description 文本（无 FormID 键）通过词典 `text_index` 做文本匹配复用；未命中的新增/变化文本走 AI 翻译。

#### 4. 冲突仲裁

多来源译文命中同一原文且译文不同时，**复用现有 `DictionaryConflictDialog`** 可视化仲裁界面，不针对 FOMOD 场景定制。

#### 5. 输出边界

FOMOD 翻译**只产出新中文安装包**（在解压临时目录上翻译/组装/打包），**不写回用户本地已有 ESP 文件**。写回本地 ESP 是现有工作台的职责，FOMOD 面板不重复。

**原因**: 用户实际场景中经常没有旧版 FOMOD 归档，但有 TransBridge 项目译文和词典资产；旧归档不应是硬依赖。统一走 `apply_to_collection()` 接口可保证词典匹配逻辑单一、行为与主工作台一致，`fomod/` 保持纯编配层定位（不侵蚀业务逻辑）。

**影响**:
- **接口变更**: FOMOD 面板新增「翻译来源项目」选择项（可多选）；`fomod/` 包通过适配器调用 `TranslationMemoryManager.save_from_collection()`/`apply_to_collection()`，不新增词典查询接口
- **数据流变更**: 翻译来源归一化流程 —— 项目译文 + 旧归档译文 → 灌词典（保留 entry.key）→ `apply_to_collection()` 统一套用 → AI 翻译兜底
- **无新依赖**: 本决策不引入新依赖；复用现有 `translation_memory`/`PluginParser`/`PluginWriter`/`LLMClient`

---

### 更新: 2026-08-14 - 逐插件翻译循环与 AI 兜底入口（AutoTranslator）

**决策**: pipeline.py 的 ESP 词条翻译采用**逐插件循环**，AI 兜底入口复用 `AutoTranslator` 而非裸 `LLMClient.chat()`。

**逐插件循环**（每个 .esp/.esm/.esl 独立处理）：

```
for esp in new_dir 插件:
  ① PluginParser.parse_plugin(esp) → TranslationEntryCollection
  ② 有旧版同款插件 → migrator.migrate（键对齐：继承 + needs_review）
  ③ tm.apply_to_collection（词典兜底：键索引 + 文本索引）
  ④ 剩余 stage=0 无译文 → AutoTranslator.translate（AI 兜底）
  ⑤ PluginWriter 写回 esp（解压临时目录内的副本）
```

**AI 兜底为什么用 AutoTranslator 而非裸 LLMClient**：

| 能力 | 裸 LLMClient.chat | AutoTranslator |
|------|------------------|----------------|
| 术语库匹配（术语一致性） | ❌ 需自建 | ✅ 内置 TermDatabaseManager |
| 名词提取（术语库成长） | ❌ 需自建 | ✅ 内置 NounExtractor |
| 批量分页 + 进度 + 断点续传 | ❌ 需自建 | ✅ 内置 BatchPlanner + ProgressCheckpoint |
| 后处理（可关） | ❌ | ✅ enable_post_process 开关 |

术语库匹配与名词提取是翻译正确性的**必要环节**（保证同一术语跨版本译名一致、术语库持续成长），非可省的"开销"；因此 AI 兜底复用 AutoTranslator 而非写轻量翻译循环。

**运行时上下文注入**：`FomodPipeline.__init__(rules, llm_config, tm_manager)`——`llm_config`（LLMConfig）驱动 AutoTranslator 与界面文本翻译，`tm_manager`（TranslationMemoryManager）驱动词典兜底；二者由 GUI 层注入（`LLMConfig.load_from_file()` + `TranslationMemoryManager()` 加载默认词典目录）。

**原因**: 用户明确翻译必须具备术语库与名词提取；这是 AutoTranslator 已封装的能力，逐插件循环复用即可，不重造。

**影响**: `pipeline.py` 新增 `_translate_plugins`/`_ai_translate`/`_write_back` 三个私有方法；`FomodPipeline` 构造与 `run()` 增加 `llm_config`/`tm_manager`/`progress_callback`/`stop_event` 注入参数；复用 `ai_translator.translator.AutoTranslator`（`TranslatorConfig(llm_config, esp_path)`）

### 更新：2026-08-18 — Typed Transactional FOMOD Pipeline（已接受）

FOMOD/TM 资产和“FOMOD 只产出新安装包”的边界继续有效；流水线改由 application use case 编排，现有 `FomodPipeline` 作为 compatibility adapter。每个阶段返回 typed outcome，统一使用 ADR-019 的 JobSpec、取消和互斥终态；fatal/failed/cancelled 阻止发布，所有中间产物进入 staging，验证后原子发布。

TM 记录 SHALL 保存 locale、Stage、provenance、source namespace 和 revision；匹配使用 ADR-017 EntryKey，外部 ID 不参与内部匹配。`locked(9)` 空译文阻断正式发布，hidden 写原文。归档处理统一使用 ADR-015 的 ArchivePolicy；FOMOD XML 更新保留命名空间、未翻译节点、属性和图片引用。旧“扩展名白名单即可保证安全/保真”和直接逐阶段 mutation 被本更新取代。
