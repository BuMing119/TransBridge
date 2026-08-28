# Story 04：项目隔离 SQLite 仓储、事务、分页与缓存

## 所属 Plan

[项目全来源术语构建、版本管理与统一报告计划](../plan.md)

## 状态

草稿（长期存储合同受 ADR-034 接受门禁约束）

## 目标

以每 Project 独立 SQLite adapter 实现 S02 的仓储合同，提供不可变事实、原子发布边界、snapshot-bound keyset pagination 和三层可丢弃缓存，同时保持应用层与 UI 不感知 SQL 和磁盘路径。

## 原始验收标准

- [ ] 每个 Project 使用独立 SQLite 资产，开启 schema version、foreign keys、唯一/校验约束、受控 migration/backup/integrity check；日志模式根据已验证的本地文件系统能力选择，不假定网络路径可用 WAL。
- [ ] 一次事务可原子写入 build facts、draft/manual action、version membership、CanonicalDiff、ChangeLogDocument、artifact ledger 初态和 effective pointer；失败时旧 pointer 与历史完整不变。
- [ ] 逻辑版本不可变；物理内容寻址/版本 membership 去重不能让后续写入改变历史查询结果。
- [ ] summary、term/conflict/manual/evidence、history/compare 使用 snapshot-bound keyset pagination；cursor 绑定 snapshot digest、query fingerprint、sort key 和稳定 ID，条件或快照变化返回 `CURSOR_STALE`。
- [ ] `build_key`、parse fragment 和 extraction fragment 三层缓存可丢弃；正式 version/diff/manual/changelog 不受缓存或普通报告清理影响。
- [ ] 数据库损坏、未来 schema、迁移失败、空间不足进入只读诊断/阻止发布，不以空库覆盖。

## 依赖与当前事实

- 上游：S02 repository/query contracts 和 refs，S03 冻结 `BuildResult`；下游：S05～S11 的 cache、draft、publish、report、effective 与 UI pages。
- `RepositoryPaths.guard()/backup()/staging()`、`OsPersistenceFilesystem` 提供路径与 fault seam，但当前只服务 JSON EntityRef。
- `ProjectRepository.save_if_revision()`、`FutureSchemaResult`、`QuarantineResult`、`ReadOnlyWriteRefused` 是 revision/只读诊断先例。
- `PersistenceV2Services.close()`、`build_persistence_v2_services()` 和 `build_runtime()` 是组合根接入点。
- 当前仓库没有 `sqlite3` adapter、keyset cursor 或 `CURSOR_STALE` 实现。

## 关键接口与数据布局

- `paths.py`：`TerminologyPaths`，定位 `projects/<encoded-project>/terminology/{db,backup,staging,artifacts}`，所有路径经 root guard。
- `connection.py`：`TerminologyConnectionFactory`、`TerminologyStorageState`，区分 create、read-write、read-only。
- `schema.py` / `migration.py`：schema version、DDL、`MigrationManifest`、`TerminologyMigrator`。
- `repository.py`：`SqliteTerminologyRepository`、短事务 `SqliteTerminologyTransaction`。
- `queries.py`：`CursorCodec`、`QueryFingerprint` 和分页查询。
- `cache.py` / `artifacts.py`：三层 cache 与 artifact ledger。

Cursor 至少编码 `{schema, snapshot_digest, query_fingerprint, sort_values, last_stable_id}`；所有 `ORDER BY` 最后用 stable ID 打破平局。

## 实施步骤

1. 以 S02 contract 为唯一应用接口，先完成模型到表、约束和索引映射；禁止 SQL row 外泄。
2. 实现 Project ID 编码和 terminology root guard，分别定位 DB、backup、staging 与 artifacts。
3. 连接工厂显式设置 foreign keys、busy timeout 和 schema；仅已验证本地文件系统启用 WAL，其他位置使用保守 journal。
4. 建立 migration manifest；迁移前使用 SQLite backup API 生成一致性备份，并从独立只读连接执行 integrity check。
5. 实现 immutable facts/CAS、draft/action、version membership、diff/document、ledger 和 effective pointer 的单一 `BEGIN IMMEDIATE` UoW；大批量先写 run-scoped staging，最终事务短小。
6. 实现 summary/term/conflict/manual/evidence/history/compare 的 keyset queries；条件或 snapshot 改变返回 `CURSOR_STALE`。
7. 实现 build-key、parse、extraction cache 与独立 GC；GC 不得级联正式 version/diff/manual/changelog/ledger。
8. 接入 persistence service 的构造与 close 生命周期，并运行 contract、fault injection 与 query-plan 测试。

## 文件与测试

计划新增 `src/transbridge/persistence/terminology/{__init__,paths,connection,schema,migration,repository,queries,cache,artifacts}.py`，以及 `tests/persistence/terminology/`、`tests/contracts/terminology/`、必要的 integration tests。计划修改 bootstrap persistence/composition；只有需要共享 root guard 时才对 `persistence/v2/filesystem.py` 做窄扩展。

建议命令：

```powershell
uv run pytest tests/contracts/terminology/test_repository_contract.py tests/persistence/terminology -q
uv run pytest tests/integration/terminology -q
```

## 边界、风险与回退

- future schema、corruption、migration/integrity failure、`SQLITE_FULL` 进入只读诊断并阻止 publish，绝不自动创建空库覆盖。
- digest 冲突须内容复核；不得用未经复核的 `INSERT OR IGNORE`。
- `TaskRuntime.try_commit()` 会在 runtime 锁内执行 mutation，百万事实必须先 staging，permit 内只做短事务附接。
- 网络/同步目录不能假定 WAL 或 atomic rename；回退代码时 DB 保持只读，不降级或删除。
