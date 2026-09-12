# Project/Variant 旧模式安全自动升级实施计划

- **状态**：已确认；实现与聚焦 QA 完成（2026-09-12，开发前产物按真实差异补录）
- **对应需求**：[FR19.7～FR19.8](../../docs/requirements.md)
- **架构约束**：[ADR-018](../../docs/adr/018-project-session-persistence-v2.md)、[ADR-024](../../docs/adr/024-rebuildable-project-catalog-index.md)
- **Feature slug**：`project-schema-auto-upgrade`

## 目标

当当前持久化 schema 提升后，仍处于受支持旧版本的 Project 不得在开始中心被误报为“工程记录已损坏”。系统应在只读发现和准备阶段以内存迁移结果执行当前合同校验，并在用户实际打开、激活工程时，通过既有 repository 备份和原子替换机制将 Project 与活动 Variant 升级到当前版本。

## 非目标

- 不在开始中心刷新、历史搜索或 catalog 查询期间改写 Project/Variant。
- 不猜测没有 canonical 身份或没有显式迁移链的旧格式。
- 不读取或降级覆盖未来 schema。
- 不把来源文件缺失、Variant 损坏或迁移失败伪装成成功升级。
- 不引入新的持久化格式、后台批量迁移器或跨进程写锁。

## 当前实现事实与问题根因

- `ProjectRepository.load()` 已具备解析原始字节、创建按 digest/原版本命名的备份、调用 `migrate_to_current()`、按当前 schema 验证以及 staging 原子替换的迁移链。
- `ProjectRepository.read_snapshot()` 只在内存迁移和验证，不写盘，适合目录投影与工程准备。
- `V2ProjectCatalog`、`ProjectCatalogRepairService` 和 `CurrentProjectOpener` 曾分别硬编码“schema 2 或当前版本”。当前版本从 3 提升到 4 后，合法 V3 Project 因未命中硬编码分支被误报为损坏。
- 工程激活经 `V2ProjectCandidateLoader` 调用 Project/Variant repository `load()`，因此无需新增第二套写回或备份实现。

## 关键约束

1. 旧记录只有在存在完整显式迁移路径且迁移结果通过当前 schema、身份、路径和引用语义校验时才算可用。
2. 目录列表和缺失 catalog 的候选扫描只使用内存迁移结果；自愈服务只允许发布派生的 `project-catalog.json`。
3. Project/Variant 正式升级只能发生在用户实际激活工程的生命周期事务中，并复用 repository 的备份与原子替换。
4. 准备失败或进入来源不可用的只读恢复视图时，不发布迁移结果。
5. 损坏、未来版本、非 canonical 路径和身份不符记录保留原字节并返回稳定诊断。

## Story 01：旧版 Project 可发现且目录查询保持只读

**状态**：已实现

### 验收标准

- catalog 中的 V3 Project 可通过内存迁移与当前校验，开始中心显示为可用且不再出现“工程记录已损坏”。
- `project-catalog.json` 缺失时，具有完整迁移链的旧版 Project 可参与目录自愈；自愈只写 catalog，不改 Project。
- 当前 schema Project 行为不变；未来版本、损坏、身份/路径不符和无迁移路径的记录仍不可用或被安全跳过。
- `list_projects()` 全程不执行 write、replace、remove 或 mkdir。

### 文件落点与实施步骤

- `src/transbridge/persistence/project_catalog.py`
  - 将单一旧版本判断改为 `version < SCHEMA_VERSION`，统一调用 `migrate_to_current()` 后再走当前验证。
  - 保留当前版本的严格 `source_relations` 与 `validate_v2()` 检查，不调用 repository `load()`。
- `src/transbridge/persistence/project_catalog_repair.py`
  - 对可迁移历史版本使用同一内存迁移链；未来版本及失败候选保持跳过诊断。
  - 保持修复服务只发布 catalog 的既有写入边界。
- `tests/persistence/v2/test_project_catalog.py`
  - 新增 V3 catalog 条目可见且 Project 原始字节不变的回归测试。
- `tests/persistence/v2/test_project_catalog_repair.py`
  - 新增 catalog 缺失时恢复 V3 Project、Project 原始字节不变的回归测试。
  - 将“未来版本”用 `SCHEMA_VERSION + 1` 表达，避免 schema 提升后测试把合法旧版本误当未来版本。

