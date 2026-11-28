# translation-memory（通用翻译记忆/词典系统）

**对应需求**: FR15.1（通用翻译记忆系统）
**技术模块**: backend + ui
**业务域**: 翻译基础设施
**状态**：S11–13 已实现（2026-11-26）。验证范围见[词典库](../../docs/changelogs/translation-memory/story-11-to-13/2026-11-26-001-dictionary-libraries.md)及[只读查询窗口](../../docs/changelogs/translation-memory/story-11-to-13/2026-11-26-002-read-only-lookup.md)的交付记录。 2026-11-27 补充[保存入库与套用条件](../../docs/changelogs/translation-memory/story-11-to-13/2026-11-27-003-save-and-apply.md)与[词典设置](../../docs/changelogs/translation-memory/story-11-to-13/2026-11-27-004-dictionary-settings.md)。

## 当前交付计划

对应 [FR15.1](../../docs/requirements.md)。以下 Story 11 至 13 接替早期桌面词典方案，01 至 10 为历史实施记录。

S11、S12 已接入工程的实际译名方案。保存整句时使用默认译名，套用时转换到目标正文方案。见[实现、回归及性能实测](../../docs/changelogs/translation-memory/story-11-to-13/2026-11-28-006-dictionary-default-terminology.md)。

- **S11 SQLite 保存。** 在 `translation_memory/` 实现按游戏和语言分区的个人与具名主库、活动主库持久化、事务化增量保存。验证修订重存、不同来源同文异译、取消与语言隔离。工程保存时按上次成功入库的版本读取当前来源，首次全读，后续只读变更词条。见[实现与 10 万条实测](../../docs/changelogs/translation-memory/story-11-to-13/2026-11-28-005-incremental-dictionary-save.md)。
- **S12 套用与共享入口。** 统一桌面、助手和插件打开时的来源识别、个人库及当前主库候选排序、阶段限制和修订校验。验证补丁覆盖、文本兜底、库间优先级及稳定性。历史搜索通过只读接口读取 SQLite 中各分区的全部库。
- **S13 独立词典查询窗口。** 翻译菜单及编辑器选区共用只读查询窗口。后台读取当前游戏及语言分区的个人库和所有具名主库，按双语文本检索；表格呈现来源文件、命中高亮和自动换行。验证无来源、请求取消和语言分区切换场景。

**旧格式退役**：`.tbdict` 文件留在原位置，应用移除读取、导入和迁移路径。回归覆盖旧文件原样保留、套用和查询跳过旧记录、历史搜索排除旧缓存记录，以及 FOMOD 显式候选边界。

## 历史 Story

### Story 01: 词典数据模型与持久化存储

**验收标准**:
- [ ] 定义 `DictionaryEntry`（权威对象）：translation/original/source/imported_at/updated_at/tags，不含 hit_count
- [ ] 定义 `Dictionary`（一本词典）：scope（project/global）、scope_id、entries（权威对象表）、key_index、text_index（命中计数 hits 落在索引值）
- [ ] 定义 `TranslationMemoryManager`：管理多本词典、按 `(scope, scope_id)` 定位、`add()`/`save()`/`load()`/`merge()`
- [ ] 原文键规范化复用 `_normalize_text`（统一换行 + 去首尾空白，不剥离游戏标记）
- [ ] JSON 持久化往返无损（含中文文本，`ensure_ascii=False`，顶层 schema_version，原子写）

> 详细实现指南见 `plans/translation-memory/stories/story-01-data-model.md`

### Story 02: 查询与逐级兜底

**验收标准**:
- [ ] `query(complete_key, original, context) -> QueryResult`，键索引优先（命中即停），文本索引兜底
- [ ] 逐级兜底：当前 project 词典 → global 词典
- [ ] 键命中返回原文一致性判定：EXACT（原文一致）/ STALE（原文已变，需复核）
- [ ] 文本命中收集候选后按 scope 优先级（project 优先于 global）仲裁
- [ ] 命中计数落在索引值上，键/文本两条路径独立计数

> 详细实现指南见 `plans/translation-memory/stories/story-02-query-fallback.md`

### Story 03: 存为词典（从集合落地）

