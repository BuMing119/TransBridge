# AI 自动应用、持久化完成提示与实时日志

日期：2026-10-03。Epic：[ai-partial-results](../../../../plans/ai-partial-results/plan.md)，Story 04～06。

## 行为

- 未启用润色预览时，每轮自动应用有效结果并保存项目/后快照；部分失败只重试失败集合，不再阻止成功条目应用。
- stage 2（有疑问）是成功结果，译文与 stage 一起发布、持久化，并按最终 stage 统计。
- 明确启用预览时仍需确认；拒绝不重试，取消不提交未应用候选。
- 项目保存完成后弹出摘要；保存失败、快照失败分开提示并提供重试。旧轮次通知不能在新轮次弹出。
- 运行中可打开 LLM 日志，自动刷新当前文件和列表；上翻保留阅读位置，关闭停止刷新。

## 本轮变更边界

工作区包含此前尚未提交的续跑、部分结果和其他功能。本记录仅归属以下符号级增量，不将完整 Git diff 归入本次任务。

- `task_entry_results.py`：publish_to_drafts 按来源一次 ChangeSet；decide 一次校验全部候选。
- `translation_entry_collection.py`：apply 在 patch 循环外构建 expected revision 映射，去除隐藏平方复杂度；保留冲突和身份检查。
- `task_apply_preparation.py`（新增）、`task_draft.py`、`task_session.py`：后台准备独立输入和基线；GUI 线程等待 worker 结束、复核输入/版本/配置、原子提交。回调可立即串接保存；保存深拷贝在后台执行，保留同步接口。
- `task_completion.py`（新增）、`task_run.py`：自动应用、保存与通知协调；应用 busy、旧 attempt 通知隔离、异步成功后的远端 client 清理。
- `task_progress.py`：非阻塞完成摘要窗口、错误/重试入口；日志入口不再等待完成结果；预览 ready_keys 只计算一次。
- `task_run_presentation.py`：有疑问按 stage 2 统计。
- `source_execution.py`、`task_worker.py`：创建日志目录即发送 log_ready，包括创建失败的空路径，重试不得打开旧日志。
- `_llm_log_viewer.py`：可见时每秒刷新，当前内容未变不重读，上翻阅读不抢位置，读取上限 2 MiB / 显示 30000 行。
- `single_task_view.py`：续跑开关隐藏时保留空间，修复同会话前次续跑功能导致的模式切换布局跳动，不改模式语义。
- 新增 `test_ai_result_publication.py`、`test_ai_task_async_apply.py`、`test_live_llm_logs.py`、`test_task_completion_flow.py`；更新部分成功、统一进度、混合完成和摘要回归。
- 更新原部分结果计划及索引的当前规则；历史增量不改写。

## 性能证据

合成数据、无真实项目和模型调用。仅 publish_to_drafts：2000 条旧 5 次中位 3.3678 秒，新 5 次中位 0.0343 秒；8276 条旧单次 99.7605 秒，新 5 次中位 0.2051 秒。此结果不代表完整界面操作耗时。

8300 条合成会话及内存持久化：后台合并和基线准备约 0.616 秒，主线程提交约 0.043 秒，保存启动约 0.006 秒；会话应用并保存约 1 秒，5 ms GUI 心跳最大间隔约 76 ms。对照优化前最大间隔约 580 ms。实际权威投影和 UI 刷新仍在 GUI 线程；真实项目耗时随内容与环境变化。

## 验证

- `.venv/Scripts/python.exe -m pytest tests/ui/tools tests/ui/test_ai_task_shutdown.py tests/converter tests/contracts/io -q --tb=short --basetemp=.tmp-ai-completion-tests`：834 通过。
- 最终 stage 口径调整后，`test_ai_task_presentation.py` 与显式指定 `tests/converter/tests_translation_entry_collection.py`：24 通过。
- `.venv/Scripts/python.exe -m ruff check src tests` 与 `ruff format --check src tests`：通过（1487 个文件）。
- `git diff --check`：通过，仅既有 LF/CRLF 提示。
- uv 全局缓存受沙箱限制，改用项目已有 Python 环境；pytest 临时目录访问使用获批命令。未改依赖或锁文件。
- 独立 QA 核验取消、旧信号、重复提交、保存失败和退出等待链，未发现新增可达数据丢失缺陷。

未运行真实 LLM、用户真实项目写入或安装包构建。无数据格式迁移；本轮未提交 Git。

## 责任边界复核

任务完成与异步准备各提取独立模块；AiTaskRun 29 个方法、TaskSession 30 个方法，均低于 500 行。已有大集合模块仅局部修复索引映射热点，不新增职责；未来扩展该模块需先拆分。

## 最终组合验证补充

窄顺序组合曾因模块级 qapp fixture 销毁/重建 QApplication 而提前退出。监听 destroyed 信号已确认，保留进程级强引用的只读对照 31 项通过。将部分任务与统一进度测试的 qapp 改为 session 生命周期后，原顺序组合及集合测试 50 项通过（`test_ai_task_presentation.py test_ai_partial_task.py test_task_completion_flow.py tests_translation_entry_collection.py`）。这是测试生命周期修正，不修改业务绕过。最终有疑问统计只依赖 stage 2，不依赖诊断文字。
