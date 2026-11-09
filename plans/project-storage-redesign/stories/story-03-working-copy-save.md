# S03：稀疏编辑与原子增量保存

- 状态：本地实现与相关回归完成；运行证据和限制见上级计划。
- 所属：[project-storage-redesign](../plan.md#s03稀疏编辑与原子增量保存)。
- 架构：[ADR-054](../../../docs/adr/054-project-storage-and-independent-tasks.md)。
- 前置：S01/S02；下游 S04/S06/S08。

## 目标与原始验收标准

单条/少量修改只提交实际变化和必要 revision/凭据，无修改不写；未加载行和其他版本不丢；保存过程中继续编辑仍保持正确 dirty；并发、失败、崩溃保持一致；不依赖插件或任务库判断“已保存”。

## 当前链与职责抽取

现行 ProjectLifecycleService 捕获完整 LifecycleSave，project_save_journal 在必要时调用 VariantSnapshot.to_dto，再经 repository 写入完整文档；DurableVariantCommitService 还构造完整候选聚合。全量 entries 参与若干 projection、一致性和 AI 读取。

新工作副本与保存服务放入实现 `application/projects/working_copy.py`、`save_changes.py`，SQL 提交放入 `persistence/project_store/commits.py`。现有 lifecycle 超过 1000 行，只留下协调与委托；不添加巨型增量 Controller 或通过 mixin 隐藏职责。

## 实现合同

- WorkingCopyToken：项目/版本身份、UI generation、基准 project/variant revision、局部编辑序号。不同计数不混用。
- EntryPatch：完整条目身份、明确提供的字段和值、原字段状态/读取版本、local_edit_seq。空值/清空与缺字段分开；单条修改不携带全部条目。
- SaveBatch：捕获的 patch、项目元数据变更、期望 persisted revision、匹配的 pending 合入凭据、operation_id。捕获后不可变。
- SaveReceipt：实际提交 revision、本批 operation_id、已提交行/字段集合及错误/冲突诊断。它不是“把当前整个 UI 标为 clean”的命令。

这些是计划新增合同，命名可在 S01 按现有风格统一，行为不可削弱。业务层不持有 SQLite cursor 或 Qt model 对象。

## 完整数据与分页缓存

数据库持有完整正式数据；缓存只是一部分读取结果，脏修改放在独立 patch 中。缓存淘汰不得淘汰 patch，也不得把未加载行序列化为空列表。修改未加载的批量范围时先经 read port 获取确切目标和必要字段，不能仅遍历可见行。

查询的排序/筛选若涉及已修改字段，须把 dirty patch 纳入结果语义。允许在一致查询基准上剔除受影响旧行，再将重新求值的 patch 行按同排序归并；不允许只修改可见页而使“全选筛选结果”漏选或重选。跨页缓存及 query token 失配时重新查询，不清除 dirty。

出现另一窗口的磁盘新 revision 时，不允许把新页直接混入旧基准的编辑视图；标记需要刷新/相关字段重比，保留本地 patch。AI 全范围固定输入还须捕获当时的 patch 与基准读令牌，后续编辑只影响后续任务。

## 保存时序

1. 在应用层短锁内捕获 SaveBatch，检查 active identity/generation，冻结本批数据后释放锁。没有真实变化时直接返回既有保存状态，不建立写事务。
2. worker 使用其独占连接开启项目写事务，读取小型 revision/身份记录，验证 CAS；只读受影响行以执行领域约束、外部引用和关联字段检查。
3. 按变化插入/更新/删除相应行，同事务修改必要 revision、来源/版本元数据及与实际提交一致的合入凭据。不得调用全量 Snapshot.to_dto、删除重建整张 entries 表或复制整个数据库作为普通保存备份。
4. 提交成功返回 SaveReceipt。若提交结果因错误不确定，按 operation_id 查项目内凭据/提交标记，明确已提交或未提交，不盲目重写。
5. 应用层核对工作副本身份，并仅确认仍对应本批 local_edit_seq 的变更。保存期间同字段的新编辑保留；其他未保存字段保留。重定基准时对已提交字段使用实际已存值，对后续 patch 保持最新意图及新的 base。
6. 投影只发布变化和真实 dirty。提交成功但 UI 安装失败进入可诊断刷新状态，不降级数据库、不把其他窗口/项目改成 clean。

手动保存、自动保存和明确持久型成果合入共享上述入口；S06 使用同一事务，不再从任务服务绕过工作副本写数据库。

## 关键交错与预期

- 保存 A 时用户把同字段改为 B：库中 A 可成功提交，界面保留 B 且 dirty，下次保存基于新 revision；不把 B 误确认。
- 保存失败：旧正式内容保持，patch 保留；原异常及可重试原因保留。
- CAS 冲突：零业务写入，返回受影响身份和最新 revision，不能把全表最新值自动覆盖本地。
- 切项目/版本：先按现有保存/放弃/取消处理；迟到回执只归属原工作副本，不更新新项目 UI。
- 页未加载：保存和关闭仍保护所有持久内容；空页面不是整来源删除指令。
- 快照恢复/更新源文件：属于显式范围事务，检查未保存 patch；不沿普通少量保存路径隐式重写全库。
- Project/Variant 元信息共同变化：在同一库事务提交，没有旧跨文档先写 Variant 后写 Project 的窗口。
- 术语选择：先验证不可变目标版本已发布，再随项目状态提交稳定引用；不维护第二份可写 selection。

## 验证与成本证据

实现 `tests/persistence/project_store/test_entries.py`、`tests/integration/bootstrap/test_project_storage_composition.py` 与 `tests/performance/test_project_storage_scaling.py`。

用 1k/100k 条目的相同 k 条修改对照，记录触及业务行数、查询范围、序列化次数、实际写入字节及 journal 开销。不能只 monkeypatch to_dto 然后绕道另一份全量序列化，也不能忽略全表一致性扫描。完整性测试覆盖未加载来源、多版本、tombstone、显式清空、外部引用、嵌套投影越权修改和保存中继续编辑。

故障注入在事务开始、首批写入、revision 更新、commit、回执与 UI 发布之间执行；真实双连接/多窗口 CAS 与进程中断恢复是后续集成验证，文档评审不算通过。
