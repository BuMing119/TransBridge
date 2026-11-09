# S06：任务成果合入、跨库对账与独立删除

- 状态：本地实现与相关回归完成；运行证据和限制见上级计划。
- 所属：[project-storage-redesign](../plan.md#s06合入对账与独立删除)。
- 架构：[ADR-054](../../../docs/adr/054-project-storage-and-independent-tasks.md)。
- 前置：S03 工作副本/提交凭据、S05 独立任务输入/候选；不要求原项目始终打开。

## 目标与原始验收标准

项目提交成功而任务回执失败时，可按凭据恢复状态且不重复合入；未保存/被撤销候选不得假报已持久合入；删除任一方不损坏另一方；失效目标禁止自动回写；清理保留运行中、未合并和必要恢复材料。

## 现有可复用能力

`application/translation/result_merge.py::merge_result_entries` 比较 base/current/proposed；`application/projects/result_merge.py::ProjectResultMergeService` 负责真实目标与 CAS 重试。保留这些规则，改变其全量 Snapshot 读取和项目目录成果依赖。

现行 `smart_assistant/tools/translation_task_results.py` 和 GUI 的 TaskMergeReview 使用项目目录构建 recovery store；S05/S06 改为注入独立任务服务。任务权限仍按 Owner/Session/Run 校验，脱离项目目录不代表历史对任何调用方开放。

## 实现记录与接口

- ResultGeneration：不可变 Run ID、候选修订、完整条目身份和最小 base/proposed。既有字段变化必须建立新修订，不原地替换旧请求引用的候选。
- ApplyRequest：独立 operation_id、目标项目/版本、候选 ID/修订集合、明确处理选择和策略。任务库先持久化；同 ID 不接受不同请求，不需要新增内容哈希。
- PendingApplication：working copy 内 patch 到请求/候选的关联和 local_edit_seq；它不证明持久提交。
- ProjectApplyReceipt：项目事务内的 operation_id、目标、候选身份与修订、实际处理结果、提交 revision。仅保留业务幂等所需记录，不存日志、模型输入或整任务报告。
- TaskApplicationState：prepared / staged-unsaved / committed / acknowledgement-pending / conflicted / target-unavailable / discarded-or-superseded；状态由相应证据决定，不改 TaskRuntime 执行终态。

实现 `application/tasks/result_application.py` 编排；`persistence/project_store/receipts.py` 与 `persistence/task_store/decisions.py` 各自负责本库事实。禁止把两个 SQLite connection 的 commit 串起来称为原子跨库提交。

## 合入与确认顺序

1. 独立任务库持久保存 ApplyRequest，验证候选存在且不可变。失败则不对项目实施合入。
2. 读取目标实际行及当前未保存 patch，调用三方规则。原文/来源变化、条目删除、重复目标和权限失配均按现有合同处理；不因 prefer_incoming 绕开身份校验。
3. 应用到 working copy 后标 staged-unsaved；结果面板明确“已应用、待保存”。与普通项目编辑共用保存策略，不能为简化对账擅自强制保存用户其他修改。
4. S03 保存时，收集仍实际存在的合入 patch。变化与 ProjectApplyReceipt 在同一项目事务提交。批量部分处理按候选结果记录；未处理、未加载、冲突项不得一起标成功。
5. 项目提交后任务库更新 committed。任务库失败时回执暂待同步，禁止向用户报告项目保存失败并自动重放变化。
6. 下次查询/恢复该任务时，使用 operation_id 查询项目凭据：存在则同步状态；不存在且项目可用则重新检查请求状态，不自动复放写入；目标删除则报告 unavailable 并保留成果。正常打开/保存项目不主动扫描任务对账。

相同请求重试读取既有凭据，不再次修改。用户改变候选选择或策略应产生新请求，并重新按 current 比较；不得把 operation_id 当绕过新编辑的许可。

## 未保存内容与幂等细节

- 应用后关闭前放弃修改：移除对应 PendingApplication，任务保留候选并标明未持久合入；重启不能仅凭 staged 状态宣称已保存。
- 应用后首次保存前再次编辑：如果候选字段已被用户覆盖，凭据明确该候选被本地后续编辑取代；不生成“候选原值已持久”的假凭据。未被覆盖字段可按实际提交单独确认。
- 应用后保存中继续编辑：本批成功凭据只对应本批值；用户后续修改仍 dirty，已经持久合入的事实不被后来编辑抹除，也不授权重新覆盖后来值。
- current 已等于 proposed：记录等值跳过的处理结果，不创建空业务变更；请求处理事实与实际业务 revision 分开。
- 结果中同时有成功、失败、未处理：逐项状态可恢复，不以整个 run 的 completed 代替已全部合入。
- 任务删除后项目还有待保存合入：用户删除前明确取消 pending 处理或先保存；删除不能静默丢掉编辑。已经提交的项目凭据和内容不依赖任务存在。

## 故障矩阵

1. 请求尚未落盘即失败：项目零改动，成果保留。
2. 请求已落盘、项目尚未应用：任务显示待处理，不自动执行外部副作用。
3. 已应用内存、项目保存前崩溃：无项目凭据，成果保留，不能误判已持久合入。
4. 项目事务写入途中失败：回滚变化与凭据，保留 patch/任务候选。
5. 项目 commit 成功、回执返回丢失：通过同库凭据识别已提交，不再次递增 revision 或建立历史。
6. 项目已提交、任务状态更新失败：按需对账补状态，不回滚项目、不重跑 AI 或下载。
7. 对账前项目被删除：保留任务候选，显示原目标已删除和可确认的已有历史状态；不能把同名新项目当原目标。
8. 项目和任务同时被两个窗口处理：以不可变请求、项目 CAS、任务版本与执行租约拒绝重复/陈旧决定，不通过界面禁按钮作为唯一防护。

## 删除与回收

删除项目只涉及新项目根：处理当前修改，阻止新的项目提交，等待相关写入结束并释放连接，发布删除状态后清理自有内容。任务运行无需被级联删除，可继续保存独立成果；任务最终合入必须再次验证目标。

删除任务按 Run ID 与 Owner 获取删除租约，停止活动写入并明确未合并成果影响，之后删除任务自有数据；不能删除 ProjectApplyReceipt 或项目条目。只读历史与删除交错要返回明确已删除，不继续持有失效 cursor。

自动回收只处理终态、无候选/租约引用且可重建或被替代的中间材料；正式成果/历史不默认设置天数 TTL。删除数据和物理缩库分开执行，不能每次结果保存后 VACUUM。旧项目目录不属于此回收器范围。

## 文件与验证

实现 `tests/integration/bootstrap/test_project_result_receipts.py`、`tests/persistence/task_store/test_independent_lifecycle.py` 与 `tests/integration/gui/test_task_storage_history.py`。

故障矩阵每个点必须有断言：项目实际值/revision、凭据、任务候选、任务处理状态及是否发生重复外部调用。补充数据库 busy/只读、任务库损坏而项目仍可打开、删除后新建同名项目、源更新后重新合入、关闭中迟到回调及下载/AI/助手的同协议集成。

本 Story 不实现跨项目自动迁移候选，不提供旧项目绑定成果格式的恢复桥，不更改模型提示词或三方比较产品策略。
