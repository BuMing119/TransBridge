# S05～S08：已有译文正常编辑与按操作补充输入

日期：2026-10-28（Asia/Shanghai）。Epic：application-preflight；Story：S05～S08。
关联：[计划](../../../../plans/application-preflight/plan.md)、[ADR-050](../../../adr/050-application-preflight-and-optional-source-reference.md)、[需求](../../../requirements.md)、[首轮 001](../story-01-to-04/2026-10-28-001-application-preflight-and-saved-inputs.md)。

## 原因与边界

首轮已取消原始路径门禁，但真正缺少保存来源内容时仍进入整工程只读恢复，缺少从当前操作选文件、明确对应并继续的完整交互。用户追加授权修复：合法已存译文正常打开、编辑与保存；只有实际操作需要缺失原文或模板才补输入，取消只取消当前操作。

本轮不新增校验规则，不伪造原文、来源基线或快照。主动新输入、实际输出和工程自身完整性的既有保护保持。工作区原有下载重构及首轮 S01～S04 变化不重复归入本轮；增量 001 和下载历史不改写。

## 逐文件变化

生产路径相对于 `src/transbridge/`。

- 新增 `application/projects/source_inputs.py`：`MissingProjectSourceInput` 明确来源/namespace、格式、显示名与现有诊断；缺输入是来源可用性，不等于整个工程只读。新增 `persistence/project_source_inputs.py`：逐来源只读保存成果，失败不阻止其他来源，返回可用 hydration/baseline 和缺失状态。
- 修改 `persistence/{current_project,project_lifecycle_loader,prepared_project_read}.py`：合法保存记录正常激活并传递缺输入状态；正常版本及历史恢复采用保存状态与真实可用基线的无损并集。保留所有未覆盖行、未知 namespace、原指纹和 tombstone；稀疏版本只补真实基线缺省条目，并保持正确 dirty/持久化修订。原文未知不从参考路径重新解析。
- 修改 `application/projects/{models,lifecycle,gui_facade,catalog_commands,file_source_catalog}.py` 及相关 projection：传递未就绪来源与可复制元信息，编辑、保存、切版本及项目副本继续使用权威状态；Project/Variant 结构损坏保持原拒绝边界。
- 新增 `ui/saved_translation_slots.py`，修改 `ui/{source_hydration,projection_types,entry_projection,project_view_binding,projection_consistency}.py`、工程协调器和工作台模型：保存态也能建立可编辑 slot，真实 `EntryKey.original` 保留，未知原文用显式 `original_known` 标记，展示载体不成为来源原文。hydration 和保存态统一过滤 tombstone，可见集合与权威非删除集合一致，保存不复活已删除行。
- 新增 `application/projects/{source_supplement,source_supplement_models,source_supplement_mapping}.py`：prepare/remap/commit/discard，明确来源与逐条对应预览；未对应译文在所有 Variant 中保留，已保存阶段、标签和引用不清空。部分来源仍有可用 artifact 时保留原成果及已知原文，新文件仅补缺失部分。
- 新增 `application/projects/source_input_publication.py`，修改 `source_update.py`：从原来源更新抽取并复用实际输入重新准备、目标/generation、备份和原子事务发布；没有复制第二套提交保护。真实选定文件在预览后改变时仍拒绝提交，不改变工程或译文。
- 修改 `bootstrap/{persistence,composition}.py`：注册 `project_source_supplements`，接入统一保存来源读取与生命周期。
- 新增 `application/preflight/inputs.py`、`ui/source_input_state.py`、`ui/coordinators/source_input_coordinator.py`、`ui/source_supplement_dialog.py`：操作按所需原文或模板询问来源与文件，未知格式允许明确选择，条目对应可调整；先按既有边界保存编辑，再后台准备/发布，成功后重新准备原操作。取消、关闭、目标切换不让旧操作进入新工程。
- 修改 `ui/main_window.py`、`ui/shell/tool_windows.py`、`ui/operations/{facade,categorized_upload_bridge}.py`、AI 启动/作用域/来源展示、术语窗口与迁移协调器：按原选择范围接入补输入，原文已知的选区不因同来源其他未知行而被无关阻断。批量与单项动作保留原目标范围。
- 新增 `application/projects/entry_source_text.py`，修改 `application/projects/{categorized_upload,result_sources}.py` 与 `bootstrap/history_search.py`：没有 artifact 但完整 `EntryKey.original` 实际已保存时，文字型读模型可继续消费；未知原文仍使用既有 prerequisite，绝不把保存身份推导为二进制模板。
- 修改 `application/terminology/input_capture.py`、`bootstrap/terminology_workloads.py` 和术语 presenter：显式 source_ids 作用域贯穿构建、报告、变更日志、重试与比较；传递配置副本，避免重新捕获全工程而碰到无关缺失输入。
- 新增 `tests/persistence/test_editable_missing_sources.py`、`tests/application/projects/{test_source_supplement,test_known_original_consumers}.py`、`tests/ui/test_on_demand_source_inputs.py`、`tests/ui/operations/test_file_upload_input_scope.py`；更新 loader、恢复、来源展示、术语生产链和 presenter 测试。真实 MainWindow 的空/稀疏/tombstone 三种案例曾复现失败，修正无损并集和可见集合后通过。
- 更新本 Epic plan、ADR-050、ADR-038/049、FR8.4/19.15.8/26.8/29.5 与相关索引；保留首轮实测事实，当前契约不再要求缺输入整工程只读。

