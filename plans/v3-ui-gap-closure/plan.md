# V3 权威工程 UI 缺口闭环

- **状态**：已完成（2026-09-01，综合 QA 通过）
- **日期**：2026-09-01
- **对应需求**：FR1.7、FR1.9、FR4.5、FR5.8、FR19、FR26
- **关联 ADR**：ADR-017、ADR-018、ADR-019、ADR-024、ADR-025、ADR-035

## 目标

补齐当前 Project schema v3 已有数据能力但桌面端尚无完整用户链路的功能：本地工程删除与重命名、历史还原点删除、JSON/SST 迁移导入、工程归档冲突恢复与系统打开、任务活动动作，以及 V3 权威工程下的批量 AI、ParaTranz 下载和写回。

工程删除只提供一个用户入口。它删除 TransBridge 管理的数据，不删除用户选择的外部 ESP/ESM/ESL、XML、SST、Strings 或其他来源文件。

## 非目标

- 不新增“从列表移除”和“永久删除”两个语义。
- 不删除 ParaTranz 云端工程，不改变其现有独立管理入口。
- 不实现 SST 从零创建或扩大当前格式能力矩阵。
- 不重复实现当前已有的 ParaTranz 讨论回复/关闭/重开、成员添加/移除和翻译记忆 project/global 作用域。
- 不回退到 legacy `ProjectHandle`、`VariantStore` 或只修改 `AppContext.collection` 的写路径。

## 当前实现事实与约束

- `persistence/v2/models.py` 的当前 `SCHEMA_VERSION` 为 3；目录名是迁移兼容名，不代表另有 V3 UI。
- `ProjectRepository.delete()` 只删除单一 Project 文档，没有聚合清理、目录索引和活动指针事务。
- 正式 `ProjectSnapshotPort` 只有 `list/load`；legacy `VariantStore.delete_snapshot()` 不能作为 V3 权威入口。
- DropRouter 能识别 JSON/SST，但 `ParseConfigDialog` 和正式迁移提交不接收这两类来源。
- 归档导入在同 ID 或同名时 fail-closed；CLI 只接受 `--open-project`。
- Task activity 模型已经表达 recover/retry/open_result/open_log，但当前面板只对活动任务提供 pause/resume/cancel。
- V3 批量入口当前显式阻断，ADR-035 禁止以 legacy collection 修改作为降级实现。
- 工作树已有大量未提交变更；实现必须最小增量合并，不能覆盖或格式化无关文件。

## Story 01：工程生命周期管理闭环

### 验收标准

- 开始中心和当前工程菜单提供同一个“删除本地工程”意图，显示工程名和不可撤销说明，并要求一次明确确认。
- 删除清理 Project 文档、所属 Variant、历史还原点、项目目录索引、活动指针以及明确按 project_id 管理的本地资产；任一步失败不得留下指向已删除工程的有效目录记录。
- 外部来源路径即使位于数据根之外也绝不删除；路径越界或身份不匹配时整体拒绝。
- 删除当前工程后返回开始中心并刷新投影；删除非当前工程不打断当前工作。
- 权威工程可以重命名；名称唯一性、revision CAS、Project 文档和 catalog 在同一提交边界更新，Project ID 和磁盘身份保持不变。
- 历史还原点列表允许删除选中项；只删除经身份校验且属于当前 Project/Variant 的快照。

### 文件落点

- `src/transbridge/application/projects/`：新增窄生命周期管理 command/service 和快照删除命令。
- `src/transbridge/persistence/`、`src/transbridge/persistence/v2/`：受根路径约束的聚合删除/重命名提交、catalog/active pointer 更新和快照删除。
- `src/transbridge/bootstrap/persistence.py`：组合新服务。
- `src/transbridge/ui/shell/`、`src/transbridge/ui/coordinators/`、`src/transbridge/ui/workbench/`：意图、确认、开始中心和项目栏入口。
- `tests/application/projects/`、`tests/persistence/v2/`、`tests/ui/`：成功、失败回滚、重复删除、外部源保护、当前/非当前工程和名称冲突。

## Story 02：JSON 与 SST 正式迁移导入

### 验收标准

- 拖放或文件选择的 ParaTranz/DSD/TransBridge JSON 与 SSU8/SSU9 SST 能进入同一个“导入已有译文”草稿，而不是落入不可处理提示。
- 空文件、歧义 JSON、未知 schema、SSU8 无译文、条目映射歧义和跨来源 EntryKey 冲突给出稳定诊断，不部分提交。
- 导入只更新当前 Variant 中可证明映射的既有条目；通过权威 mutation service 提交并保持正确 dirty/revision。
- 取消或解析失败不修改 Project、Variant 或工作台投影。

### 文件落点

- `src/transbridge/application/io/`：补齐 JSON/SST 只读 adapter 与稳定映射结果。
- `src/transbridge/ui/workbench/_parse_config_dialog.py`、`src/transbridge/ui/coordinators/parse_coordinator.py`、Drop intent 组合：接入选择、预填和提交。
- `tests/contracts/io/`、`tests/ui/`：格式、歧义、失败原子性和拖放旅程。

## Story 03：工程归档恢复和系统打开

### 验收标准

