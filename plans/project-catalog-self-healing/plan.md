# Project catalog 自动自愈实施计划

- **Feature slug**：`project-catalog-self-healing`
- **状态**：已完成（2026-08-25，综合 QA 通过）
- **日期**：2026-08-25
- **对应需求**：[FR19.8、NFR2.1、NFR4.1](../../docs/requirements.md)
- **架构决策**：[ADR-024](../../docs/adr/024-rebuildable-project-catalog-index.md)
- **关联兼容修复**：[catalog 缺失时只读恢复活动工程](../../docs/changelogs/maintenance/project-catalog-active-fallback/2026-08-25-001-目录缺失时恢复活动工程投影.md)

## 目标

当 `project-catalog.json` 被误删，而 `data/projects/` 中仍存在合法的 canonical V2 Project 记录时，在 Persistence V2 启动组合阶段自动发现全部合法工程并原子重建 schema-1 catalog，使开始中心在首次查询前恢复完整本地工程列表。

## 非目标

- 不在 `V2ProjectCatalog.list_projects()` 或其他只读 projection 中写盘。
- 不修复、迁移、隔离、重命名或删除 Project/Variant 记录。
- 不自动覆盖已存在但损坏、未来版本或人为遗漏条目的 catalog。
- 不从 `workspace.json`、`current-workspace.json`、active pointer 或 Variant 目录猜测工程全集。
- 不在本次引入多实例/跨进程 catalog 锁；保持现有单进程桌面组合假设，并在写前复查目标仍缺失。
- 不新增 catalog schema、第三方依赖或 UI 管理页面。

## 当前实现事实与关键约束

- `ProjectLifecycleTransactionStore._commit_provisioning()` 是当前唯一创建/更新 catalog 的生产写路径；普通工程激活只写 `active-project.json`。
- `V2ProjectCatalog` 明确避免调用 `ProjectRepository.load()`，因为 load 可能迁移或 quarantine；自愈扫描必须同样直接使用 strict JSON 与 `validate_v2()`。
- `PersistenceFilesystemPort` 目前不支持目录枚举；生产实现和测试 fake 各只有一个，扩展影响面可控。
- lifecycle 内 `_AtomicDocuments` 已有 root guard、staging、复读验证和 replace，但作为私有类不应被修复服务跨模块引用。
- 当前真实 `data` 中 catalog 缺失，active pointer 存在，顶层有“蕾米尔”“艺术馆”两个 canonical schema-2 Project；本功能不能把验证动作变成对真实数据的测试写入。
- `uv run` 当前被失效的 `.venv` Python 3.12.12 路径阻断；验证优先尝试 uv，失败时使用系统 Python 3.13.5 与仓库内 `--basetemp`，并如实记录。

## Story 01：可发现文件系统与共享原子根文档

**验收标准**：

- [x] Persistence filesystem 可稳定列出指定目录的直接子文件；不存在目录返回空集合，枚举错误可故障注入，结果经过 canonicalize 且顺序稳定。
- [x] 符号链接、junction 或 fake alias 指向授权 root 外的候选不能越过 `RepositoryPaths.guard()`。
- [x] lifecycle 现有根文档写入抽成内聚公共组件后，project provisioning、active pointer 和 session pointer 的可观察格式及故障补偿语义不变。
- [x] 原子文档组件继续执行 staging 写入、字节复读、replace 和失败清理，不把 catalog 修复职责放进通用组件。

**文件落点**：

- 修改 `src/transbridge/persistence/v2/filesystem.py`。
- 新增 `src/transbridge/persistence/v2/atomic_documents.py`。
- 修改 `src/transbridge/persistence/v2/lifecycle_transactions.py`、`src/transbridge/persistence/v2/__init__.py`（仅在需要公开边界时）。
- 修改 `tests/persistence/v2/fakes.py`，更新原子文档/生命周期相关测试。

**实施步骤**：

1. 在 filesystem port、OS adapter 和 MemoryFilesystem 增加直接子文件枚举与故障记录。
2. 把 `_AtomicDocuments` 原样语义抽为 root-confined `AtomicDocumentStore`，保留现有 staging token 哈希和清理合同。
3. 让 lifecycle stores 依赖新组件，删除被替代的私有实现并运行 provisioning/fault 回归。

**测试策略**：OS 临时目录枚举、MemoryFilesystem 不存在/故障/稳定排序、root alias 逃逸、现有 provisioning fault tests、session lifecycle tests。

## Story 02：严格修复引擎与启动组合

**验收标准**：

- [x] catalog 合法存在时返回无需修复，不枚举 projects、不写盘；catalog 存在但损坏/未来版本时原始字节完全不变并返回阻塞诊断。
- [x] catalog 缺失时只扫描 `root/projects/*.json` 直接子级，恢复全部通过当前 V2 schema、entity、ID、canonical path、引用和名称验证的 Project。
- [x] 单个非法、V1、未来、不可读、非 canonical 候选被跳过并产生安全诊断，其他合法记录仍可恢复；源记录无 write/remove/quarantine。
- [x] 合法候选的规范化名称冲突阻止整次发布；不自动重命名或挑选。
- [x] 没有合法工程时不创建空 catalog；成功输出包含稳定排序的 `name` 与 `name_key`，发布后复读验证。
- [x] `build_persistence_v2_services()` 在首次 query/lifecycle 暴露前执行一次修复并保存 report；失败不阻断启动，活动工程只读 fallback 保持。

