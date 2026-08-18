# ADR-015: 通用文件与词条工具 — fileops/migrator 独立包 + archive/editor/translator namespace

- **状态**: 已接受
- **日期**: 2026-08-14
- **决策者**: BuMing
- **对应需求**: [FR16](../requirements.md)
- **关联 ADR**: [ADR-008](008-smart-assistant-code-layering.md)（UI/业务分层）、[ADR-010](010-infra-extraction.md)（infra 共享基础设施）、[ADR-014](014-fomod-translation-memory.md)（FOMOD + 翻译记忆）

## Context

FR15 FOMOD 翻译流水线中的通用能力（归档解包/打包、目录 diff、资源过滤、词条键对齐迁移、词典套用）被用户指出不应被 FOMOD 特有逻辑锁死，而应独立为通用工具，供 FOMOD 翻译与未来批量翻译复用。这 5 个能力从 FR15 拆出，独立为新的 FR16 需求。

关键约束：
1. 归档/diff/过滤是纯文件操作，与现有 infra/ 包（LLM 客户端/向量存储/Markdown 渲染器/配置，即 LLM 基础设施）语义不同
2. 词条键对齐是 TranslationEntry 操作，与词典套用（translation_memory/apply_to_collection）是两个不同工具——前者是新旧集合键对齐，后者是词典→集合套用
3. 词条键对齐与新集合的文本兜底严格分离：键对齐只做精确键匹配 + 原文变化检测，不做文本兜底（文本兜底是词典套用的职责）

## Decision

### 1. 模块归属：新建两个独立包

```
src/transbridge/fileops/         # 通用文件操作（纯文件，与 LLM 无关）
    ├── __init__.py
    ├── archive.py               # 7z/zip/rar 解包/打包统一接口（FR16.1）
    ├── differ.py                # 目录/文件 diff（FR16.2）
    └── filter_rules.py          # 资源过滤规则引擎（FR16.3）

src/transbridge/migrator/        # 词条键对齐迁移（FR16.4）
    ├── __init__.py
    └── key_migrator.py          # 新旧集合按键对齐迁移译文
```

理由：infra/ 语义是 LLM 基础设施（llm_client/embedding_client/vector_store/config），文件操作（解包/diff/过滤）不属于该范畴，独立为 fileops/ 语义清晰。词条键对齐是 TranslationEntry 级操作，独立为 migrator/ 职责单一。

不并入 converter/：converter 聚焦格式转换（数据模型/DSD/分类导出），键对齐迁移是复用逻辑而非格式转换，独立包更清晰。

### 2. 词条键对齐 vs 词典套用：严格分离

| 工具 | 模块 | 输入 → 输出 | 匹配方式 |
|------|------|------------|---------|
| 词条键对齐（FR16.4） | migrator/key_migrator.py | 新旧两个集合 → 新集合（译文对齐） | 仅 entry.key 精确匹配 + 原文变化检测 |
| 词典套用（FR16.5） | translation_memory/manager.py（已有） | 词典 + 集合 → 集合（译文套用） | 键索引 + 文本索引两级匹配 |

词条键对齐不做文本兜底——键未命中的条目保留待翻译，文本兜底由词典套用负责。二者序贯使用：键对齐先做（精确），词典套用后做（兜底）。

### 3. Agent 工具 namespace 划分

5 个工具注册到 Agent（ToolRegistry）时的 namespace：

| 工具 | namespace | 理由 |
|------|-----------|------|
| extract_archive / pack_archive / diff_directories / filter_files | 新建 archive | 文件操作，与现有 7 个翻译工作流 namespace 语义不同 |
| migrate_entries | editor | 条目操作的编辑类 |
| apply_dictionary / save_dictionary | translator | 翻译复用类，补齐词典套用/存词典的 Agent 工具缺口 |

新建 archive namespace 承载 4 个文件操作工具；词条对齐与词典工具按语义归入现有 editor/translator namespace，不新建。

### 4. 归档实现：py7zr + zipfile + rarfile（复用 ADR-014 决策 4）

复用 ADR-014 的归档选型，fileops/archive.py 提供统一 extract()/pack() 接口，支持分层提取（按文件列表选择性提取）与进度回调。

## Alternatives Considered

