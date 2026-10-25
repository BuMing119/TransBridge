# S17：下载后自动保存当前本地版本

日期：2026-10-25。Epic / Story：paratranz-categorized-upload / S17。
关联：[计划](../../../../plans/paratranz-categorized-upload/plan.md)、[FR3.3](../../../requirements.md)、[使用说明](../../../dev/paratranz-categorized-upload.md)。

## 需求与结果

用户要求下载成功后自动保存当前本地版本，不再手动点击保存；保留下载前还原点，保存失败可在原窗口重试。

当前权威工程的单插件和批量下载均显式使用持久化提交：先创建下载前还原点，生成完整候选，磁盘事务成功后才交换内存与发布界面状态。当前版本已有未保存编辑一并保留并保存；不会创建新的命名版本或修改其他版本。普通编辑默认仍使用原来的保存流程。

写入失败保留原工作内容、还原点、下载快照及人工复核选择，可以原窗口重试，不重新读取成功云端内容；相同工作修订的还原点复用。写盘已完成但回执丢失或首次验证失败时，会在共享存储锁内精确核验完整 Project/Variant，避免假失败或重复写入。无法确认的异常如实显示为未确认保存成功，并保留原异常原因，不笼统承诺磁盘从未发生变化。

## 本次可归属增量

工作区已有大量未提交改动。主会话在任务开始为项目、UI、bootstrap、相关测试/文档保存专属起始副本；后续授权修改的 executor/task_adapter 依据子 agent 修改前读取及明确的符号级增量核验。以下不包含这些文件的其他 HEAD 差异，也不把同时发生的无关文档索引改动归入本轮。

- **新增 `application/projects/durable_variant.py`**：DurableVariantCommitService 用临时聚合验证下一版本，复用 LifecycleSave/UoW 保存完整工程及版本；写入成功才发布，真实失败不清除原未保存工作。提供中文权限、空间和 I/O 原因；写后异常精确核验支持原地恢复。空 patch 但当前版本仍有未保存内容时也能持久化。
- **修改 `application/projects/gui_facade.py`、`bootstrap/persistence.py`**：增加可选持久化服务注入，replace_entry_records 的 persist=True 显式接入；默认 False 保持其他编辑行为。bootstrap 复用现有 UoW 与工程/版本只读端口，无新 schema 或依赖。
- **修改 `application/projects/lifecycle.py`**：commit_active_content 增加可选 commit_mutation；在既有生命周期锁内将 before_publish 的磁盘事务与内存交换一起交给提交仲裁，之后再发布投影，不向大模块加入下载业务。
- **修改 `ui/operations/batch_download_commit.py`、`batch_download_session.py`**：批量提交持久化；按工程/版本及工作修订复用已成功还原点。失败保留计划并原地重试，正常修订竞争可重新捕获；候选被并发编辑为相同云端内容时，仍保存当前版本后才返回成功。成功文本明确已更新并保存。
- **修改 `ui/operations/batch_download_dialog.py`**：主动作改为“更新并保存”，成功文案明确当前版本已保存、下载前还原点保留。
- **修改 `ui/operations/production_support.py`、`paratranz_sync.py`**：权威单插件 DOWNLOAD 透传 persist=True，上传和历史兼容调用默认不变。还原点缓存以 DownloadSession 对象作键并有界保留，防止仅存 id 的复用风险；保存失败重用同一成功还原点和远端快照。
- **修改 `application/sync/executor.py`**：仅本地事务异常分支保留具体异常原因，LOCAL_TRANSACTION_FAILED 继续可重试，不改变远端执行合同。
- **修改 `application/sync/task_adapter.py`**：TaskDraft 新增默认 False 的 final_local_commit，仅 GUI 权威下载开启。最终 mutation 成功后同锁写入 operation_outcome 并完成任务，避免迟到取消反转已保存结果；失败保留实际错误和状态。终态不再调用不合法的 update_progress。
- **修改 `ui/operations/facade.py`、`production.py`**：仅对标记 await_result_receipt 的任务，暂存提前到达且尚无结果的终态；生产 retain_result 后主动 notify_result_ready，将真实保存结果推送窗口。无需轮询或伪造成功回执，关闭窗口清理登记，其他任务保留原合同。
- **修改 `ui/operations/paratranz_dialog.py`**：当前权威下载有可信完成回执时显示“已自动保存到当前本地版本”；失败保留具体原因及原窗口重试入口。
- **新增测试**：`tests/application/projects/test_durable_variant.py`、`tests/ui/operations/test_batch_download_autosave.py`、`tests/ui/operations/test_download_autosave_boundaries.py`。涵盖真实文件落盘读回、原未保存工作、双文档事务/补偿、权限/空间错误、写后异常核验、同候选重试、空 patch 保存、版本切换和真实线程取消边界。
- **修改测试**：`tests/ui/operations/test_batch_download_dialog.py`、`test_paratranz_download_recovery.py`、`test_paratranz_sync_dialog.py`，验证明确已保存提示、单插件真实持久化、失败后选择/快照/还原点保留、真实生产窗口终态先于回执时仍正确呈现，以及部分词条暂缓时任务状态字段不丢失。
- **修改文档**：FR3.3、两份 ParaTranz 使用说明、S17 计划与直接相关索引原位更新。S16 历史日志保持不变。