### 验证

```powershell
uv run --frozen pytest tests/persistence/v2/test_project_catalog.py tests/persistence/v2/test_project_catalog_repair.py -q
```

## Story 02：打开激活后安全升级 Project 与 Variant

**状态**：已实现

### 验收标准

- 工程选择/准备接受所有具有 repository 显式迁移链的 envelope 旧版本，不再只接受 schema 2。
- 准备阶段完成内存验证和来源加载，Project/Variant 文件及数据树保持不变。
- 正常激活后 Project 与活动 Variant 均写为 `SCHEMA_VERSION`，保存的翻译、Stage、标签和来源关系不变。
- repository 在替换前保留原版本备份；备份、迁移、校验或 replace 任一步失败时不把半迁移记录当作成功。
- 未来 Project、无效 Project、损坏 Variant 和来源不可恢复场景继续使用既有拒绝或只读恢复语义。

### 文件落点与实施步骤

- `src/transbridge/persistence/current_project.py`
  - 将入口预检从固定版本集合改为拒绝未来版本；历史版本的真实性由 `read_snapshot()` 和当前 validator 判断。
  - 保留 canonical repository 路径校验、Variant 存在性、来源基线和恢复上下文检查。
- `src/transbridge/persistence/v2/repository.py`、`src/transbridge/persistence/project_lifecycle_loader.py`
  - 复用现有 `load()` 迁移发布链和激活调用链，不新增并行 writer；本 Story 不需要修改这些文件。
- `tests/persistence/test_project_recovery.py`
  - 将“准备不写盘、激活后升级”集成测试扩展到 schema 2 与 schema 3，覆盖 Project 和 Variant。

### 验证

```powershell
uv run --frozen pytest tests/persistence/test_project_recovery.py -q
uv run --frozen pytest tests/persistence/v2/test_repository.py tests/persistence/v2/test_repository_read_snapshot.py tests/persistence/v2/test_project_source_registry_migration.py tests/persistence/v2/test_assistant_session_migration.py -q
```

## 依赖顺序

1. 先完成 Story 01，使开始中心和缺失 catalog 自愈能发现旧工程。
2. 再完成 Story 02，使用户实际打开时通过既有生命周期发布升级。
3. 最后同时验证目录只读性、迁移原子性和失败保持原状，防止只修显示而无法打开，或为修打开而破坏查询纯读契约。

## 风险与缓解

- **误把损坏数据当旧版本**：所有历史记录必须先完成显式迁移并通过当前 `validate_v2()`；不能只比较版本号。
- **UI 刷新产生隐式写盘**：目录投影使用 `migrate_to_current()` 的内存草稿，测试断言无写、替换、删除和建目录调用。
- **迁移过程中断导致数据丢失**：正式升级仅复用 repository 的已验证备份、staging 和原子替换流程。
- **schema 再次提升后回归**：入口使用相对当前版本的迁移判断，未来版本测试使用 `SCHEMA_VERSION + 1`，避免再次硬编码 V2/V3。
- **旧程序回退**：升级后的当前 schema 可能被旧程序拒绝；原版本备份保留，回退不得让旧程序覆盖未来 schema。

## 回退

回退本功能代码只会恢复旧版工程在目录中的不可用状态，不会改写用户数据。若已在新代码中激活并升级工程，应保留 repository 生成的原版本备份；不得通过降版本号或手工删除字段伪造回退。catalog 是派生索引，可在应用离线时按 ADR-024 的显式维护流程重建。

## 验证证据

- catalog、catalog repair、project recovery：`41 passed`。
- repository、纯读快照、Project source registry 与 Session migration：`51 passed`。
- 本次 6 个生产/测试文件的 Ruff check 与 format check 通过。
- 真实持久化根只读投影确认“蕾米尔”与“艺术馆”均为 available；未直接打开或改写用户工程数据。

## 未决问题与假设

- 无阻断问题。
- 本计划所称“旧版本”限于 repository 已提供完整显式迁移链且具备 canonical 身份的持久化版本；无身份的历史 V1 工程包继续走既有 legacy import/recovery 路径。
- 不承诺多个进程同时对同一持久化根执行迁移的强一致性；该边界沿用 ADR-024。