- 导入 `.transbridge` 遇到同名但不同 ID 时可在导入前输入新名称；遇到同 ID 时生成新的 Project/Variant 身份并以副本导入，不覆盖原工程。
- 归档内所有 Project/Variant/source relation/snapshot 引用在换身份后保持一致，无法安全重写时整体拒绝。
- GUI 命令行可显式接收 `.transbridge` 导入目标；Windows 文件关联把归档路径作为启动参数时进入审查/导入流程，而不是当作 Project JSON 打开。
- 取消导入不写盘；导入成功后由用户决定是否打开，不隐式覆盖当前 dirty 工程。

### 文件落点

- `src/transbridge/persistence/project_archive.py` 及 archive helper：copy/rename identity rewrite。
- `src/transbridge/cli.py`、`src/transbridge/entrypoints/gui.py`、窗口启动参数与 coordinator：启动路由。
- `tests/persistence/v2/test_project_archive.py`、`tests/integration/entrypoints/`、`tests/ui/`。

## Story 04：任务中心真实动作

### 验收标准

- 历史/恢复记录根据 `available_actions` 显示且只显示真实可用的恢复、重试、打开结果、打开日志动作。
- 恢复和重试调用已注册 typed use case；重试生成新 Run ID，并在提交前重新执行功能自有 preflight。
- 结果或日志不存在、owner 不匹配、上下文已过期时按钮禁用或返回可理解原因，不猜测文件路径。
- 动作完成后刷新当前、历史和恢复投影；旧终态记录保持不可变。

### 文件落点

- `src/transbridge/application/tasks/`：复用现有 recovery/retry/activity 契约，只补缺失的窄 facade。
- `src/transbridge/bootstrap/`：注册 GUI 可调用 use case。
- `src/transbridge/ui/shell/task_center.py`：动作呈现与 controller 路由。
- `tests/contracts/tasks/`、`tests/ui/test_task_center.py`。

## Story 05：V3 权威批量操作

### 验收标准

- 跨来源 AI 批量翻译对捕获的 Project/Variant identity 和 revision 运行；全部候选通过校验后以一次 Variant command 提交，取消、迟到结果或映射缺失不修改权威状态。
- ParaTranz 批量下载按来源形成独立预检结果，再以一个受控提交发布所有可接受 existing-entry 更新；远端 create/delete 或不完整映射继续 fail-closed。
- 批量写回为每个来源构造正式 source snapshot/WriteRequest；先完成全部 preflight，再发布，返回逐来源成功/失败和 artifact，不调用 legacy writer。
- 三个按钮在 V3 工程下不再显示“尚未接入”阻断；能力确实不足时显示具体来源级原因。

### 文件落点

- `src/transbridge/ui/tools/ai_translator/`：批量结果到权威 Variant change set 的 adapter。
- `src/transbridge/ui/operations/`、`src/transbridge/ui/workbench/cards/`：batch operation plan/facade、ParaTranz 批量下载和批量写回。
- `src/transbridge/application/projects/`、`src/transbridge/application/io/`：复用 ADR-035 mutation/写入边界。
- `tests/ui/tools/`、`tests/ui/operations/`、`tests/contracts/io/`、`tests/application/projects/`。

## 依赖顺序

1. Story 01 建立项目管理和快照删除的正式命令边界。
2. Story 02 与 Story 03 可在 Story 01 的 catalog/identity 事务能力上实现。
3. Story 04 独立于项目删除，但复用 owner/context 检查。
4. Story 05 最后实施，复用既有 authoritative mutation、hydration 与 operation-plan 能力。

## 验证策略

- 每个 Story 先运行对应的聚焦 pytest。
- 完成后运行受影响的 persistence/application/UI/operation/task 测试集合。
- 最终运行 `uv run ruff check src tests`、`uv run ruff format --check src tests`；若仓库既有环境阻止 `uv`，记录原因并使用可用解释器运行等价聚焦测试。
- 审查 `git diff --check` 和本任务实际 diff，确认未覆盖既有未提交修改、未删除外部或临时用户文件。

## 风险与回退

- **聚合删除半完成**：使用单一受根路径约束的生命周期事务；发布目录索引前完成实体删除计划校验，并保留可恢复 staging 直到提交成功。
- **归档换身份遗漏引用**：只重写明确 schema 字段并重新通过 schema/语义验证；不做字符串全局替换。
- **批量操作部分提交**：预检与正式提交分离，Variant 修改一次提交；文件发布保留每来源 artifact/诊断，不把部分成功描述为全成功。
- **现有工作树冲突**：逐文件检查当前 diff，使用最小 patch；无法区分所有权时停止该局部 Story，而不是重置用户改动。

## 明确假设

- “删除工程”是一个用户动作，不区分忘记记录与永久删除。
- 外部来源文件永远不属于删除范围；只有应用数据根内、能由 project_id 明确归属的资产可清理。
- 当前工作树中的未提交改动属于用户，均需保留。

## 完成证据（2026-09-01）

- Story 01～05 均已通过权威 application/persistence 边界接入桌面 UI；未使用 legacy Project/Variant 写路径作为降级。
- 五个 Story 合并回归：`180 passed`。
- 全仓静态检查：`ruff check src tests` 与 `ruff format --check src tests` 均通过。
- `git diff --check` 通过；仅报告仓库既有的 LF/CRLF 转换提示。
- 仓库 `uv` 环境指向已移除的 Python 3.12，测试改用系统 Python 3.13；Ruff 使用仓库现有可执行文件。
