# S01～S04：应用层预检与保存输入独立使用

日期：2026-10-28（Asia/Shanghai）。Epic：application-preflight；Story：S01～S04。
关联：[计划](../../../../plans/application-preflight/plan.md)、[需求](../../../requirements.md)、[ADR-050](../../../adr/050-application-preflight-and-optional-source-reference.md)。

## 目的与归属

将既有 UI 预检迁移或委托到无 Qt 应用层；原始来源路径仅作为可空参考，已保存内容不依赖外部原文件才能读取。只改变责任归属和明确授权的来源参考语义，不增加检查规则或通用校验框架。

工作区已有未提交的 ParaTranz 下载重构。本记录排除 `application/sync/` 下载用例、下载 GUI/助手、下载测试及此前 S27 文档变化；共享 `requirements.md`、ADR-049 和索引仅记录本轮来源/预检段落，不把整个工作区 diff 归属本轮。

## 逐文件变化

以下生产路径均相对于 `src/transbridge/`。

- 新增 `application/preflight/operation.py`：承接原 UI 的 `OperationKind`、检查/预检 DTO、facts 和既有检查服务，保留 code、状态、repair intent 与执行顺序。新增 `ai.py`：迁移 AI 模式、配置、依赖、范围等既有预检，取消已保存输入对原始 `esp_path` 的前置依赖。新增 `sources.py`：参考只做存在性归一化，确认缺失置空，存在性未知保留；包 `__init__.py` 提供入口。
- 修改 `ui/operations/{mappers,plan_view,preflight_view}.py` 和 `ui/tools/ai_translator/run_spec.py`：重导出应用契约、委托检查，保留展示与运行输入装配。修改 `run_request_preparation.py`、`task_launch.py`、`ai_translator_window.py`：使用已安装来源身份和内容准备运行，移除参考非空门禁。
- 修改 `application/projects/{source_registry,source_content,provisioning_models,result_sources}.py` 与 `persistence/v2/schema.py`：location 可缺失、为空或重复，不参与来源唯一性。新增 `application/projects/installed_source_request.py`：读取已安装内容的请求不要求导入文件名。
- 修改 `application/projects/source_commands.py`：新加入来源 ID 基于已捕获内容 namespace 摘要，移除相同参考地址拒绝，保留内容重复保护；已保存来源 ID 不迁移。修改 `source_update.py`：更新目标不因参考地址与其他来源相同而拒绝；旧内容只读保存成果，缺成果沿用未验证旧内容的译文保留警告，不解析旧参考。
- 修改 `persistence/{current_project,saved_project_sources,source_artifact_codec}.py`：启动/打开只归一化参考，成果 hydration 携带稳定来源身份与可空参考，成果内容身份保持；没有保存内容时进入原恢复视图，不自动读外部 hash 或重新解析。
- 修改 `persistence/{project_archive,project_archive_files,source_artifact_archive}.py`：包内内容用稳定身份和内部引用定位，复制工程不重写参考为新内部路径；成果恢复不依赖原文件名。新增 `archive_source_inputs.py`：旧 portable 包自带 sources 在导入事务中解析并保存成果，使用包内明确输入，沿用原有摘要/身份核验。
- 修改 `persistence/legacy_project_archive.py`：纯 legacy JSON 包只转换已存记录，移除外部来源解析、SST magic 读取及借外部文件补齐稀疏历史；已有译文和身份保留，缺真实内容走恢复视图。
- 修改 `ui/{source_hydration,project_view_binding,project_recovery}.py`、`ui/coordinators/{project_coordinator,parse_coordinator}.py`：slot 按正式 source_id 绑定，解析登记后使用返回 ID，删除/重开/重试保持同一身份，参考只作可选展示。修改 `ui/workbench/{parse_presenter,step1}.py` 与 `ui/shell/drop_controller.py`：删除路径重复门禁，显式新输入交由既有来源命令处理。
- 修改 `application/io/{operation_write,plugin_transfer}.py`、`application/projects/categorized_upload.py` 及 `ui/operations/{write_batch,write_dialog,write_feature,write_plan}.py`：使用保存输入和展示名称，输出位置独立选择；不把可空参考当默认覆盖目标，保持实际目标、覆盖与原子发布保护。
- 修改 `bootstrap/history_search.py`：从保存成果恢复完整 EntryKey 原文，缺成果沿用既有来源诊断；只取权威基线，避免 folded 译文来源覆盖原文。
- 新增 `bootstrap/terminology_sources.py`，修改 `bootstrap/{terminology,terminology_build,terminology_storage,terminology_workloads}.py`：注入保存成果 leases；完整输入仅随当前请求传递，长期 ProductionState 只保留轻量证据。
- 修改 `application/terminology/{input_capture,build,corpus,runtime,workloads}.py`：SourceLease 携带保存 ParseResult，解析器直接消费；BuildWorkloadRequest 带当前完整输入，运行记录移除其强引用；fingerprint 不变。folded 迁移来源保留自身 fingerprint、fragment 和关系证据，避免误报 PARTIAL。
- 修改 `smart_assistant/tools/tool_proofreader.py`：历史报告不因 esp_path 为空直接返回，优先保存 snapshot 的资源名定位旧报告目录。新增 `ui/dynamic_terminology_source.py`，修改 `ui/tools/ai_translator/config_dialogs.py`、`ui/tools/terminology_profiles/source_catalog.py` 等入口：复用原 `source_terminology_path(reference, stable_key)` 缓存定位，保存名称仅作显示。
- 修改 `scripts/benchmark_project_open.py`：合成 fixture 正式保存成果，说明缺保存内容进入恢复视图；本轮只验证脚本测试，没有执行实际性能基准。
- 新增 `tests/application/{test_preflight_boundaries,test_source_reference_preflight}.py`、`tests/bootstrap/test_terminology_saved_sources.py`、`tests/contracts/terminology/test_build_input_lifetime.py`、`tests/ui/tools/{test_dynamic_terminology_sources,test_run_input_identity}.py`，覆盖无 UI 应用契约、存在性策略、保存输入、请求结束释放及稳定来源身份。
- 更新来源命令/更新、schema/归档/打开、历史搜索、术语、写回、AI、解析、拖放和工程创建测试。原路径 slot-key 断言改为正式 source_id，同时单独断言参考保留；没有删掉其原有生命周期验收目的。
- 文档原位更新 FR8.3/8.4、FR19.10/19.14.4/19.15.8、FR26.8、FR29.5/29.6，ADR-038/049 对齐当前合同并明确局部被 ADR-050 取代；新增 plan、ADR-050 与本记录，索引只加相关条目。

