# 工作台与助手共用 ParaTranz 工程绑定、项目详情与导出

2026-11-22 · [助手工具复用桌面功能](../../../../plans/assistant-shared-business-tools/plan.md) · S3

## 工程绑定与远端详情

- `get_paratranz_project` 从工作台读取当前本地工程保存的绑定和状态。断网时仍可查询；浏览其他云端项目不会改变结果。显式查询其他远端项目时，`get_project_info` 使用指定的 ID；无效 ID 直接返回错误。[目标解析](../../../../src/transbridge/application/projects/target_context.py)
- `switch_paratranz_project` 和桌面绑定入口共用工程绑定命令及“我的项目”目录。查询期间工程、修订、账号或端点变化会中止操作。后台助手通过桌面线程提交，已有的本轮撤销继续恢复原绑定。[绑定命令](../../../../src/transbridge/paratranz/project_binding.py) · [工作台与助手双向对照](../../../../tests/smart_assistant/test_workbench_project_binding.py)
- 项目列表、概览和远端详情统一读取整数 `members` 及 `privacy`。此前桌面会把其他字段当成人数，助手则把整数人数报作 0。概览沿用已加载的目录数据，仅在信息不完整时后台调用同一项目详情服务。成员数为 0 时显示 0，未提供时桌面显示“—”、助手返回 `null`。[官方项目数据结构](https://paratranz.cn/api-docs?format=json) · [项目模型](../../../../src/transbridge/application/ports/paratranz.py) · [项目概览对照](../../../../tests/ui/test_paratranz_management_context.py)

## 导出

- `export_artifact` 的默认 `trigger` 操作沿用现有调用方式。桌面和助手共用提交、按作业 ID 轮询和识别新工件的实现；助手返回新工件元数据，等待超时则保留作业信息。[工件发布服务](../../../../src/transbridge/application/sync/artifact.py)
- `download` 对应桌面“下载导出包”，直接读取已生成的 ZIP。桌面助手复用保存窗口，用户选择最终文件并确认覆盖；无界面调用沿用授权目录。桌面与助手均先写临时文件，再校验 ZIP 并替换目标。取消、无权限或 ZIP 损坏时保留原文件。[桌面导出入口](../../../../src/transbridge/ui/paratranz/export_tab.py) · [模拟 HTTP 请求和文件对照](../../../../tests/smart_assistant/tools/test_export_artifact_parity.py)

## 验证

- 使用临时本地工程、Qt 控件和模拟远端。工作台在缓存显示旧名称后提交，绑定采用再次查询得到的新名称；成员资格已撤回时本地修订不变。助手反向切换后，工作台显示与离线绑定查询一致。[绑定测试](../../../../tests/smart_assistant/test_workbench_project_binding.py)
- 模拟传输记录确认，下载已有 ZIP 只发一次下载 GET；新导出只提交一次 POST。桌面上拒绝覆盖、取消选择或确认后更改文件，原文件均得到保留。[文件确认测试](../../../../tests/ui/tools/smart_assistant/test_confirmation_lifecycle.py)
- 更新了两条会话摘要测试的词条夹具，使用实际词条身份和翻译阶段验证统计变化。[会话摘要测试](../../../../tests/smart_assistant/test_round_context.py)

最终联合回归通过 2225 项。1 项线上契约测试因未配置专用测试账号和工程跳过。全仓 Ruff 检查、格式检查和差异空白检查均通过。

```powershell
uv run --offline --no-sync --no-cache pytest tests/smart_assistant tests/paratranz tests/contracts/paratranz tests/ui/tools/smart_assistant/test_confirmation_lifecycle.py tests/ui/tools/smart_assistant/test_confirmed_file_upload.py tests/ui/test_paratranz_management_context.py tests/ui/test_paratranz_permission_refresh.py tests/ui/test_paratranz_layout_stability.py tests/ui/test_paratranz_secondary_state.py tests/ui/test_paratranz_project_binding_validation.py tests/ui/test_remote_target_presenter.py tests/ui/test_remote_target_dialog_lifecycle.py tests/ui/test_remote_target_layout.py tests/ui/test_paratranz_delete_confirmation.py tests/ui/tools/test_unified_task_runtime.py tests/ui/tools/test_ai_launch_config_snapshot.py tests/application/projects/test_assistant_binding_undo.py tests/application/projects/test_remote_binding.py -q -m 'not llm' --tb=short --disable-warnings -rs
.venv/Scripts/ruff.exe check src tests
.venv/Scripts/ruff.exe format --check src tests
git -c core.safecrlf=false diff --check
```