**验收标准**:
- [ ] 提供「将 `TranslationEntryCollection` 的已翻译条目写入指定词典」接口
- [ ] 支持全量（整个集合）与选中子集两种粒度
- [ ] 写入时同时登记键索引（complete_key=entry.id）与文本索引（规范化原文=entry.original），指向同一权威对象
- [ ] 写入时允许指定目标词典（scope/scope_id）与词典标签
- [ ] 跳过空译文条目与 stage==9/-1（锁定/隐藏）条目；复用现有集合遍历（`for e in collection`）

> 详细实现指南见 `plans/translation-memory/stories/story-03-save-from-collection.md`

### Story 04: 套用到集合与统计

**验收标准**:
- [ ] `apply_to_collection(collection, context) -> ApplyResult`，遍历集合按词典补空译文
- [ ] 键索引优先匹配（entry.id），键未命中再走文本索引（entry.original）
- [ ] 仅填补空译文（overwrite 默认 False）；排除 stage==9/-1；命中回填对应索引 hits
- [ ] 套用结果统计（键命中数/文本命中数/未命中数/实际填充数）+ needs_review（键命中但 STALE 的条目）

> 详细实现指南见 `plans/translation-memory/stories/story-04-query-apply.md`

### Story 05: GUI 集成

**验收标准**:
- [ ] 主窗口「小工具」菜单提供「翻译词典」入口，打开词典管理面板
- [ ] 词典管理面板：查看各词典、键索引/文本索引条目、按词典/词典标签筛选、查看来源与命中计数、存为词典按钮
- [ ] 存为词典对话框：选择 scope/scope_id、粒度（整个集合/选中条目）、词典标签

> 详细实现指南见 `plans/translation-memory/stories/story-05-gui.md`

## 架构依赖

- 引用 ADR：`docs/adr/014-fomod-translation-memory.md`（决策 1 键+文本分层、决策 2 精确匹配、决策 3 独立 `translation_memory/` 包、决策 3.1 两档 scope + 单表权威对象 + 双索引 + 逐级兜底 + EXACT/STALE；**更新节 2026-08-14 词典粒度重构**）
- 依赖的技术决策：精确匹配规范化复用 `converter/translation_entry.py::_normalize_text`（本地浅封装，暂不上移）
- 依赖的接口契约：`TranslationEntryCollection`（复用 `__iter__`/`get`/`to_dict`，不修改）；持久化复用 `persistence/_utils.py::atomic_write_json`/`validate_name`

## 风险与回退方案

- **原文含游戏标记导致命中率下降**：本次按 ADR-014 采用精确匹配；若后续命中率不足，可升级为 D3（忽略换行+颜色、保留占位符）作为文本索引匹配模式选项，向后兼容
- **词典文件随累积变大**：本次按 scope 分文件（project/global 各自 `.json`），复用原子写；规模超阈值后再评估 SQLite 迁移（存储层已抽象为 manager 接口，可无痛替换）
- **词典标签不参与匹配**：若后续需要「按词典标签过滤匹配范围」，可在 `query` 增加标签过滤参数，当前明确仅管理/筛选

---

## 词典粒度重构（FR15.1.6）— 追加 Story

> 本段为 2026-08-14 追加：替换上述 Story 01-05 所实现的词典数据模型（见 ADR-014 更新节）。Story 06-10 为重构成套交付，编码时按序推进。

### Story 06: 数据模型重构（一文件一 mod）

**验收标准**:
- [ ] `Dictionary` 字段重构为 `{ mod_file_id, scope(单值 project|global), entries, key_index, text_index }`，移除 `scope_id`
- [ ] `DictionaryEntry` 字段重构为 `{ translation, original, source_mod, form_id_with_plugin, imported_at, updated_at, tags }`，移除 `source`（被 `source_mod` 取代）
- [ ] 词条主键 `entry_id = sha1(mod_file_id | 原文)`，不含 scope，验证跨 scope 切换不换 ID
- [ ] 文件后缀 `.tbdict`（内容仍为 JSON，`schema_version` 保留），`to_dict`/`from_dict` 适配新字段
- [ ] scope 单值校验（project/global），保留 `VALID_SCOPES`

**详细文档**: `plans/translation-memory/stories/story-06-model-refactor.md`

### Story 07: 定位/命名/加载重构