## 验证证据

以下为本轮实际命令及结果。测试组有重叠，不合并为唯一测试总数；GUI 使用 offscreen，未进行手工桌面、网络或 LLM 验收。

最初工程聚合 258 通过、4 失败。两项旧 loader 自动物化期望随新契约修正并被后续 36/44 项组覆盖；另两项源码字符串合同在 HEAD 已不匹配，保留为既有失败，不宣称聚合全绿。

```powershell
uv run --no-sync pytest tests/application/projects tests/contracts/projects -q --basetemp .tmp-pi/proj --tb=short
```

既有失败为 `tests/contracts/projects/test_authoritative_mutation_paths.py::test_known_translation_adapters_commit_or_fail_closed_for_v2_projects` 和 `::test_projection_divergence_guard_compares_complete_visible_and_authoritative_sets`。主会话以 `git show HEAD:` 核对：前者期望 `translation_task_results.py` 含 `commands.merge_entry_states(`，后者期望 `project_view_binding.py` 含 `return visible != states`，HEAD 两者均不含。本轮未改这两项测试和所断言的方法，不扩大范围修改既有合同。

来源更新/补输入/loader 36 通过；补输入+历史搜索 12 通过；新增预览后真实文件变化案例后补输入模块 8 通过；预检/参考/写回边界 19 通过。

```powershell
uv run --no-sync pytest tests/application/projects/test_source_update.py tests/application/projects/test_source_update_plugin.py tests/application/projects/test_source_supplement.py tests/application/projects/test_v2_loader.py -q --basetemp .tmp-pi/pub --tb=short
uv run --no-sync pytest tests/application/projects/test_source_supplement.py tests/bootstrap/test_history_search_providers.py -q --basetemp .tmp-pi/sup4 --tb=short
uv run --no-sync pytest tests/application/projects/test_source_supplement.py -q --basetemp .tmp-pi/change --tb=short
uv run --no-sync pytest tests/application/test_preflight_boundaries.py tests/application/test_source_reference_preflight.py tests/contracts/io/test_hydrated_write_operation.py -q --basetemp .tmp-pi/pre --tb=short
```

12 项组运行时可选 faiss native import 出现 Windows 异常输出，但 pytest 最终退出码 0；不把输出隐藏成无异常环境。

最终 loader/可编辑缺输入/打开聚合 44 通过；真实 scoped 术语构建与发布链 8 通过，后者因沙箱硬链接权限在授权环境运行。

```powershell
uv run pytest tests/application/projects/test_v2_loader.py tests/persistence/test_editable_missing_sources.py tests/persistence/v2/test_project_open_records.py tests/persistence/v2/test_project_open_activation.py tests/persistence/v2/test_project_open_reuse.py --basetemp=.tmp-editable-missing-source/pytest -q
uv run pytest tests/integration/bootstrap/test_terminology_task_wiring.py --basetemp=.tmp-editable-missing-source/pytest -q
```

