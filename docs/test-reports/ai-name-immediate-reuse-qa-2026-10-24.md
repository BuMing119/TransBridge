# 项目名称即时草稿复用 QA

- 日期：2026-10-24
- 范围：[S10 计划](../../plans/ai-project-concurrency/plan.md)、FR5.13.11、[ADR-048](../adr/048-ai-project-concurrency-and-terms.md)。
- 环境：仓库现有 uv 环境与锁文件，Python 3.12.12 / Windows；模型 Provider 使用测试替身，候选提交、任务草稿和结果账本使用真实实现。

## 验收结果

107 项专项测试通过。阻塞第一个未知名称的模型请求时，所有已知名称在两个来源的真实任务草稿中已经具有有效译文、stage=2 及增加后的修订；后续翻译不重复写入，未知名称仅翻译一次。

- 草稿提交顺序：先持久化候选，再执行受保护提交并保存提交证据；候选保存失败或提交前取消不会写入该来源。
- 作用域与内容保护：名称类型、跨插件重复项、插件特例、受保护状态、占位符、已有异译、只校对范围和禁用检索的既有测试保持通过。
- 失败隔离：未知名称模型失败保留已写入项；后一来源存储失败保留前一来源草稿，阻止剩余模型启动，并以未完成检查的失败状态交付结果。
- 暂停与取消：候选落盘后暂停会等待提交，取消阻止迟到提交；名称模型阶段取消保留已有草稿译文，但账本没有可应用成功项。
- 幂等与接续：重复名称准备不增加第二次修订；全量复用时无翻译模型调用；启用后处理仍校对已填译文；同一执行的检查点接续不重复计数或应用。
- 证据校验：完整词条身份、范围、原文、上下文、译文、状态、修订及提交来源均参与验证；旧式字段变更即使未增加修订也拒绝接续，相同译文不能伪造已提交证据。
- 展示：写入阶段按出现条数显示，随后按已提交的独立名称数显示“已写入草稿”；尚未写入时不提前显示成功计数。

## 执行命令

专项命令结果：**107 passed，4 warnings**。警告来自既有 SWIG 类型和旧测试中的直接 context 修改。

```powershell
uv --cache-dir .tmp-ai-immediate-reuse-20261024/uv-cache run --no-sync pytest tests/application/translation/test_draft_reuse.py tests/ai_translator/test_translation_reuse.py tests/ai_translator/test_prepared_names.py tests/ai_translator/test_name_preparation_plan.py tests/ui/tools/test_ai_project_name_reuse.py tests/ui/tools/test_ai_project_name_preparation.py tests/ui/tools/test_ai_project_name_progress.py tests/ui/tools/test_ai_project_name_barrier.py tests/ui/tools/test_ai_project_name_scheduling.py tests/ui/tools/test_ai_task_progress_presentation.py -q --basetemp=.tmp-ai-immediate-reuse-20261024/final-focused
```

来源并发、共享术语事务、最终检查及统一执行另经 **108 passed**（与专项有 9 项重叠）：

```powershell
uv --cache-dir .tmp-ai-immediate-reuse-20261024/uv-cache run --no-sync pytest tests/ui/tools/test_ai_source_concurrency.py tests/ui/tools/test_ai_source_term_closure.py tests/ui/tools/test_ai_task_term_closure.py tests/ui/tools/test_ai_task_term_transactions.py tests/ui/tools/test_ai_task_term_writes.py tests/ui/tools/test_shared_task_term_consistency.py tests/ui/tools/test_unified_ai_execution.py tests/ui/tools/test_ai_project_name_reuse.py -q --tb=short --basetemp=.tmp-ai-immediate-reuse-20261024/source-regression
```

后端完整相关矩阵 **830 passed，4 warnings**：

```powershell
uv --cache-dir .tmp-ai-immediate-reuse-20261024/uv-cache run --no-sync pytest tests/ai_translator tests/application/translation tests/infra/test_limited_llm_client.py -q --tb=short --basetemp=.q-imr/backend
```

包含较大 UI 集合的扩大回归命令（正常权限环境，未完成）：

