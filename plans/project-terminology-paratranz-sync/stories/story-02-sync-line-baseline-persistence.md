# Story 02：同步 line、Variant 映射与持久基线

- **所属 Plan**：[项目术语库与 ParaTranz 备份/双向同步计划](../plan.md)
- **状态**：已确认
- **追溯**：FR5.17.2、FR5.17.4～FR5.17.6；ADR-018、ADR-023、ADR-034
- **前置依赖**：[Story 00](story-00-terms-contract-calibration.md)；S01 DTO 合同冻结后完成集成
- **下游调用方**：S03 planner、S04/S05 executor、S06 legacy echo filter、S07 状态投影

## 目标

在现有 Project 隔离的 terminology SQLite 中建立同步身份、活动 Variant 映射、三方比较基线、逐项结果和入站 change set 的持久事实。损坏、未来 schema、并发 revision 或目标变化时必须 fail closed，绝不能把缺失/不可读 baseline 当成首次空同步。

## 原始验收标准

- `TerminologySyncLine` 稳定绑定 local Project/Variant、endpoint、account user、remote project ID 和 profile revision；目标或 Variant 不同即为不同 line，不共享 baseline。
- 每个受管条目保存 local term ID/version/digest、remote ID/revision 或 observed digest、最后共同 canonical digest、作用域、ownership、最后结果和 tombstone 状态。
- baseline、逐项结果和入站 change set 在同一个项目隔离 SQLite 中事务保存；SQLite 损坏或 schema 不兼容时同步 fail closed，不退化为空 baseline 执行全量覆盖。
- 相同目标已有其他 Variant 的活动映射时，任何远端写入前返回 `variant_mapping_conflict`；显式替换映射保留旧审计和 remote links。
- schema migration 有备份、校验和故障回退；旧数据库没有 sync 表时解释为“尚未同步”，不改变 effective version。

## 当前存储事实与约束

- `TerminologyPaths.database(project_id)` 已按本地 Project 隔离 SQLite，并使用 path guard。
- `TerminologyConnectionFactory` 会对未来 schema 只读打开、对损坏库拒绝重建、对旧 schema 先调用 `TerminologyMigrator` 备份再迁移。
- 当前 `SCHEMA_VERSION = 2`，`REQUIRED_TABLES` 和 `validate_schema()` 会拒绝 current version 但缺表的数据库；新增表必须提升版本并提供 v2→新版本迁移。
- `SqliteTerminologyRepository` 已拥有 versions/drafts/effective/report/artifact 事务；同步仓储应以组合对象或窄 property 接入，不继续扩大主 repository 的业务方法集合。
- `DraftService` 和 `DraftTransactionPort` 是 S05 修改草稿的唯一 application 边界；本 Story 只持久化同步事实，不直接构造/保存 draft。

## 计划新增的领域结构

- `TerminologySyncTarget`：规范 endpoint、account user ID、remote project ID；不含 token、project name 或浏览状态。
- `TerminologySyncLine`：`line_id`、local project ID、variant ID、target、profile revision、created/retired 状态。`line_id` 由 canonical identity 派生而非数据库自增。
- `TerminologySyncProfile`：mode capability、lossy policy、delete policy、活动 Variant mapping revision；默认无自动同步开关。
- `TerminologySyncBaseline`：line ID、baseline revision、local version ID/digest、remote snapshot digest、共同 snapshot digest、完成 run ID。
- `TerminologySyncItemLink`：stable item ID、local term ID、remote ID、remote revision/observed digest、common content digest、scope、ownership、tombstone、last outcome。
- `TerminologySyncRunRecord` 与 `TerminologySyncItemOutcomeRecord`：plan/run/owner/target、terminal outcome、逐项 confirmed/failed/unknown/reconciled。
- `InboundChangeSetRecord`：change set ref、line/baseline/plan、remote snapshot、status/revision；具体 item schema由 S05 完成。

所有计划新增值对象都放在 `application/terminology_sync/`，SQLite codec 不向 application 返回裸 row/dict。

## 状态与事务边界

```text
read line/profile/baseline@revision
          ↓
planner/executor 使用不可变 ref
          ↓
transaction: append run outcomes
             + CAS baseline revision
             + upsert confirmed item links
             + optional immutable inbound change set
          ↓
commit 或完整 rollback
```

“首次未同步”必须由 schema 可读且查询确实无 line/baseline 证明；存储 unavailable/corrupt/read-only 均返回 capability unavailable，而不是空 baseline。

## 依赖有序的实施步骤