保存来源/恢复/真实原文/术语聚合 208 通过、5 项 `os.link` WinError 5 沙箱失败；授权复跑完整报告模块 10 通过，覆盖这 5 项环境失败。

```powershell
uv run pytest tests/persistence/v2/test_saved_project_sources.py tests/persistence/test_project_recovery.py tests/application/projects/test_known_original_consumers.py tests/application/terminology tests/bootstrap/test_terminology_saved_sources.py --basetemp=.tmp-editable-missing-source/pytest -q
uv run pytest tests/application/terminology/test_reports.py --basetemp=.tmp-editable-missing-source/pytest -q
```

UI 来源与计划组 20 通过；新补输入/上传组 31 通过；术语 presenter/窗口组 16 通过。

```powershell
uv run --no-sync pytest tests/ui/test_project_source_hydration.py tests/ui/test_v2_variant_hydration.py tests/ui/operations/test_operation_plan_presenter.py -q --basetemp .tmp-on-demand-ui/p1
uv run --no-sync pytest tests/ui/test_on_demand_source_inputs.py tests/ui/operations/test_file_upload_input_scope.py tests/ui/operations/test_categorized_upload_bridge.py tests/ui/operations/test_file_upload_production.py -q --basetemp .tmp-on-demand-ui/p7
uv run --no-sync pytest tests/ui/tools/terminology/test_presenter.py tests/ui/tools/terminology/test_window_lifecycle.py -q --basetemp .tmp-on-demand-ui/p12
```

UI/AI/迁移扩展组 62 通过、6 项 `os.link` WinError 5 沙箱失败；授权定向复跑 6 通过、16 deselected，无 skip。

```powershell
uv run --no-sync pytest tests/ui/tools/test_ai_source_selection_preparation.py tests/ui/tools/test_ai_translator_story08.py tests/ui/tools/test_mixed_preflight.py tests/ui/test_structured_migration_import.py tests/ui/operations/test_production_facade.py tests/ui/tools/terminology/test_window_lifecycle.py -q --basetemp .tmp-on-demand-ui/p5
uv run --no-sync pytest tests/ui/tools/test_ai_source_selection_preparation.py tests/ui/test_structured_migration_import.py -q -k 'empty_recovery_prepare_is_read_only_and_commit_rechecks_record or real_v2' --basetemp .tmp-on-demand-ui/p8
```

最终真实 MainWindow 稀疏/tombstone、投影保存和术语 scope 聚合 42 通过，无 skip/deselect。测试同时核对可见 key 等于权威非 tombstone key、未就绪译文保留、未知原文不伪造及编辑保存后权威状态保真。

```powershell
uv run --no-sync pytest tests/ui/test_on_demand_source_inputs.py tests/ui/test_projection_consistency_cost.py tests/ui/workbench/test_terminology_projection_consistency.py tests/ui/tools/terminology/test_presenter.py -q --basetemp .tmp-on-demand-ui-final/p6
```

最终实施 Agent 执行 `uv run ruff check src tests` 全部通过，`uv run ruff format --check src tests` 报告 2289 文件已格式化；文档收尾时 `git -c core.safecrlf=false diff --check` 退出码 0。

## 遗留与未执行项

- 上述两个 HEAD 已有源码字符串合同失败仍保留；未跑全仓 pytest，不宣称全仓测试全绿。
- 未进行真实用户工程、手工桌面、网络/LLM 或性能基准验收。
- 两实施 Agent 已核实归属并清理 `.tmp-on-demand-ui/`、`.tmp-on-demand-ui-final/`、`.tmp-editable-missing-source/`。文档落盘时主会话 `.tmp-pi/` 尚存在，待创建者清理并完成最终收尾；未读取或删除既有 `.transbridge/`。
- 首轮已说明的本机动态词库 stem→stable-key 自动迁移仍不在本轮范围，不影响正式工程术语与保存原文。
