# 修复 ESP 写回校验、执行反馈、保存状态与主日志初始化

- 日期：2026-10-09
- Epic / Story：maintenance / write-save-feedback（非计划维护）
- 入口：用户提供 0.2.1.2 使用截图与日志，完成只读诊断后授权 bm-pilot 修复已知问题。

## 证据与范围

本地复现了原文限定任务日志写回时 `FIDELITY_MISMATCH`、写回任务失败但计划窗口直接关闭、自动保存成功后按钮仍停留在 SAVING，以及 root 已有处理器时 `basicConfig()` 跳过文件日志配置。交叉审查另复现保存线程已结束但 GUI 收尾尚未派发时的重复保存竞态。

朋友日志中 2026-10-09 主日志为 0 字节，不能确认现场写回错误码；2026-10-06 同一 Neiva 插件的三次 `SOURCE_LOCATOR_CONFLICT` 属于历史译文导入操作。当前修复依据可复现代码缺陷，不把历史错误或空日志归因为已证明的现场原因。日志包在内存读取，未保存用户翻译数据到仓库。

## 逐文件变更

- 新增 `src/transbridge/application/io/publish/plugin_fidelity.py`：从不可变源快照建立所有物理字符串位置的预期，按顺序、定位、类型和 string ID 核对 staged ESP，保留未请求、空白和冲突跳过条目；共享 localized string ID 传播同一预期文本。失败保留异常类型及原因。避免译文改变 `EntryKey.original` 后被旧身份过滤，也避免相同/交叉译文掩盖位置错写。
- 修改 `src/transbridge/application/io/publish/validators.py`：ESP 通过结构检测后进入专责物理位置校验；其他格式保留原适配器回读校验。
- 修改 `src/transbridge/application/io/publish/coordinator.py`：渲染失败保留适配器诊断码和原因到失败消息；外层 `RENDER_FAILED` 码及不替换正式目标的行为不变。
- 新增 `src/transbridge/ui/operations/write_dialog.py`：执行后保留非模态窗口，显示运行状态、成功/部分完成/失败/取消、实际产物路径及完整诊断；通过 queued Qt 信号更新界面。运行期间禁用重复提交、编辑和关闭，终态后允许关闭或重新预检。
- 修改 `src/transbridge/ui/operations/facade.py`：连接任务活动与 `OperationTaskAdapter.result`，支持提交期间即完成的任务；终态、关闭及销毁时解除订阅。
- 修改 `src/transbridge/ui/operations/coordinator.py`：保护执行期间的重复信号，并在窗口 finished / destroyed 时释放会话。
- 修改 `src/transbridge/ui/operations/production.py`：仅新增写回专用对话框及结果读取端口的装配，不扩展已有大模块职责。
- 修改 `src/transbridge/ui/workbench/_project_bar.py`：新增明确的保存完成入口，结束 SAVING 后依据权威 dirty 状态显示 CLEAN 或 DIRTY，恢复保存按钮。
- 修改 `src/transbridge/ui/main_window.py`：成功回调调用保存完成入口；以 `_save_worker` 所有权而非仅 `isRunning()` 合并保存请求，GUI 收尾前不允许前台任务抢先进入，避免旧回调污染新保存。该 564 行模块仅局部修正既有保存职责，没有新增职责。
- 修改 `src/transbridge/ui/app.py`：显式安装应用自己的 FileHandler，保留已有第三方处理器；重复初始化复用、目录切换关闭旧自有处理器；设置 DEBUG 并立即记录版本及日志位置。保留本任务开始前已有的 `DiagnosticLogFormatter` 改动，不将其计入本次新增功能。
- 新增 `tests/integration/publish/test_plugin_fidelity.py`：19 项真实二进制回归，包含不同/相同/交换/部分译文、本地化和共享 ID、错位及未授权改动拒绝、空白/重复条目保留、中文路径，以及仓库 sample.esp 全部 7,492 条写出。
- 新增 `tests/integration/publish/test_render_diagnostics.py`：渲染失败保留具体诊断、原目标不变、staging 清理。
- 新增 `tests/ui/operations/test_write_execution_feedback.py`：10 项执行反馈回归，涵盖四种终态、早完成和真实后台线程、实际 XML 输出、重复确认及窗口释放。
- 新增 `tests/ui/test_save_completion_state.py`：6 项真实 ApiWorker + ProjectBar 回归，涵盖手动/自动保存、保存期间修改、失败重试、native 完成但 GUI 回调尚未派发。
- 修改 `tests/ui/test_background_gui_operations.py`：三个保存测试替身对齐明确的保存完成端口。
- 新增 `tests/ui/test_logging_setup.py`：5 项回归，覆盖预配置/未配置 root、重复初始化、目录切换及既有凭证保护。
- 修改 `docs/changelogs/INDEX.md`：仅添加本记录的入口。

工作区原有的其他代码、需求、计划、ADR、依赖版本和锁文件变更不属于本次记录。未修改 `tests/integration/publish/test_atomic_publish.py` 的既有改动。

## 验证

集成验证（退出码 0）：

```powershell
uv run --no-sync pytest tests/integration/publish tests/contracts/io tests/parser/test_original_match_plugin.py tests/writer/test_plugin_writer.py tests/ui/operations tests/ui/test_save_completion_state.py tests/ui/test_logging_setup.py tests/ui/test_background_gui_operations.py tests/ui/test_workbench_story07.py tests/ui/test_v2_variant_hydration.py tests/security/test_data_boundaries.py tests/integration/gui/test_ui_foundation_startup.py -q
```

结果：**427 passed、1 skipped**，66.76 秒。跳过项为 Windows 环境不支持的符号链接检查；7 条警告为既有 SWIG 和兼容 API 弃用提示。该轮包含最初 5 项保存状态测试。

补充保存所有权竞态修复后的验证（退出码 0）：

```powershell
uv run --no-sync pytest tests/ui/test_ai_task_shutdown.py tests/ui/test_worker_shutdown_registry.py tests/ui/test_save_completion_state.py tests/ui/test_background_gui_operations.py -q
```

结果：**41 passed**，含第 6 项保存回归；与集成回归存在重叠，不累计为独立测试总数。

```powershell
uv run --no-sync ruff check src tests
uv run --no-sync ruff format --check src tests
git diff --check
```

静态检查、1577 个文件格式检查和差异空白检查通过。使用仓库现有 uv 环境及锁文件；因沙箱禁止写 uv 缓存，测试通过授权的正常本地权限执行，未安装或升级依赖。

## 兼容与未验证项

无需工程数据迁移，不修改翻译条目身份、格式协议或备份/原子发布策略。保真验证仍拒绝错位写入和未请求的条目变动；窗口显示的成功路径来自实际任务结果。

已确认修复范围无未完成项。未运行全仓库测试、真实网络/LLM 调用或安装包构建；没有朋友原始 ESP，尚未现场复验。未提交、推送或发布。
