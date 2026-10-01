# 完整回归与来源更新菜单翻译模板补齐

- 日期：2026-10-01
- Epic：source-file-update；关联 paratranz-context-order
- Story：01～03 修改后完整验证
- 方式：三个子 Agent 分工执行全库测试、来源迁移复查、ParaTranz 顺序复查与静态检查，主会话核验失败归属。

## 本次修改

- `src/transbridge/ui/i18n/messages.pot`：增加“更新源文件…”消息及 action catalog 来源定位。全量测试发现新增菜单没有同步进入翻译模板，本次补齐该遗漏；中文源语言继续使用既有 msgid 回退规则，无需为其添加重复 PO 翻译。
- `plans/source-file-update/plan.md`：将验证范围更新为本轮实际全库分组结果，保留未通过项与未执行的真实远端、桌面人工验收边界。
- `docs/changelogs/INDEX.md`：加入本记录入口。
- 不修改测试断言，不回退其他工作线的助手 UI 改动，不修改工程 schema，不写入真实 ParaTranz。

## 完整覆盖结果

原始 `uv run pytest -q` 因两个既有同名测试模块收集冲突停止；尝试 importlib 模式又遇既有 helper/scripts 导入问题。改用不重叠目录分组，并将存在 Qt 聚合进程异常的部分 UI 测试拆分执行，覆盖全部 **4803 项**：**4787 passed、7 断言失败、8 skipped、1 项因 Qt 回调异常退出**。这不是单进程全套通过，也不是整库全绿。

- `uv run pytest -q -ra --ignore=tests/application/terminology_sync --ignore=tests/ui`：3693 passed、5 failed、7 skipped，569.87 秒，61319 warnings。
- `uv run pytest tests/application/terminology_sync -q -ra`：44 passed。
- `uv run pytest tests/ui -q -ra --ignore=tests/ui/test_accessibility_contracts.py --ignore=tests/ui/test_dialogue_editor.py --ignore=tests/ui/test_dialogue_authority.py --ignore=tests/ui/tools/smart_assistant`：842 passed、2 failed、1 skipped，66.82 秒，1320 warnings。
- 对 `tests/ui/tools/smart_assistant` 的全部 18 个测试文件分别运行 `uv run pytest <file> -q`：163 passed，每个进程均退出 0。
- `uv run pytest tests/ui/test_dialogue_editor.py -q`：29 passed。
- `uv run pytest tests/ui/test_dialogue_authority.py -q`：9 passed。
- `uv run pytest tests/ui/test_accessibility_contracts.py -q -k 'not test_task_center_escape_never_stops_task_and_stop_description_names_object_and_recovery'`：7 passed、1 deselected；被排除的一项已独立执行并确认回调异常退出，计入上述总数中的异常项。

UI 使用 `QT_QPA_PLATFORM=offscreen`。跳过项为五项符号链接权限限制、一项 Windows 非法文件名、一项环境长路径限制，以及未配置专用 ParaTranz 远端合同测试凭据的一项。重复复测和专项验证不重复计入 4803 项。

## 失败归属与遗留问题

1. `tests/contracts/projects/test_authoritative_mutation_paths.py::test_direct_projection_writes_stay_inside_reviewed_commit_or_rollback_boundaries`：基线测试仍列出旧的直接写入函数，实际已委托 `projection_restore.py`；仅加载 HEAD 测试和扫描文件也可复现。
2. `tests/integration/terminology/test_migration_fault_rehearsal.py::test_project_v3_migration_uses_a_copy_and_retains_verified_v2_backup`：基线测试要求 schema 3，当前及 HEAD 均为 4。
3. `tests/integration/terminology/test_ui_workflow.py::test_ui_areas_stay_object_oriented_and_delegate_business_commands`：基线测试要求四页，当前及 HEAD 均为三页。
4. `tests/paratranz/test_entry_upsert.py::test_assistant_upload_reuses_only_the_target_projects_known_identity[8]`：重复 key 返回失败，却统一断言成功；内存替换为 HEAD 实现仍失败，不能复用其他项目的远端 ID。
5. `tests/quality/test_success_chains.py::test_fomod_typed_nine_stage_success_chain_deterministic`：基线时间戳波动。文件独立复测 12 passed、目标单独复测 1 passed；相同内容间隔 2.2 秒生成 ZIP 可复现时间戳及归档指纹变化。独立通过不撤销全套失败记录。
6. `tests/ui/foundation/test_locale_service.py::test_source_template_covers_menu_catalog_and_critical_settings_msgids`：本次新增缺失消息已补齐；复测仍因 HEAD 原有两条搜索消息缺失失败。修复后运行 locale 与来源更新 UI 两文件，15 passed、1 failed；失败集合只剩“搜索历史翻译与术语…”及“按原文或译文搜索所有已保存的本地翻译与术语”。
7. `tests/ui/tools/test_layout_stability.py::test_smart_assistant_input_is_one_bounded_composer_card`：其他工作线的未提交助手 UI 改动将最大高度 112 改为 88，测试仍要求 112。仅在内存加载 HEAD 的 `input_view.py` 后通过；不能归为 HEAD 既有失败。本轮保留该工作线改动。
8. 无障碍用例 `test_task_center_escape_never_stops_task_and_stop_description_names_object_and_recovery`：基线 fixture 的 `available_actions` 缺少 `recover` 等字段，Qt 回调触发 AttributeError 并退出进程；控件和测试均与 HEAD 一致。剩余七项已全部执行通过。

此外，较大 UI 进程先后在对话编辑器和助手会话管理清理阶段出现 Windows access violation；对应文件在独立进程全部通过。聚合运行的生命周期/状态干扰尚未解决，不能以隔离通过认定崩溃已修复。

## 专项复查与最终检查

- 来源更新四个专项文件共 38 passed，额外三个临时工程场景通过：旧源丢失且多次保存后仍保留各版本独立业务状态；预览后编辑保存拒绝旧预览；ESP 未变但 Strings 改变时拒绝提交且磁盘业务数据不变。
- ParaTranz 顺序及原有适配器/往返/同步入口两组共 170 passed、1 skipped；确认仅主动发起并确认同步计划后按远端 ID 更新 context，保留拆分前序号及过期/重试校验。
- 最终 `uv run ruff check src tests`、`uv run ruff format --check src tests`（1432 文件）、`git -c core.safecrlf=false diff --check` 均通过。
- 自建临时工程已清理；没有提交代码、打包发布、真实远端写入或人工桌面验收。