```powershell
uv --cache-dir .tmp-ai-immediate-reuse-20261024/uv-cache run --no-sync pytest tests/ai_translator tests/application/translation tests/ui/tools tests/infra/test_limited_llm_client.py --ignore=tests/ui/tools/smart_assistant --ignore=tests/ui/tools/terminology --ignore=tests/ui/tools/test_ai_launch_refresh_guard.py --deselect='tests/ui/tools/test_ai_mode_switching.py::test_mode_switches_preserve_shell_and_common_field_anchors[size1-14]' --deselect='tests/ui/tools/test_ai_mode_switching.py::test_mode_switches_preserve_shell_and_common_field_anchors[size2-10]' -q --tb=short --basetemp=.q-imr/full
```

该矩阵在 `test_ai_history_result_interactions.py` 执行期间以 Windows 0xc0000409 退出，未获得整套结果，不能计为通过。该文件单独运行 **7 passed**，根因尚未确定；本次不把它定性为已修复的环境问题。矩阵排除与本次无关的智能助手、独立术语工作台子目录，以及已在 [S05 记录](../changelogs/ai-project-concurrency/story-05/2026-10-24-003-project-progress-ui.md) 中基线复现的两项配置窗口布局断言。

```powershell
uv --cache-dir .tmp-ai-immediate-reuse-20261024/uv-cache run --no-sync pytest tests/ui/tools/test_ai_history_result_interactions.py -vv -s --maxfail=1 --basetemp=.q-imr/history
```

此前受限沙箱内两次扩大运行未完成：FAISS DLL 加载报 Windows 0xc0000008，测试进程随后以 0xc0000005 退出；单独复现的任务恢复测试在 `os.link` 遇到 WinError 5。正常权限环境可继续执行，未更改依赖或测试中的保护逻辑。包含所有 UI 子目录的探索性运行在进入无关智能助手矩阵后停止，改用上述相关范围；其部分输出不计为整套通过。

正常权限运行仍受测试临时路径长度影响：两个校对检查点测试在较长路径报 SQLite `unable to open database file`。保持代码不变，缩短临时目录后该组 **33 passed**，所以后续矩阵使用任务专属短路径 `.q-imr/`；缩短路径未消除上述较大 UI 集合退出。

```powershell
uv --cache-dir .tmp-ai-immediate-reuse-20261024/uv-cache run --no-sync pytest tests/application/translation/test_proofread_checkpoint.py -q --tb=short --basetemp=.q-imr/checkpoint
```

另一工作线运行期间新增的 `test_ai_launch_refresh_guard.py` 使用不存在的 `AiTranslatorWindow` 类名导入（实际类为 `AITranslatorWindow`），曾导致整个集合收集失败；本次没有改动该文件，最终矩阵显式排除并单独注明。

静态检查命令：

```powershell
uv --cache-dir .tmp-ai-immediate-reuse-20261024/uv-cache run --no-sync ruff check src tests
uv --cache-dir .tmp-ai-immediate-reuse-20261024/uv-cache run --no-sync ruff format --check src tests
```

本次变更的 16 个生产/测试文件定向 Ruff 检查和格式检查均通过，目标 Git 差异无空白错误；相关新文档链接与代码围栏已校验。全库 `ruff check` 通过；全库格式检查曾通过（2078 个文件），12:25 复验时另一工作线正在编辑的 `tests/ui/tools/test_live_llm_logs.py` 有 1 处格式差异（其余 2079 个文件通过），本次未修改该文件。此前另一工作线测试中的 fixture 重定义已消失。

## 审查与边界

新增提交组件不依赖 Qt；名称编排在既有后台执行器中完成。`translator.py` 已超过仓库大小门禁，本次只加入可选依赖、范围过滤和提交/报告接线，新提交及证据责任放在独立模块。后续新增流程仍应继续提取，不能将提交实现搬回主控。

本次基线快照用于识别既有及并发未提交改动，只审查归属于 S10 的增量。没有修改模型、锁文件、安装包或用户翻译数据；没有执行 commit/push。

提前写入仅修改任务草稿。任务结果仍沿现有应用入口合入主窗口来源；取消不自动应用或保存项目。每个来源独立提交，整个项目不承诺原子性。新增参数默认空，无数据结构迁移；旧执行缺少提前提交证据时需新建任务。本次没有扩展跨进程翻译恢复能力。

未运行真实模型 API、真实项目大批量耗时基准或打包应用桌面验收；不声称模型语义质量或端到端耗时比例已得到验证。
