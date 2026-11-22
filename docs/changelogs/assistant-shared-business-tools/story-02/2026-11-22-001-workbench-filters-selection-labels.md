# 助手复用工作台筛选、行选区和批量标签

2026-11-22 · [助手工具复用桌面功能](../../../../plans/assistant-shared-business-tools/plan.md) · S2 第一批

## 筛选与请求

- [workbench_filters.py](../../../../src/transbridge/application/workbench_filters.py) 承接工作台原有的筛选状态和匹配规则。[FiltersPresenter](../../../../src/transbridge/ui/workbench/filters_presenter.py) 与助手都调用它。工作台和 AppContext 双向同步筛选，普通集合和工程分页查询共用阶段、分类、标签和四个搜索框的规则。
- [tool_editor.py](../../../../src/transbridge/smart_assistant/tools/tool_editor.py) 的 `set_filters` 应用正式筛选状态，`get_visible_entries` 用完整来源计算筛选总数并只保留请求页。[workbench_rows.py](../../../../src/transbridge/smart_assistant/tools/workbench_rows.py) 将共享查询限制在本次请求获准的来源及已修复的词条范围。旧的独立 ID、上下文搜索字段返回诊断。
- [request_scope.py](../../../../src/transbridge/smart_assistant/request_scope.py) 与 [request_execution.py](../../../../src/transbridge/smart_assistant/request_execution.py) 保存本请求显式设置的筛选。随后查询、预览和批量操作可在新建的执行上下文中恢复同一条件。来源切换时拒绝继续，保存筛选失败时恢复工作台原状态。分页工程按已捕获的词条身份批量读取选区。

## 行选择和标签

- [row_selection.py](../../../../src/transbridge/ui/workbench/row_selection.py) 接入工作台表格已有的 Qt 行选择模型。[project_paging.py](../../../../src/transbridge/ui/workbench/project_paging.py) 提供完整分页结果，确认整个范围已就绪后处理视口外的行。助手请求捕获实际选区与来源。
- 工作台向桌面翻译窗口交接当前选区时传递完整词条身份。两条词条的旧显示 ID 相同，选中其中一条再排序，翻译预览和启动输入仍只含原来选中的那一条。普通旧字符串选区仍由现有范围读取器处理。[选区适配](../../../../src/transbridge/ui/tools/ai_translator/scope_presenter.py)
- [workbench_labels.py](../../../../src/transbridge/application/workbench_labels.py) 承接原桌面标签操作，桌面和助手对明确的词条身份提交标签变更。[_editor_labels.py](../../../../src/transbridge/smart_assistant/tools/_editor_labels.py) 与 [operation_preview.py](../../../../src/transbridge/smart_assistant/operation_preview.py) 共用批量目标。空范围和已全部赋过相同标签的范围均写入零条，返回值分别列出命中数与新增数。
- 当不同词条共用旧显示 ID、现有标签记录无法唯一归属时，工作台和助手都在提交前提示歧义。助手按名称操作时也会拒绝同名的多个标签，确认预览给出相同原因。
- [react_execution_binding.py](../../../../src/transbridge/ui/tools/smart_assistant/react_execution_binding.py) 在用户确认后使用已有后台线程准备标签操作。正式提交回到工作台所属线程，核对执行许可、来源、取消状态与版本。涉及的现行参数和操作见 [FR9](../../../../docs/requirements.md)。

## 验证

- [请求级回归](../../../../tests/smart_assistant/test_workbench_request_filters.py) 使用 3 条无标签词条验证查询、确认预览与批量赋值均为零，核对非空范围的实际身份及变更数，并覆盖请求存储失败。
- [分页工作台回归](../../../../tests/integration/gui/test_project_paged_workbench.py) 在真实 Qt 表格和临时工程中选择第 571 行并捕获原请求，验证非法组合或来源切换后保留已有选区。[标签操作回归](../../../../tests/smart_assistant/tools/test_workbench_label_actions.py) 核对重复赋值、分页、原标签和窄选区。
- [确认写入回归](../../../../tests/ui/tools/smart_assistant/test_confirmed_labels.py) 用真实 Qt 事件循环测试确认时拒绝、扫描中取消、主线程提交前取消、执行许可失效和切换来源。正常情况只在主线程提交一次并生成原请求的回执。以下聚焦回归通过 26 项。

```powershell
uv run --offline --no-sync --no-cache pytest tests/ui/tools/smart_assistant/test_confirmed_labels.py tests/ui/tools/smart_assistant/test_confirmed_file_upload.py tests/smart_assistant/test_workbench_request_filters.py tests/smart_assistant/test_operation_change_preview.py -q --tb=short
```

补跑桌面排序、右键编辑、分页及标签用例，共 94 项通过。测试使用本地临时工程，未调用线上模型。

```powershell
uv run --offline --no-sync --no-cache pytest tests/ui/test_translation_table_sorting.py tests/ui/test_translation_reset.py tests/smart_assistant/tools/test_workbench_label_actions.py tests/integration/gui/test_project_paged_workbench.py tests/ui/tools/smart_assistant/test_confirmed_labels.py -q --tb=short
```

助手、工作台、请求存储、Qt 面板与 UI 契约的联合离线回归通过 2144 项，跳过 12 项。上述聚焦回归与此命令有重叠。

```powershell
uv run --offline --no-sync --no-cache pytest tests/smart_assistant tests/ui/workbench tests/ui/tools/smart_assistant tests/ui/test_interaction_filters.py tests/ui/test_step2_incremental_rendering.py tests/ui/test_workbench_filter_layout.py tests/ui/test_translation_table_sorting.py tests/integration/gui/test_project_paged_workbench.py tests/application/assistant_requests tests/contracts/ui -q -m "not llm" --tb=short
.venv/Scripts/ruff.exe check src tests
.venv/Scripts/ruff.exe format --check src tests
git -c core.safecrlf=false diff --check
```

选区交接改为完整身份后，补跑以下翻译窗口、任务生命周期和分页回归，通过 69 项。

```powershell
uv run --offline --no-sync --no-cache pytest tests/ui/tools/test_ai_project_task_scope.py tests/ui/tools/test_ai_translator_slices.py tests/smart_assistant/tools/test_translation_task_lifecycle.py tests/smart_assistant/tools/test_original_match_tool_paths.py tests/integration/gui/test_project_paged_workbench.py -q --tb=short
```

下一批继续 S2 的译文编辑与阶段变更。