## 验证证据

以下来自主会话及两个实施 Agent 的实际命令结果。测试组存在重叠，不相加为唯一通过总数。Qt 组使用 offscreen；实施 Agent 使用工作区 UV_CACHE_DIR 与 TEMP/TMP。

工程/来源聚合：382 通过、1 项 SQLite 超长临时路径失败；同一失败单测改为短 `--basetemp .tmp-pfr` 后通过。该失败属于路径环境，不宣称原命令全绿。

```powershell
uv run --no-sync pytest tests/persistence/v2/test_saved_project_sources.py tests/persistence/v2/test_source_artifact_archive.py tests/persistence/v2/test_project_archive.py tests/persistence/v2/test_legacy_project_archive.py tests/persistence/v2/test_legacy_project_task_assets.py tests/application/projects tests/ui/test_project_source_hydration.py tests/ui/test_v2_variant_hydration.py tests/ui/test_project_recovery.py tests/application/test_source_reference_preflight.py tests/application/test_preflight_boundaries.py -q --basetemp .tmp-application-preflight/final-project
```

消费者聚合 249 通过；随后含完整输入释放的术语最终聚合 210 通过。

```powershell
uv run --no-sync pytest tests/application/terminology tests/bootstrap/test_history_search_providers.py tests/bootstrap/test_terminology_saved_sources.py tests/integration/bootstrap/test_terminology_task_wiring.py tests/smart_assistant/tools/test_run_postprocess.py tests/smart_assistant/postprocess/test_report_system.py tests/ui/test_parse_hydration_authority.py tests/ui/test_parse_migrations.py tests/ui/test_project_source_hydration.py -q --basetemp .tmp-application-preflight/consumers
uv run pytest tests/bootstrap/test_terminology_saved_sources.py tests/contracts/terminology tests/integration/bootstrap/test_terminology_task_wiring.py tests/integration/bootstrap/test_terminology_build_review.py tests/application/terminology --basetemp=.tmp-saved-source-memory/pytest -q
```

UI/IO 聚合第一次 214 通过、11 项仍使用旧路径 key 的断言失败；改为稳定 ID 并保留参考断言后，三个创建模块复验 17 通过、3 项拖放旧断言失败。后续最终 38 项组覆盖已修复拖放/创建，全部通过，不能将第一次描述为单次 225 项通过。

