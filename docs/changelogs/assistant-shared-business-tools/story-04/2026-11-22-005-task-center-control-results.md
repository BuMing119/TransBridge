# 任务中心控制与已保存成果

2026-11-22 · [整改方案 S4](../../../../plans/assistant-shared-business-tools/plan.md)

- 桌面任务中心的停止按钮调用 `RuntimeTaskProjection.control(..., "cancel", expected_revision=...)`，桌面术语同步页的停止按钮经 `TerminologySyncTaskAdapter` 交给同一个任务运行时。助手 `get_task_status` 和 `stop_task` 可直接使用 `terminology_sync_execute` 返回的任务 ID。原生任务按发起者、助手入口、会话、工程和版本查询，并通过任务投影提交带修订的控制。批量操作逐项提交，每个 worker 退出前仍显示“取消中”。桥接任务的结果和消息仍由原有句柄提供。[桌面控制器](../../../../src/transbridge/ui/shell/task_center.py) · [术语同步控制](../../../../src/transbridge/ui/tools/terminology/sync_task_adapter.py) · [入口装配](../../../../src/transbridge/ui/app.py) · [助手任务控制](../../../../src/transbridge/smart_assistant/tools/task_control.py)
- 助手与任务历史窗口共用 `TaskHistoryService` 的项目可用性检查和报告投影。助手打开限于当前 owner/session 的存储视图，跳过旧逐条上传档案。已删除工程的旧成果可查看其候选内容。[任务历史](../../../../src/transbridge/application/tasks/history_service.py) · [助手成果查询](../../../../src/transbridge/smart_assistant/tools/translation_task_results.py)
- 翻译任务窗口与助手共用保存候选的选择校验、三方工程合入、已有应用回执复用与提交后的确认。任务中心复核与助手解读同一份候选原文、上下文和来源定位，并可读取助手保存的覆盖前证据。助手写入仍经请求取消和撤销回执边界。可合入的导入、下载、词典历史记录仅在原工程版本中写入，只有来源登记信息的记录无法重新登记。[共享成果命令](../../../../src/transbridge/application/translation/saved_candidates.py) · [桌面任务提交](../../../../src/transbridge/ui/tools/ai_translator/task_session_merge.py) · [桌面复核](../../../../src/transbridge/ui/tools/ai_translator/task_merge_review.py) · [助手提交适配](../../../../src/transbridge/smart_assistant/tools/translation_task_merge.py)
- 离线回归以两项同属一个会话的任务分别点击桌面按钮、调用助手，比较暂停、继续和停止后的实际状态。原生术语回归用同一份计划提交两项任务，对照桌面同步停止接口与助手通用工具，验证取消信号、worker 退出前后的状态、旧修订拒绝、跨入口及跨作用域拒绝，以及已清理的桥接任务不可重新枚举。历史记录用同一存储对照桌面报告，检查历史来源只读、旧 AI 校对候选、合入冲突及取消后的磁盘状态。[按钮与工具对照](../../../../tests/ui/test_task_center.py) · [原生任务与权限回归](../../../../tests/smart_assistant/test_task_control_scope.py) · [历史种类及权限](../../../../tests/smart_assistant/tools/test_translation_task_lifecycle.py) · [删除工程后的双入口查询](../../../../tests/integration/gui/test_task_storage_history.py)

```powershell
uv run --offline --no-sync --no-cache pytest tests/ui/tools/test_unified_task_runtime.py tests/smart_assistant/test_runtime_v2_cancellation.py tests/ui/test_task_center.py tests/smart_assistant/tools/test_translation_task_lifecycle.py tests/integration/gui/test_task_storage_history.py tests/smart_assistant/test_task_control_scope.py -q --tb=short
```

72 项通过。测试使用临时工程，不迁移历史记录。

```powershell
uv run --offline --no-sync --no-cache pytest tests/smart_assistant/test_task_control_scope.py tests/ui/test_task_center.py tests/smart_assistant/test_runtime_worker_controls.py tests/smart_assistant/test_runtime_v2_cancellation.py tests/ui/tools/test_unified_task_runtime.py tests/smart_assistant/postprocess/test_task_manager.py tests/smart_assistant/test_tool_consolidation.py tests/ui/tools/smart_assistant/test_submission_lifecycle.py -q --tb=short
```

原生术语任务补充回归共 130 项通过。术语计划输入和远端执行使用离线测试替身。
