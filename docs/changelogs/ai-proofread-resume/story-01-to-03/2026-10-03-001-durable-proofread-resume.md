# 校对按批持久化与重启续跑

- 日期：2026-10-03
- Epic：ai-proofread-resume；Story：S01～S03
- [计划](../../../../plans/ai-proofread-resume/plan.md)

## 问题与结果

退出程序会取消校对任务，旧历史只是只读报告，不包含可执行断点；重新创建任务会再次请求所有条目。本次为默认 proofread 策略的独立/混合校对增加逐条持久化候选。每个完成批次在报告进度前原子落盘，重启后重新选择原内容并启动，默认复用有效候选，只请求剩余条目。恢复候选仍经过术语闭环和 TaskSession 的应用/版本校验。

原始工作区已有大量未提交修改。本记录只归属下列符号级新增或调整，不将既有取消、术语并发、部分成功、日志和历史功能归为本次开发。

## 变更文件

### S01：持久化与恢复

- 新增 `src/transbridge/application/translation/proofread_checkpoint.py`：项目/版本/来源/输出配置隔离的 SQLite 存储；输入修订、原文、原译文、上下文、stage 指纹与术语哈希校验；每批事务提交、连接关闭、损坏/写入失败显式报错；不保存凭据。
- 修改 `src/transbridge/application/translation/proofread_batch_dispatch.py`：新增完成批次回调，持久化错误不被进度异常处理吞掉。
- 修改 `src/transbridge/application/translation/_open_proofread_stage.py`：仅透传可选批次回调，不改变已有恢复与拆批合同。
- 修改 `src/transbridge/application/translation/terminology_closure.py`：修复结果验证后回调；含部分取消的批次仍可保存有效子集。
- 修改 `src/transbridge/application/translation/proofread_stage.py`：恢复候选、剩余分批、原顺序合并、术语复检、累计恢复进度及术语观察接线。
- 修改 `src/transbridge/ai_translator/post_processor/proofread_pipeline.py`：工厂接受可选检查点；术语解析失败明确携带原异常，避免把空词库当作可恢复身份。
- 新增 `tests/application/translation/test_proofread_checkpoint.py`：取消、新实例、跨进程强制退出、输入/作用域/术语失效、术语修复续跑、失败不缓存、并发、显式重跑及存储故障。

### S02：桌面接线

- 修改 `src/transbridge/ui/tools/ai_translator/proofread_composition.py`：向共享流水线透传检查点。
- 修改同目录 `task_run.py`、`task_worker.py`、`source_execution.py`：项目目录经 worker 传入执行器，为每个来源按 owner 项目/版本身份构建存储。
- 修改同目录 `run_controller.py`：不可变请求增加 `reuse_proofread` 数据字段，默认开启；没有增加控制器新职责。
- 修改同目录 `task_runtime.py`：冻结用户续跑/重跑选择。
- 修改同目录 `single_task_view.py`：润色/混合模式显示默认勾选的“继续上次校对”；翻译模式隐藏，提示仅默认校对策略适用。
- 修改同目录 `task_history_dialog.py`：明确历史只读与重新启动校对复用断点的操作方式。
- 修改同目录 `task_entry_results.py`：取消阻止应用，但不抹除已验证的候选和已完成证据。
- 新增 `tests/ui/tools/test_proofread_resume_wiring.py`：真实组合流水线跨执行器复用、版本隔离、强制重跑、取消历史不应用原项目。
- 修改 `tests/ui/tools/test_unified_task_progress.py`：测试替身补齐真实 TaskSession 的项目目录合同。
- 修改 `tests/ui/tools/test_ai_translator_task_layout.py`、`test_unified_task_runtime.py`：模式显示和复用选择传递回归。

### S03：旧日志救援与文档

- 新增 `src/transbridge/application/translation/proofread_log_recovery.py`：离线解析原请求/响应，以当前逐条响应验证器保留有效子集，按数值调用序号去重，输出待身份和术语复核的救援证据。
- 新增 `scripts/recover_proofread_logs.py`：显式输入/输出路径，不覆盖已有文件、不修改项目、不调用模型。
- 新增 `tests/application/translation/test_proofread_log_recovery.py`：结构/语法失败隔离、数值顺序去重和不完整日志统计。
- 新增 `plans/ai-proofread-resume/plan.md` 并更新 `plans/INDEX.md`、`docs/changelogs/INDEX.md` 对应条目。

## 验证

1. 聚焦校对、断点与部分结果：86 项通过。
2. 后处理与 UI 生命周期扩展验证：237 项通过。
3. `uv run pytest tests/application/translation tests/ai_translator/post_processor tests/ui/tools tests/ui/test_ai_task_shutdown.py -q`：首次 915 通过、24 失败，原因为测试替身缺少新增接线依赖的项目目录属性；补齐后同命令 **939 通过**。
4. 最后收窄续跑开关可见模式后，`uv run pytest tests/ui/tools/test_ai_translator_task_layout.py tests/ui/tools/test_unified_task_runtime.py -q`：**22 通过**，包括新增 3 项显示/选择传递验证。
5. `uv run ruff check src tests scripts/recover_proofread_logs.py`、`uv run ruff format --check src tests scripts/recover_proofread_logs.py` 和 `git -c core.safecrlf=false diff --check`：通过。
6. 使用 `.venv/Scripts/python.exe scripts/recover_proofread_logs.py <用户日志目录> <用户救援文件>` 处理 856 份旧日志，恢复 **8181 个独立条目**，其中 **5420 个候选变化**；66 个条目响应未通过验证（含重试，不代表独立失败条目数）。产物位于 Git 忽略的 `data/recovery/`，未纳入版本管理。

uv 最初受到缓存目录读取权限限制，授权读取缓存后测试正常。测试含已有 SWIG/TranslationEntry 兼容 API 弃用警告；没有真实 API 调用。

## 兼容与剩余边界

- 本次持久化仅接入桌面的独立/混合 proofread，不改变 strict 或正式翻译引擎既有断点合同。
- 历史记录保持报告语义；重启不会自动发起付费调用。用户重新打开相同项目版本、选择原内容并启动，才执行恢复和剩余请求。
- 旧日志缺少完整模型与版本证据，救援文件不能自动冒充新断点，也未自动应用。新检查点无需迁移旧历史。
- 已保存候选可以仍需术语修复；语法疑问降级结果不作为通过语法验证的候选缓存。用户可取消勾选续跑来强制重做。
- 未运行整仓 pytest、真实网络 API、安装包构建或安装版替换；源码变更需要使用更新后的源码/构建版本。
- 尺寸复核：`run_controller.py` 为 560 行，本次只有既有请求数据声明新增字段；存储职责已独立。其余扩展文件均低于 500 行。
- 没有创建任务临时目录；救援文件是用户交付物，予以保留。没有 commit/push。
