# 收尾：版本目录与成果附件同名

- 日期：2026-10-26
- Epic / Story：task-result-lifecycle / S01–S05
- 前序：[003 统一运行流程](2026-10-26-003-single-runtime-legacy-import.md)
- 证据：[QA](../../../test-reports/task-result-lifecycle-unification-2026-10-26.md)

003 记录完成后，实现 Agent 交回最后一项边界修正：旧版本可能合法命名为 `ai-task-history`。修改 `persistence/legacy_project_archive.py` 优先识别已登记版本的手动历史；`legacy_project_tasks.py` 从成果附件集合排除这些版本文件；`project_archive.py` 将已登记版本名称传入捕获入口。没有新增运行时或改变原文件。

新增 `tests/persistence/v2/test_legacy_project_task_assets.py::test_variant_named_like_sidecar_directory_keeps_its_manual_history`。主会话执行该文件及 `test_legacy_project_directory.py`、`test_legacy_project_archive.py`，**50 passed**。全库 Ruff check、format check（2189 文件）、`git diff --check` 通过；最终 pytest collect-only **9361 项**。这些收尾结果接续 003 的 9360 项收集记录，不代表执行了全套测试。既有扩大矩阵限制仍见 QA。