```powershell
$taskTests = @(rg --files tests/ui | Where-Object { $_ -match 'test_(project_(view_installation|open_operation|open_guards|labels|editing_boundary|creation_transition|creation_reuse|creation_deadlock)|v2_project_transfer|source_update|background_gui_operations|dialogue_authority|dialogue_lazy_index)\.py$' })
uv run --no-sync pytest @taskTests tests/contracts/io/test_hydrated_write_operation.py tests/ui/operations/test_write_save_as.py tests/ui/operations/test_write_execution_feedback.py tests/ui/operations/test_strict_write_routes.py tests/ui/operations/test_plugin_transfer_ui.py tests/ui/operations/test_operation_plan_presenter.py tests/integration/publish/test_plugin_transfer.py -q --basetemp .tmp-pfr2 --tb=short
uv run --no-sync pytest tests/ui/test_project_creation_transition.py tests/ui/test_project_creation_reuse.py tests/ui/test_project_creation_deadlock.py -q --basetemp .tmp-pfr3 --tb=short
uv run --no-sync pytest tests/application/projects/test_source_commands.py tests/ui/tools/test_dynamic_terminology_sources.py tests/ui/tools/terminology/test_scheme_sources.py tests/ui/test_plugin_drop_workflow.py tests/ui/test_project_creation_reuse.py -q --basetemp=.tmp-preflight-ui/final --tb=short --maxfail=2
```

来源更新最终 25 通过；来源移除/解析/成果发布 54 通过；schema 组 43 通过，追加可选/共享 reference 持久化案例后该模块 7 通过。

```powershell
uv run pytest tests/application/projects/test_source_update.py tests/application/projects/test_source_update_plugin.py --basetemp=.tmp-source-update-reference/pytest -q
uv run --no-sync pytest tests/application/projects/test_source_artifact_publication_cost.py tests/application/projects/test_source_removal.py tests/ui/test_parse_hydration_authority.py tests/ui/test_parse_migrations.py -q --basetemp=.tmp-preflight-ui/binding --tb=short --maxfail=2
uv run --no-sync pytest tests/persistence/v2/test_project_source_registry_migration.py tests/persistence/v2/test_schema_execution.py -q --basetemp .tmp-application-preflight/schema --tb=short
uv run --no-sync pytest tests/persistence/v2/test_project_source_registry_migration.py -q --basetemp .tmp-application-preflight/optional-schema --tb=short
```

AI 运行组 41 通过、2 项 `os.link` 的 WinError 5 沙箱权限失败、1 项 deselected；授权在 sandbox 外定向复跑参数化用例共 3 项通过，覆盖两个失败与被排除项。

```powershell
uv run --no-sync pytest tests/ui/tools/test_unified_task_runtime.py tests/ui/tools/test_ai_source_selection_preparation.py -q -k 'not test_empty_recovery_finishes_before_any_preflight_or_run_creation' --basetemp=.tmp-preflight-ui/runtime --tb=short --maxfail=2
uv run --no-sync pytest tests/ui/tools/test_ai_source_selection_preparation.py::test_empty_recovery_prepare_is_read_only_and_commit_rechecks_record tests/ui/tools/test_unified_task_runtime.py::test_empty_recovery_finishes_before_any_preflight_or_run_creation -q --basetemp=.tmp-preflight-ui/recovery --tb=short
```

另有 `tests/persistence/v2/test_project_open_reuse.py` 11 项、`tests/scripts/test_benchmark_project_open.py` 21 项通过；只证明打开复用和基准脚本兼容，不构成实际性能数据。

最终静态检查：

```powershell
uv run --no-sync ruff check src tests
uv run --no-sync ruff format --check src tests
git -c core.safecrlf=false diff --check
```

Ruff 全部通过；格式检查 2272 个文件已经格式化；diff 检查退出码 0、无输出。

## 兼容边界与未执行项

纯 legacy 记录缺真实内容时仅保留译文与恢复视图，不声称完整打开；主动导入 portable 包内 sources 可建立正式成果。原始路径清空不删除保存成果或已有 ID。实际输出位置独立选择，原有目标保护保持。

动态词库继续使用既有 reference-or-stable-key 本机缓存策略；清空 reference 后不自动将旧 stem 缓存迁移到 stable-key。此缓存迁移未实施，不影响工程正式术语和保存原文，也不承诺旧本机缓存随工程包迁移。

未执行全仓 pytest、真实用户工程验收、网络/LLM 调用、手工桌面验收或实际性能基准；不据本地自动化宣称这些验收已通过。临时目录由各创建者和主会话确认归属后清理，不删除既有 `.transbridge/` 用户数据。