1. 在 `application/terminology_sync/identity.py` 定义 target/line/item canonical payload 和 digest，endpoint 复用 `normalize_paratranz_endpoint()`，但不导入 UI 或 config。
2. 在 `models.py` 定义冻结 profile/baseline/link/run/change-set ref；校验 Project/Variant/target 一致性、revision 非负、digest 非空和 remote ID 类型。
3. 在 `ports.py` 定义 `TerminologySyncStatePort`：读取活动 line/profile/baseline、分页 item links/outcomes、CAS 提交 run、保存/读取 immutable inbound change set、替换活动 Variant mapping。
4. 将 terminology schema 提升到下一个版本，新增 `terminology_sync_lines`、`terminology_sync_profiles`、`terminology_sync_baselines`、`terminology_sync_item_links`、`terminology_sync_runs`、`terminology_sync_outcomes`、`terminology_sync_inbound_sets`/items 和必要索引。
5. 明确表可变性：run/inbound set 主记录和 outcome append-only；baseline/profile/link 只允许 expected revision CAS 更新；历史 line retire 不删除。
6. 扩展 `TerminologyMigrator` 为 v2 备份后创建新表并验证。现有 v0/v1→current 路径必须仍能一次迁到新版本，migration evidence 指向最终版本。
7. 新建 `sync_codec.py` 做 typed JSON 编解码和 schema version 字段；未知未来 payload version fail closed，不丢字段重写。
8. 新建 `sync_state.py` 实现端口，所有 write 使用同一 connection transaction，并把 sqlite full/read-only/conflict 转换为现有 terminology storage/domain error。
9. 在 repository/composition 只增加 `sync_state` 窄 property/注册名；关闭 repository 时共享 connection 由现有 owner 统一释放。
10. 实现活动 Variant mapping：同 target 已有另一 active line 时读取成功但 `writable=False/variant_mapping_conflict`；显式替换只 retire/切换 pointer，不删旧 baseline/link。

## 文件变更清单

- **新增** `src/transbridge/application/terminology_sync/__init__.py`、`identity.py`、`models.py`、`ports.py`。
- **新增** `src/transbridge/persistence/terminology/sync_codec.py`、`sync_state.py`。
- **修改** `src/transbridge/persistence/terminology/schema.py`：新 schema、required tables、索引/immutable/CAS 约束。
- **修改** `src/transbridge/persistence/terminology/migration.py`：v2→新版本及历史链迁移。
- **最小修改** `src/transbridge/persistence/terminology/repository.py`、`__init__.py`、`src/transbridge/bootstrap/terminology.py`：组合和端口导出。
- **新增** `tests/persistence/terminology/test_sync_state.py`、`tests/application/terminology_sync/test_identity.py`、`test_models.py`。
- **更新** `tests/persistence/terminology/test_storage.py`：schema version、migration backup/future/corrupt 行为。

## 边界条件与错误处理

- target endpoint/account/remote project 任一变化都产生不同 target identity；旧 line 不被覆盖。
- account user ID 暂不可得时 line 只能处于 unverified/read-only planning 状态；验证成功后不能原地把另一账号写成同一 identity。
- local term ID 和 remote ID 一对多历史必须保留，但同一 active baseline 中一个 remote ID 不能绑定多个 live item；冲突由 S03 可见化。
- unknown outcome 不推进 common content digest，不把 item 标记 confirmed；reconcile 后追加新 outcome，再 CAS 推进 link/baseline。
- baseline CAS 失败表示并发 sync 已提交，当前执行结果必须 stale/reconcile，不能覆盖新 revision。
- schema migration/validation失败保持原 DB 和备份，`EffectiveTerminologyPort` 仍可在原功能允许的模式下读取；sync capability 单独 fail closed。
- 未来 schema 只读时不允许执行同步写，也不能将查询不到解释为未同步。

## 测试策略与建议命令

- identity：Project/Variant/endpoint/account/remote project/profile 任一变化产生不同 digest；排序与大小写规范稳定。
- repository：首次无 baseline、round-trip、分页、append-only、CAS 冲突、活动 Variant 替换、retired line 审计、remote ID 重用、unknown→reconciled。
- migration：v0/v1/v2→新版本、重复打开、备份摘要、migration fault、disk full、future schema、corrupt/incomplete schema。
- isolation：两个 Project、两个 Variant、两个 endpoint/账号/remote project 之间不共享任何 baseline/link。
- 建议命令：`uv run pytest tests/application/terminology_sync/test_identity.py tests/application/terminology_sync/test_models.py tests/persistence/terminology/test_sync_state.py tests/persistence/terminology/test_storage.py -q`。

## 风险、回退与未决问题

- schema 是长期数据契约；S00 三项校准若改变活动 mapping/删除 ownership 结构，必须在迁移落地前解决，不能事后追加含义相反字段。
- shared SQLite 可保证本地原子性，但不能把远端请求纳入同一事务；S04 必须先 durable 记录 outcome，再推进 baseline，并处理进程崩溃窗口。
- 回退代码不得降级写旧 schema。可以隐藏 sync capability并保留新表，只读 FR5.16 数据；旧程序若不能识别未来 schema应拒绝写而非覆盖。