职责复核：新持久化服务 167 行/3 方法，GUI facade 475 行/24 方法，批量会话约 377 行/13 方法；其他新增行为留在独立服务。既有超阈值 lifecycle 仅扩展其原有内容发布仲裁钩子，后续若增加生命周期业务仍须先按职责拆分。

## 验证

最终整合使用仓库 uv 环境，不更新锁文件；所有 cache/TEMP/pytest 目录位于本任务专属临时目录。Faiss 基础 CPU 选项只作用于测试进程，沿用 S16 已核验的加载方式。

```powershell
$env:FAISS_OPT_LEVEL = 'generic'
$env:TEMP = (Resolve-Path .tmp-download-autosave-a0eb07b2/temp).Path
$env:TMP = $env:TEMP
uv --cache-dir .tmp-download-autosave-a0eb07b2/uv-cache run --no-sync pytest tests/ui/operations tests/ui/test_download_plan_routing.py tests/application/projects tests/contracts/paratranz tests/persistence/v2 --ignore=tests/contracts/paratranz/test_terms_api_contract.py -k 'not test_reordered_source_projects_into_context_only_remote_plan' -q --basetemp .tmp-download-autosave-a0eb07b2/final-regression2
```

结果：**1222 passed、1 deselected、3 条既有弃用 warning，46.35 秒**。只排除 S16 已用启动副本独立复现的上传排序断言，本轮未改该上传逻辑。terms 接口未改动，未纳入此次验证；没有排除下载、保存或恢复测试。

全库命令：

```powershell
uv --cache-dir .tmp-download-autosave-a0eb07b2/uv-cache run --no-sync ruff check src tests
uv --cache-dir .tmp-download-autosave-a0eb07b2/uv-cache run --no-sync ruff format --check src tests
```

结果：**全部通过，2182 files already formatted**。相关 tracked 文件的 `git -c core.whitespace=cr-at-eol diff --check -- <本轮文件>` 通过。

中间验证与修复：

- 初期新增批量测试因跨模块 fixture 未导入其 project 依赖而出现 setup error，修正测试 fixture 导出后，真实保存及原地重试通过。
- 独立审查确认 UoW rollback 本身不承诺撤销已完成替换；增加完整候选精确核验，真实“Variant 已写成但回执丢失/首次读后验证失败”和双文档写失败补偿测试通过。
- 真实 ThreadBackend/Qt 窗口制造提前终态，发现若没有 result-ready 通知，窗口会在实际回执前解锁且看不到已保存提示；桥接后成功、失败与原窗口重试均正确交付。
- 首次扩大回归 **1217 passed / 4 failed / 1 deselected**，四项均为提前完成后遗漏旧 operation_outcome 字段；修复提交临界区状态写入并加入“更新一条、暂缓一条”回归，原四项及最终扩大回归全部通过。

## 边界与清理

无依赖、工程 schema、云端文件布局或命名版本迁移。当前工程 GUI 下载自动保存，独立通用同步、上传及普通编辑的默认合同保留。已保存后的连接清理异常仍保留独立诊断，不撤销磁盘事实；最终诊断以真实 OperationResult 为准。

未运行全库 pytest、真实账号下载、构建/安装包；没有提交 Git 或修改真实云端数据。仍保留 S16 已记录的一项既有上传排序断言问题。所有本任务临时目录将在归属核验后清理，不删除其他工作线文件。
