# S4：已保存 AI 结果审阅使用后台批量读取

- 日期：2026-11-11
- Epic/Story：ai-task-recovery / S4，结果与保存恢复。
- 关联：[计划](../../../../plans/ai-task-recovery/plan.md)。

## 原因与范围

用户的 UI 卡顿堆栈指向 `TaskMergeReview.refresh.ready` → `ProjectRowsCollection.get` → `ProjectPagedRead.entry` → `ProjectStore.read_entries`。任务结果文件虽已在后台读取，但完成回调仍在主线程逐条获取当前词条；分页缓存未命中时每条都打开数据库、检查版本并查询。用户明确要求先修复这个已有证据的界面阻塞，本次不修改卡顿 watchdog 或 Python 原生崩溃处理。

现在在 GUI 线程冻结来源读接口、版本 token 与当前投影修订，后台按来源批量读取目标词条，并整理结果、当前内容及覆盖前证据。回到 GUI 时只接收已准备好的映射；如果工程/版本、修订或来源集合已变化，保留已保存结果供查看，但不发布过时的当前内容，提示刷新。窗口关闭/关闭流程期间丢弃迟到结果。

仍以完整 EntryKey 匹配，保留未保存投影覆盖和删除语义。普通内存集合在 GUI 线程冻结字段值，后台不访问活动集合或 Qt 对象；分页集合冻结读端口与不可变投影覆盖，I/O 全部在既有 worker 中执行。SQLite 继续复用 `read_entries` 的每 200 个 key SQL 分片及 token 校验，所谓一次批量调用不是只执行一条 SQL。

读取期间失效的结果不能用于处理；有效审阅显示后又发生的修改，继续由既有 `decision_states` 逐词条过期决策规则保护，不改为阻断整个合入，避免改变原有冲突报告行为。

## 可归属变更

- 修改 [project_rows.py](../../../../src/transbridge/ui/project_rows.py)：增加 `ProjectRowsCollection.freeze_entries`，返回绑定来源、token 与覆盖状态的后台批量读函数；不走逐条 `get` 或访问可变分页缓存。模块 329 行、主类 26 个方法，仍属于分页集合读取职责。
- 新增 [task_review_snapshot.py](../../../../src/transbridge/ui/tools/ai_translator/task_review_snapshot.py)：`review_stamp` 捕获 GUI 投影身份；`freeze_current_reads` 分离分页读端口与内存快照；`prepare_review` 在后台生成三组审阅映射。`saved_state` 从控制器迁移，原导入位置继续显式重导出。
- 修改 [task_merge_review.py](../../../../src/transbridge/ui/tools/ai_translator/task_merge_review.py)：refresh 将读取及整理交给已有 TaskResultPreparation；ready 验证读取起点并丢弃关闭后的结果；拒绝没有有效当前快照的处理操作，保留后续逐词条决策保护。
- 新增 [test_task_review_background_reads.py](../../../../tests/ui/tools/test_task_review_background_reads.py)：8 项最终用例，使用隔离真实 SQLite 与离屏 Qt；覆盖 1,000 / 20,162 条词条的后台批量读取及等待期间心跳，切换项目、修改修订、替换集合、关闭期间的迟到回调，覆盖/删除/命名空间语义和数据库 token 过期拒绝。
- 新增本记录，更新 [索引](../../INDEX.md) 和计划的直接关联链接。无文件删除、schema/依赖/锁文件变化；旧工程和任务无需迁移。

本任务开始时已有 ParaTranz 压缩包修复、其增量记录、发布 skill 与 `.transbridge/` 改动；均保留，不计入本次修复。

## 实际验证

环境：Windows、Python 3.12.12、现有 uv 环境，`QT_QPA_PLATFORM=offscreen`。测试数据仅位于任务专用 `.tmp-review-read-20261111`，没有修改用户工程或执行 LLM 请求。测试因本机沙箱临时文件权限约束获准在沙箱外执行。

初轮 35 项测试中 34 通过、1 失败：新增的全局修订拦截改变了“审阅后新编辑”的既有 `decision_stale` 结果。修正为只阻止读取期间失效的快照，审阅后的编辑继续走既有逐词条校验。

扩大回归 **86 项通过**，包括结果生命周期、版本隔离、任务恢复、分页工作台和 SQLite 读写；随后新增同规模 20,162 条参数，专项 **8 项通过**。两组重叠 7 项，合计 **87 个不同用例通过**。

```powershell
$env:TEMP = 'D:\MyCode\TransBridge\.tmp-review-read-20261111'
$env:TMP = $env:TEMP
$env:QT_QPA_PLATFORM = 'offscreen'
uv run --no-sync pytest tests/ui/tools/test_task_review_background_reads.py tests/ui/tools/test_ai_result_identity_audit2.py tests/ui/tools/test_ai_result_lifecycle.py tests/ui/tools/test_full_task_recovery_ui.py tests/integration/gui/test_project_paged_workbench.py tests/persistence/project_store/test_entries.py -q -p no:cacheprovider --basetemp .tmp-review-read-20261111/regression --tb=short
uv run --no-sync pytest tests/ui/tools/test_task_review_background_reads.py -q -p no:cacheprovider --basetemp .tmp-review-read-20261111/scale --tb=short
uv run --no-sync ruff check src tests --no-cache
uv run --no-sync ruff format --check src tests --no-cache
```

最终 Ruff 检查通过、2542 文件格式检查通过。中间一次导入排序及测试长行格式问题已修正。告警为既有 SWIG 与旧测试直接修改字段的弃用提示。

规模用例通过实际读接口记录验证：单来源 20,162 个 key 一次批量调用，调用线程不是 GUI 线程；用受控阻塞读验证 Qt 定时器仍继续响应，再释放读取并核对完整结果。没有设置硬件相关耗时门槛，也不把这项心跳验证当成整个窗口所有阶段的性能保证。

## 验证边界

- 未复现或修复 `0xC0000005`，没有改变原生卡顿采样机制。
- 未执行全仓 pytest、真实用户项目写入、真实 AI 请求、安装包构建或发布。
- 旧内存集合仍在 GUI 线程冻结内存字段值；本次消除的是分页数据库逐条 I/O 和结果整理回调的阻塞。