| 决策点 | 方案 | 选择 | 理由 |
|--------|------|------|------|
| 文件操作归属 | 并入 infra/ vs 新建 fileops/ | 新建 fileops/ | infra 语义是 LLM 基础设施，文件操作不同域 |
| 词条对齐归属 | 并入 converter/ vs 新建 migrator/ | 新建 migrator/ | 对齐迁移是复用逻辑，非格式转换 |
| 文件工具 namespace | 并入 parser/writer vs 新建 archive | 新建 archive | 文件操作非翻译条目操作，语义独立 |
| 词条对齐 vs 词典套用 | 合并为一个工具 vs 严格分离 | 严格分离 | 匹配方式不同（键 vs 键+文本），职责不能混 |

## Consequences

- 依赖变更: 新增 py7zr、rarfile（复用 ADR-014 决策 4）；unrar.exe 随 PyInstaller 分发
- 目录变更: 新增 src/transbridge/fileops/（archive.py + differ.py + filter_rules.py）、src/transbridge/migrator/（key_migrator.py）
- 接口变更:
  - 新增 fileops/archive.py 的统一 extract()/pack() 接口
  - 新增 KeyMigrator.migrate(old_collection, new_collection) -> MigrationResult
  - 新增 Agent 工具: extract_archive / pack_archive / diff_directories / filter_files（archive namespace）、migrate_entries（editor namespace）、apply_dictionary / save_dictionary（translator namespace）
  - 复用 TranslationMemoryManager.apply_to_collection()/save_from_collection()（不改动）
- 正面: 通用能力独立可复用；Agent 工具补齐文件操作与词典套用缺口；FOMOD 流水线变薄
- 负面: 新增 2 个包；fileops/ 与 infra/ 的边界需在后续开发中持续澄清
---

### 更新: 2026-08-14 - 匹配键语义澄清（id vs key）

**决策**: 编码前核查 confirmed——词条匹配键统一使用 `TranslationEntry.key`。

**核查结论**:

| 证据来源 | 内容 | 结论 |
|---------|------|------|
| `translation_entry.py` 构造路径（EET/SST/DSD） | `id=id_value, key=id_value`（三处均相等赋值） | 当前 id == key，内容一致 |
| `translation_entry.py` 字段注释 | `key # 现在存储原来的id值`、`context # 现在存储原来的key值` | 历史重构遗留的过时注释，与当前赋值逻辑矛盾 |
| `translation_entry_collection.py:23,29` | 「以 TranslationEntry.key 作为唯一主索引（ADR-002 更新）」+ `_entries: dict[key → entry]` | **key 是唯一权威主索引** |
| `translation_memory/manager.py:317,345` | `query/save_from_collection` 用 `e.id` | **历史遗留不一致**，应与主索引统一用 `e.key` |

**约束**:

- 词条键对齐（Story 04）SHALL 以 `entry.key` 为匹配键（主索引，权威）
- 词典套用/存词典（Story 05）编码时 SHALL 将 `translation_memory/manager.py` 中 `query`/`save_from_collection`/`apply_to_collection` 内部使用的 `e.id` 统一改为 `e.key`，消除与主索引的分叉（当前 id==key 故不报错，但属历史遗留隐患，需在本次一并修正并在 changelog 记录）
- 因 id == key（当前构造路径恒等），此修正不改变现有行为，仅消除语义分叉

**影响**: `translation_memory/manager.py` 3 处字段引用（e.id → e.key）+ Story 04/05 的匹配键取值明确为 entry.key

### 更新：2026-08-18 — FileOps Ports、ArchivePolicy 与来源身份（已接受）

`fileops/`、`migrator/` 独立包和键迁移/TM 套用职责分离继续有效。它们作为 ADR-016 application ports 的 adapter，不直接编排 Project/FOMOD 或修改正式状态。

统一 `ArchivePolicy` 对 ZIP/7z/RAR 应用规范路径、符号链接/特殊文件、条目数、展开总量、压缩比、路径深度和超时预算，并在写入目标目录前完成检查。不同后端不得各自遗漏预算。KeyMigrator 使用 ADR-017 的完整 EntryKey/source namespace；只有同一或经显式映射的来源可做 key 继承，结果以 ChangeSet 候选返回，不直接 mutation 新集合。
