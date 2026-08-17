# agent-infra-tools（通用文件与词条工具）

**对应需求**: FR16（通用文件与词条工具，Agent 可调用）
**技术模块**: backend + infra
**业务域**: 翻译基础设施
**状态**: 已确认
**创建日期**: 2026-08-14

## 功能边界

### 范围内

- 5 个通用工具，每个含「纯 Python 后端 + Agent 工具注册」两层：归档解包/打包、目录 diff、资源过滤规则、词条键对齐迁移、词典套用/存词典 Agent 注册补全
- 归档解包/打包支持 7z/zip/rar 三格式统一接口（py7zr + zipfile + rarfile）
- 目录 diff 按相对路径对齐 + 内容哈希
- 资源过滤规则引擎（可配置扩展名保留/剔除清单）
- 词条键对齐迁移（新旧集合按键对齐，与词典套用严格分离）
- Agent 工具注册：archive namespace（4 个文件工具）+ editor（词条对齐）+ translator（词典套用/存词典）

### 范围外

- FOMOD 特有逻辑（fomod XML 解析/翻译、流水线编排、GUI 面板，留待 FR15 后置需求）
- TMX 标准格式读写
- 语义/模糊匹配（仅精确匹配）
- CLI 单独交付

## Story 清单

### Story 01: 归档解包与打包
**验收标准**:
- [ ] 新建 `src/transbridge/fileops/__init__.py` + `archive.py`，提供统一 `extract(archive_path, dest_dir)` / `pack(src_dir, archive_path)` 接口
- [ ] 按扩展名分派：.7z→py7zr、.zip→zipfile、.rar→rarfile(捆绑 unrar.exe)
- [ ] 支持分层提取（按文件列表选择性提取，跳过 GB 级资源）
- [ ] 支持进度回调（复用 ApiWorker 的 make_progress_callback 模式）
- [ ] 解包失败（归档损坏/unrar 缺失）返回明确错误，不崩溃
- [ ] 实现 `_find_unrar()` 多路径探测（sys._MEIPASS + 应用目录 + PATH）
- [ ] 注册 Agent 工具 `extract_archive` / `pack_archive` 到 archive namespace，permission=write

> 详细实现指南见 `plans/agent-infra-tools/stories/story-01-archive.md`

### Story 02: 目录与文件差异分析
**验收标准**:
- [ ] 新建 `src/transbridge/fileops/differ.py`，提供 `diff_directories(old_dir, new_dir)` 接口
- [ ] 按相对路径对齐，识别新增(new)/删除(removed)/内容变化(changed)/不变四种状态
- [ ] 内容变化按 SHA-256 哈希判断，支持跳过特定扩展名的哈希（大文件仅比较存在性）
- [ ] 处理新旧根目录层级不一致（路径归一化，向上查找公共锚点）
- [ ] 注册 Agent 工具 `diff_directories` 到 archive namespace，permission=read

> 详细实现指南见 `plans/agent-infra-tools/stories/story-02-differ.md`

### Story 03: 资源过滤规则引擎
**验收标准**:
- [ ] 新建 `src/transbridge/fileops/filter_rules.py`，提供可配置扩展名保留/剔除规则
- [ ] 规则集中在配置清单（keep/strip 列表），不同 mod 复用不同规则
- [ ] 支持目录级规则（同扩展名不同目录不同处理：fomod 图片保留 vs textures 贴图剔除）
- [ ] 注册 Agent 工具 `filter_files` 到 archive namespace，permission=read

> 详细实现指南见 `plans/agent-infra-tools/stories/story-03-filter-rules.md`

### Story 04: 词条键对齐迁移
**验收标准**:
- [ ] 新建 `src/transbridge/migrator/__init__.py` + `key_migrator.py`，提供 `migrate(old_collection, new_collection)` 接口
- [ ] 按 `entry.key` 精确匹配：命中且原文未变 → 继承译文（stage=已翻译）；命中但原文变化 → 标记需复核；未命中 → 保留待翻译
- [ ] 不做文本兜底（文本兜底是词典套用的职责，见 Story 05）
- [ ] 返回 MigrationResult（继承数/需复核数/未命中数统计）
- [ ] 注册 Agent 工具 `migrate_entries` 到 editor namespace，permission=write

> 详细实现指南见 `plans/agent-infra-tools/stories/story-04-key-migrator.md`

### Story 05: 词典套用与存词典 Agent 工具注册
**验收标准**:
- [ ] 复用已有 `translation_memory/manager.py` 的 apply_to_collection/save_from_collection，不重复实现
- [ ] 新建 Agent 工具模块（或扩展 tool_translator.py），注册 `apply_dictionary` / `save_dictionary` 到 translator namespace
- [ ] `apply_dictionary` 调用 `TranslationMemoryManager.apply_to_collection()`，返回 ApplyResult 统计
- [ ] `save_dictionary` 调用 `TranslationMemoryManager.save_from_collection()`
- [ ] 两个工具 permission=write（可能修改集合/词典）

> 详细实现指南见 `plans/agent-infra-tools/stories/story-05-dictionary-tools.md`

## 架构依赖

- 引用 ADR：`docs/adr/015-generic-file-entry-tools.md`（fileops/migrator 独立包、archive namespace、键对齐 vs 词典严格分离）、`docs/adr/014-fomod-translation-memory.md`（py7zr+rarfile 选型）
- 依赖的技术决策：py7zr（7z）+ zipfile（zip）+ rarfile（rar，捆绑 unrar.exe）
- 依赖的接口契约：`TranslationMemoryManager.apply_to_collection()/save_from_collection()`（复用不改动）、`ToolRegistry.register_tools(namespace, [...])`、`ToolResult` 数据类、`@require_collection`/`@validate_params` 装饰器

## 风险与回退方案

- py7zr 大归档解压性能慢 → 分层提取（仅提取必要文件）+ 进度回调
- unrar.exe PyInstaller 打包路径 → `_find_unrar()` 多路径探测 + 打包冒烟测试
- fileops/ 与 infra/ 边界模糊 → 后续开发持续澄清，先聚焦文件操作与 LLM 基础设施的语义区分