**文件落点**：

- 新增 `src/transbridge/persistence/project_catalog_repair.py`。
- 修改 `src/transbridge/bootstrap/persistence.py`；必要时修改 `src/transbridge/bootstrap/composition.py` 以注册只读 repair report。
- 新增 `tests/persistence/v2/test_project_catalog_repair.py`。
- 修改 `tests/integration/bootstrap/test_project_catalog_composition.py`。

**实施步骤**：

1. 定义 repair status/report/diagnostic 和独立 service，不实现 Query port。
2. 对每个枚举候选执行 strict parse、current schema、ProjectId、canonical path、`validate_v2()` 与名称验证；累计可恢复记录和安全诊断。
3. 在目录级检查规范化名称唯一性；生成确定性 schema-1 payload。
4. 写前再次确认 catalog 仍缺失，经共享原子文档组件发布并用 catalog 合同复读。
5. 在 persistence composition 运行一次并暴露不可变 report；保留现有 `V2ProjectCatalog` 只读回退。

**测试策略**：双工程恢复、active 排序集成、已有合法/损坏 catalog、空根、混合有效无效记录、路径/ID 不匹配、V1/future、名称冲突、重复运行、枚举/写/读/replace 故障。

## Story 03：兼容性、故障门禁与真实数据只读演练

**验收标准**：

- [x] focused persistence 与 bootstrap 测试通过，现有 `V2ProjectCatalog` 无写副作用回归保持。
- [x] stage write/read/replace 或发布后验证失败不会修改任何 Project、Variant、active pointer；无半写 catalog，修复可在下次启动重试。
- [x] 使用临时根表达当前真实“两工程、catalog 缺失”形态并验证恢复两个工程；真实 `data` 的 catalog 由启动组合另行生成，不纳入 Git。
- [x] 相关 Python 文件 Ruff check/format 与 `git diff --check` 通过；完成 persistence V2、bootstrap integration 和 UI 相关回归。
- [x] 文档准确说明首次启动自动生成 catalog、损坏 catalog 不自动覆盖，以及当前不提供多进程强一致性保证。

**文件落点**：上述测试、`docs/requirements.md`、ADR-024、本计划；完成后按用户要求补充 changelog。

**实施步骤**：

1. 完成单元、故障注入和 bootstrap 集成矩阵，复核 repair report 与无副作用调用记录。
2. 用隔离的临时根复现真实数据结构，检查输出格式和只读查询结果。
3. 运行相关回归与静态门禁，审查 diff 是否误含真实 catalog、Project 数据或无关格式化。
4. 回写计划状态、QA 证据和兼容/迁移说明，生成真实增量记录。

**测试策略**：focused → persistence/bootstrap integration → relevant UI → directed Ruff/diff；环境阻断必须记录原始原因和替代证据。

## 依赖顺序

`S01 -> S02 -> S03`

修复引擎依赖可注入枚举和共享原子文档；QA 必须在启动组合完成后验证首次 query 行为。

## 风险与回退

- **损坏数据被误当合法**：只接受 current V2、canonical path 和 `validate_v2()` 全部通过的 Project，不读取未来 schema 的未知字段。
- **派生索引覆盖现场**：只有目标完全不存在时进入修复；存在但无效一律阻塞并保留字节。
- **部分候选阻塞全部恢复**：普通无效候选只跳过；只有目录不变量无法无损选择（名称冲突）时阻止整次写入。
- **原子发布故障**：stage 失败清理，destination 仍缺失；现有活动工程只读 fallback 继续工作。
- **多进程竞态**：写前复查可降低风险但不构成 CAS。真正多实例支持需所有 catalog 写入方共享跨进程锁，另立需求。
- **回退**：移除启动 repair composition 即可；已生成 catalog 与现有 schema-1 provisioning 格式相同，无需数据回滚。

## 明确假设

- TransBridge 当前按单进程桌面应用使用同一 persistence root；本次不新增第二实例协调。
- catalog 是可从 Project 权威记录重建的派生索引，Project 文件不是修复目标。
- 自动恢复只处理 catalog 整体缺失；用户显式要求覆盖现有 catalog 时需要单独的备份与确认流程。

## 完成与 QA 证据

- `python -m pytest tests/persistence/v2 tests/integration/bootstrap tests/ui -q --basetemp .tmp-catalog-healing-full-qa -p no:cacheprovider`：`480 passed, 1 skipped`。
- 定向 `.venv\Scripts\ruff.exe check` 与 `format --check` 覆盖本功能生产代码及测试并通过。
- `git diff --check` 通过，仅有仓库既有 Windows 行尾转换提示。
- 本机 `uv run` 因 uv cache 路径异常和既有 `.venv` Python 指向失效而不可用；测试使用系统 Python 3.13.5，Ruff 使用仓库 `.venv` 内可执行文件。
- 真实忽略目录 `data/project-catalog.json` 已按同一启动组合合同恢复“蕾米尔”“艺术馆”两个合法索引条目；Project、Variant 与 active pointer 未修改。

## 未决问题

无阻断问题。跨进程强一致性仍按 ADR-024 明确排除，若未来允许多个实例同时写同一持久化根，应单独引入共享锁或 compare-and-swap 原语。
