# 助手复用工作台统计、标签目录和应用状态

2026-11-22 · [助手工具复用桌面功能](../../../../plans/assistant-shared-business-tools/plan.md) · S2 第三批

## 统计

- 从[工作台摘要](../../../../src/transbridge/ui/workbench/workflow_presenter.py)和[分页统计](../../../../src/transbridge/ui/workbench/project_paging.py)提取[共用的阶段分组与来源统计](../../../../src/transbridge/application/workbench_statistics.py)。工作台按阶段划分已译、未译、有疑问及隐藏，按桌面分类名汇总词条。[表格](../../../../src/transbridge/ui/workbench/step2.py)刷新筛选按钮时使用同一批计数。
- [get_statistics](../../../../src/transbridge/smart_assistant/tools/tool_default.py) 经[工作台读取接口](../../../../src/transbridge/ui/context.py)获取分组、百分比和分布。已译数从“有译文的行数”改为阶段 1、3、5、9 的词条数。有疑问及隐藏数单独返回。筛选不会改变来源总数。[每轮工作区摘要](../../../../src/transbridge/smart_assistant/context_builder.py)也改用相同统计；读取失败时提示重新查询。
- [分页来源](../../../../src/transbridge/ui/project_rows.py)复用相同修订和读取版本的统计结果。工程未保存的修改进入新修订后重新统计。请求已缩小到部分词条时，按完整身份读这些行及其标签，再使用同一个统计函数。[助手范围适配](../../../../src/transbridge/smart_assistant/tools/workbench_rows.py)

## 标签目录

- [工作台筛选按钮](../../../../src/transbridge/ui/workbench/filters_view.py)与 [list_labels](../../../../src/transbridge/smart_assistant/tools/tool_editor.py) 共用[标签目录](../../../../src/transbridge/application/workbench_labels.py)，返回相同的名称、颜色和当前来源内的计数。未使用的库内标签仍可查询，计数为 0。
- 内存工程按完整词条身份读取标签。分页读取包含未显示的行。底栏与标签按钮共用已打标签的词条总数。[标签对照测试](../../../../tests/smart_assistant/test_workbench_label_catalog.py)

## 当前应用状态

- [来源选择框](../../../../src/transbridge/ui/workbench/widget.py)、[工程栏](../../../../src/transbridge/ui/workbench/_project_bar.py)与工具共用[已加载来源和版本名称](../../../../src/transbridge/ui/projection_types.py)。外部切换来源后，下拉框也跟随新的活动来源。
- 状态读取在桌面线程取同一份工程、版本和当前槽位。已受理请求的筛选由[请求范围](../../../../src/transbridge/smart_assistant/request_scope.py)恢复。来源在排队期间被切换时，工具返回要求重新核对的诊断。[状态测试](../../../../tests/smart_assistant/test_workbench_app_state.py)
- [设置页](../../../../src/transbridge/ui/settings/center_layout.py)与助手读取[当前配置对象上的凭据状态](../../../../src/transbridge/config/paratranz.py)。回答只含布尔值，文件字段只含文件名。

## 验证

四条词条分别处在阶段 0、2、5、-1，其中仅阶段 5 应计入工作台“已译”。修复前助手返回 3 条已译，修复后桌面、工具和每轮摘要都返回 1 条。两个同显示 ID 的词条分属红蓝标签时，两端均为各 1 条。[统计对照](../../../../tests/smart_assistant/test_workbench_statistics.py) · [分页与限定范围](../../../../tests/smart_assistant/tools/test_workbench_label_actions.py)

助手、桌面工作台、设置页和分页工程联合离线回归通过 1469 项。调整内存表格重建标签的路径后，补跑工作台筛选、阶段变更及统计对照，通过 72 项。未调用线上模型或 ParaTranz 服务。

```powershell
uv run --offline --no-sync --no-cache pytest tests/smart_assistant tests/ui/workbench tests/ui/test_workbench_story07.py tests/ui/test_workbench_slices.py tests/ui/test_workbench_context_layout.py tests/ui/test_workbench_filter_layout.py tests/ui/test_interaction_filters.py tests/ui/test_authoritative_content_projection.py tests/ui/test_step2_incremental_rendering.py tests/ui/test_project_labels.py tests/ui/test_label_manager_interactions.py tests/integration/gui/test_project_paged_workbench.py tests/ui/guidance/test_qt_binding.py tests/ui/test_settings_keyboard_navigation.py tests/ui/test_ui_settings_dialog.py tests/ui/test_settings_connection_feedback.py tests/ui/test_save_completion_state.py tests/ui/test_layout_stability.py tests/integration/gui/test_app_context_projection.py -q -m 'not llm' --tb=short --disable-warnings
.venv/Scripts/ruff.exe check src tests
.venv/Scripts/ruff.exe format --check src tests
git -c core.safecrlf=false diff --check
```

对齐每轮工作区摘要后，工具、会话上下文、Qt 请求面板及其性能回归共 84 项通过。首次联合运行中，一条取消请求的用例遇到会话存储提交失败；单独复验三个参数组合及重跑整组均通过。以上测试组有重叠。

```powershell
uv run --offline --no-sync --no-cache pytest tests/smart_assistant/test_context_builder.py tests/smart_assistant/test_conversation_orchestrator_lifecycle.py tests/smart_assistant/test_workbench_statistics.py tests/smart_assistant/test_workbench_label_catalog.py tests/smart_assistant/test_workbench_app_state.py tests/smart_assistant/tools/test_workbench_label_actions.py tests/ui/tools/smart_assistant/test_request_lifecycle_panel.py tests/ui/tools/smart_assistant/test_context_performance.py -q --disable-warnings --tb=short
```
