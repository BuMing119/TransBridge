# 001: QA 审查修复 — 5项 Blocker+Critical 修复 (B1+C3+C4+C1+C2)

**日期**: 2026-05-21
**类型**: 改
**关联**: Epic: Agent 工具系统全面扩展 > Story: qa-review (跨Story QA审查修复)

## 修改文件

### `src/transbridge/smart_assistant/tools/tool_translator.py` (改)
- **修改内容**: `_tool_stop_task()` 第337行 `ToolResult.ok(..., partial=True)` → `ToolResult.partial_ok(...)`（`partial` 非 `ok()` 接受参数）；第352行 `ToolResult.fail(..., data={...})` → 移除 `data=` 参数（`fail()` 不接受 `data=` kwarg）
- **原因**: 修复对不存在的 task_id 调用 stop_task 时 TypeError 崩溃（B1）

### `src/transbridge/smart_assistant/tools/task_manager.py` (改)
- **修改内容**: `on_completed()` / `on_failed()` / `remove_listener()` 三个方法包裹 `with self._lock` 保护 `_listeners` 修改；`notify_completed()` / `notify_failed()` 改为锁内 `list()` 快照 + 锁外派发，避免回调执行时持有锁
- **原因**: 消除并发注册/移除回调时 `RuntimeError: dictionary changed size during iteration` 风险（C3）

### `src/transbridge/smart_assistant/tools/tool_paratranz.py` (改)
- **修改内容**: `export_artifact` 工具注册添加 `require_confirmation: True`
- **原因**: 与 `upload_entries` / `download_entries` 权限确认行为一致，防止 LLM 未经用户确认触发服务端导出（C4）

### `tests/smart_assistant/test_tool_consolidation.py` (改)
- **修改内容**: `TestStopTask.test_stop_specific_task_id` 和 `test_stop_nonexistent_task_returns_fail` 从 `assertRaises(TypeError)` 改为正常断言 `assertFalse(r.success)` + 检查消息内容
- **原因**: B1 修复后函数正确返回 `ToolResult.fail()` 而非抛出 TypeError，测试需同步适配

### `tests/conftest.py` (改)
- **修改内容**: `MockAppContext.__init__()` 新增 `self.current_project = None` 和 `self.paratranz_project_id = None` 属性
- **原因**: ParaTranz 工具测试需要这两个属性（工具通过 `getattr(ctx, 'current_project', None)` 访问），缺失导致 17 个测试 AttributeError

### `tests/smart_assistant/tools/__init__.py` (增)
- **修改内容**: 新建空 `__init__.py` 使 `tests/smart_assistant/tools/` 成为 Python 包
- **原因**: pytest 需要包结构来正确发现测试

### `tests/smart_assistant/tools/test_paratranz_tools.py` (增, 280行)
- **修改内容**: 9 个测试类（TestListProjects / TestGetProjectInfo / TestCompareWithRemote / TestUploadEntries / TestDownloadEntries / TestExportArtifact / TestGetUploadHistory / TestGetParatranzProject / TestSwitchParatranzProject），共 19 个测试用例，覆盖参数校验/错误路径/happy path
- **原因**: 补全 ParaTranz 工具零测试覆盖缺口（C1），均为无网络调用的 mock 测试

### `tests/smart_assistant/tools/test_run_postprocess.py` (增, 165行)
- **修改内容**: 4 个测试类（TestRunPostprocessValidation / TestGetQualityReport / TestListQualityReports / TestSummarizeHelpers），共 15 个测试用例，覆盖参数校验/默认值/质量报告/历史报告/辅助函数
- **原因**: 补全 `_tool_run_postprocess` 零执行测试缺口（C2），均为 mock 测试不触发真实 LLM 调用

### `docs/test-reports/agent-tool-expansion-qa-full-2026-05-21.md` (增)
- **修改内容**: 4 维度并行 QA 审查报告，发现 26 项问题（1B+4C+10M+11m），综合评分 45/60
- **原因**: QA 审查产出

### `docs/test-reports/agent-tool-expansion-qa-verify-2026-05-21.md` (增)
- **修改内容**: 复验报告，5/5 Blocker+Critical 修复验证通过，0 新问题引入，34 新测试通过
- **原因**: 修复后复验产出