**验收标准**:
- [ ] manager 定位键从 `(scope, scope_id)` → `mod_file_id`（`_key`/`_dict`/`dictionaries` 全链路）
- [ ] 文件命名从 `{scope}__{scope_id}.json` → `{mod_file_id}.tbdict`
- [ ] `load()` 扫描 `*.tbdict`，以 `mod_file_id` 唯一索引；同名文件重复 → 硬校验抛错（不静默覆盖）
- [ ] `save_from_collection` 从打开文件路径推断 `source_mod`，并持久化 `form_id_with_plugin`
- [ ] 旧 `*.json` 词典文件弃置（不加载、不迁移）

**详细文档**: `plans/translation-memory/stories/story-07-locate-load-refactor.md`

### Story 08: 多词典组合查询与冲突仲裁（性能重点）

**验收标准**:
- [ ] `query` 改为多词典全查兜底：同名 mod 词典（最优先）→ 其余 project → 其余 global
- [ ] 键索引命中即停仅限「同名 mod 词典」内；跨词典文本命中收集候选后仲裁
- [ ] `QueryResult.conflicts` 真正填充（译文 + 来源词典 + 胜者），不再空转
- [ ] 仲裁规则：同名 mod > project > global；同级内命中计数高的优先（可配置）
- [ ] **性能约束**：全查兜底必须避免每次 query 线性扫全部词典——采用「按 mod_file_id 索引 + 惰性加载 + 命中即短路同名 mod」策略，全局词典文本索引预建合并索引或分级缓存；大词典（万级词条）查询耗时需在验收测试中量化
- [ ] `_normalize_cache` 加锁/按词典隔离，避免多词典并发串味

**详细文档**: `plans/translation-memory/stories/story-08-multi-dict-query.md`

### Story 09: scope 修改 + 分享/导入

**验收标准**:
- [ ] 提供 GUI 入口修改词典 scope（global ↔ project 切换），单值覆盖
- [ ] 「导入词典」：选 `.tbdict` 复制进 `data/translation_memory/`，同名提示覆盖/跳过
- [ ] 「导出」：选目标位置复制出去；「打开词典目录」：定位到词典目录
- [ ] 导入时校验 `.tbdict` 后缀与内容格式，损坏文件报告明确错误

**详细文档**: `plans/translation-memory/stories/story-09-scope-share.md`

### Story 10: GUI 面板改造 + 冲突可视化仲裁界面

**验收标准**:
- [ ] 词典面板适配 mod 粒度：词典列表按 mod_file_id 展示、显示 scope 标签
- [ ] 套用词典时激活集规则（同名 mod → project → global）默认自动，无需手动勾选
- [ ] 冲突仲裁对话框：列出命中冲突的词条（原文 + 多个候选译文 + 来源词典），用户逐条采纳/拒绝
- [ ] 存词典对话框改为「从打开文件路径推断 mod 名」，支持用户确认/修改 mod 名

**详细文档**: `plans/translation-memory/stories/story-10-gui-arbitration.md`

## 架构依赖（重构追加部分）

- 引用 ADR：`docs/adr/014-fomod-translation-memory.md` 更新节（2026-08-14：一文件一 mod、`.tbdict`、主键 sha1(mod名|原文)、更新语义、全查兜底、冲突仲裁、分享导入、旧数据弃置）
- 依赖的接口契约：`TranslationEntry.form_id_with_plugin`（`converter/translation_entry.py`，已存在持久化字段，供词典持久化复用）

## 风险与回退方案（重构追加部分）

- **多词典全查性能**：Story 08 重点——若全查兜底性能不达标，回退为「同名 mod 优先 + global 文本预建合并索引」，或引入按活跃度分级缓存；必须在 QA 阶段量化万级词典查询耗时
- **旧数据弃置不可逆**：本次明确不迁移旧 `*.json`；若后续发现需要旧数据，需从源 collection 重新「存为词典」重建（源数据仍在，非永久丢失）
- **冲突仲裁交互复杂度**：全查兜底下冲突概率上升，若逐条仲裁体验过重，可退化为「默认采纳最高优先级译文 + 冲突仅报告不逐条选」

## 综合整改状态增量（2026-08-18）

- `partially-verified`：保留 S01～S10 历史交付；locale、Stage、provenance、来源变化和 FOMOD 冲突策略尚未通过本轮验收。
- `blocked_by`：`translation-io-kernel-v2` S02/S05、`fomod-pipeline-v2` S03、`release-hardening-v2` S02。
- `superseded_by`：只按文本/旧 key 直接套用正式集合的边界由 EntryKey/ExternalEntryRef、CandidateSet 和显式仲裁取代。
