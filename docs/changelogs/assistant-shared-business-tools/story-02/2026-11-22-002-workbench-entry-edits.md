# 助手复用工作台译文编辑、阶段变更与翻译范围

2026-11-22 · [助手工具复用桌面功能](../../../../plans/assistant-shared-business-tools/plan.md) · S2 第二批

## 译文和阶段

工作台表格和 `edit_translation` 共用 [workbench_edits.py](../../../../src/transbridge/application/workbench_edits.py) 准备译文与阶段，再通过 `AppContext.update_projected_entry` 写入同一个工程命令。阶段 0 首次填入非空译文时默认转到阶段 1；显式指定阶段优先。首尾空白和表格占位文字“（无译文）”按工作台原有规则处理。

表格右键菜单和 `set_stage` 共用阶段变更规则，经 `replace_entry_states` 一次提交，保留原译文。助手按完整身份去重，只把实际变更计入更新数。已经是目标阶段的条目无需写入。[桌面阶段命令](../../../../src/transbridge/ui/workbench/translation_stage_action.py) · [助手命令适配](../../../../src/transbridge/smart_assistant/tools/_editor_edits.py)

确认卡保存每个编辑步骤的目标、原值和修订。确认与执行之间若目标已被修改，助手拒绝这一笔写入。同一计划顺序修改一条词条时，后一项预览以先前步骤的预期结果为准。提交时与桌面对话编辑共用工程快照核验，词条缺失或已删除时停止写入。[确认快照](../../../../src/transbridge/smart_assistant/entry_edit_review.py) · [工程快照核验](../../../../src/transbridge/ui/entry_projection.py)

## 翻译范围

桌面翻译、助手范围预览和启动使用同一套可执行词条分组。混合模式按配置和词条标签区分翻译、润色与跳过；同一请求里设置的阶段、分类和标签范围用于后续步骤。独立润色以前按阶段推断 `passed` 和 `has_issues`，这两个旧启动参数现要求重新选择。桌面后处理仍按实际质检结果选择润色范围。[桌面任务范围](../../../../src/transbridge/application/translation/task_scope.py) · [助手范围适配](../../../../src/transbridge/smart_assistant/tools/translation_scope.py)

未设置范围时，翻译预览显示阶段 0，润色预览显示阶段 1、2、3、5，混合模式按动作规则选词。显式清空条件会预览全部符合执行条件的词条。传入具体词条时，预览显示去重后的选区数量；有标识缺失或存在歧义时，启动前要求重新选择。

## 验证

使用两个内容相同的临时工程，分别从真实 Qt 表格和助手注册工具执行相同操作，再比较工程保存的译文、阶段、标签和外部引用。首次填写、清空占位文字和去除首尾空白的三个用例在改动前失败。改动后这三项、菜单批量改阶段，以及一次提交译文和显式阶段的用例均通过。[两端对照用例](../../../../tests/smart_assistant/test_workbench_edit_parity.py)

真实 Qt 确认卡经单步和计划两条路径提交，分别验证同一词条被手改时保留手改、其他词条被手改时原计划仍可完成。三个任务模式从桌面创建任务与助手启动入口对照完整词条身份，覆盖同显示 ID、锁定词条、空白原文以及分页标签。[确认卡回归](../../../../tests/ui/tools/smart_assistant/test_confirmed_entry_edits.py) · [翻译范围回归](../../../../tests/smart_assistant/test_desktop_translation_scope.py)

最终代码上的编辑、确认、范围、撤销与对话编辑聚焦回归通过 72 项。以下测试均使用本地临时工程和测试替身，未调用在线模型。

```powershell
uv run --offline --no-sync --no-cache pytest tests/smart_assistant/test_workbench_edit_parity.py tests/smart_assistant/test_desktop_translation_scope.py tests/ui/tools/smart_assistant/test_confirmed_entry_edits.py tests/smart_assistant/test_operation_change_preview.py tests/smart_assistant/test_workbench_request_filters.py tests/smart_assistant/tools/test_original_match_tool_paths.py tests/smart_assistant/test_undo_capture.py tests/ui/test_dialogue_authority.py tests/ui/test_dialogue_editing_flow.py -q --tb=short --disable-warnings
.venv/Scripts/ruff.exe check src tests
.venv/Scripts/ruff.exe format --check src tests
git -c core.safecrlf=false diff --check
```

助手、请求存储、工作台、分页工程和桌面翻译窗口联合离线回归最终通过 2251 项，跳过 12 项。联合回归包含上述聚焦用例。分页切换来源的测试在读取完整结果前等待当前来源的完整查询就绪。会话清空测试在前一次联合运行中遇到一次会话存储提交失败，单独复跑及最终联合回归均通过。

```powershell
uv run --offline --no-sync --no-cache pytest tests/smart_assistant tests/ui/tools/smart_assistant tests/ui/tools/test_ai_project_task_scope.py tests/ui/tools/test_ai_translator_slices.py tests/application/assistant_requests tests/ui/workbench tests/ui/test_dialogue_editing_flow.py tests/ui/test_dialogue_authority.py tests/ui/test_translation_reset.py tests/ui/test_step2_incremental_rendering.py tests/ui/test_translation_table_sorting.py tests/ui/test_interaction_filters.py tests/integration/gui/test_project_paged_workbench.py tests/application/projects/test_variant_commands.py tests/integration/gui/test_app_context_projection.py tests/ui/test_project_view_installation.py -q -m 'not llm' --tb=short --disable-warnings
```
