# 助手工具注册表与最终验收

2026-11-22 · [整改方案](../../../../plans/assistant-shared-business-tools/plan.md)

启动 `register_all()` 后实得 9 个命名空间、63 个可用工具和 138 个顶层参数。其中 62 个为业务工具，`get_tool_help` 用于查阅助手目录。`propose_plan` 和回答覆盖报告由原生协议单独提供。逐项检查名称可分派、原生定义与注册参数相等、顶层参数对象拒绝额外字段、角色工具可解析，以及内置 Skill 的依赖与允许列表均包含在其角色目录内。[注册与预设装配](../../../../src/transbridge/smart_assistant/tools/__init__.py) · [原生工具](../../../../src/transbridge/smart_assistant/native_tools.py) · [内置预设](../../../../data/skills/translate_with_terms.toml)

现行目录、角色、提示和内置 Skill 已撤下 `recover_upload_associations`、`migrate_entries`、`plan_sync`、`run_postprocess`。旧计划含退役步骤时中止。文件登记直接返回工程提交结果，旧来源登记不能用成果 ID 重放。权限与撤销说明按实际操作展示，导入现有来源可记录词条变更，登记新来源应由工程操作管理。[下线验收](../story-01/2026-11-22-001-retire-assistant-tools.md) · [旧后处理计划](../story-04/2026-11-22-002-retire-standalone-postprocess.md) · [确认预览](../../../../src/transbridge/ui/tools/smart_assistant/operation_preview.py)

## 注册参数与桌面调用

以下为验收时实际加载的签名。问号表示该参数在顶层 schema 中可省略，动作特有的必填条件仍由具体操作校验。`确认` 表示注册项要求人工确认，批量停止等按输入触发的条件确认另由执行护栏处理。

### default（8）

桌面来源选择器和助手读写同一个 `AppContext`。开始页经 `project_catalog.list_projects()` 读取本地工程，共用的工作台筛选和统计服务提供其余读数。用打开两个来源的窗口核对索引、名称和当前来源，用同一临时目录的两个工程对照开始页返回值。`get_tool_help` 是助手的目录协议，直接读取注册表。[工作台与开始页对照](../../../../tests/smart_assistant/test_workbench_app_state.py) · [开始页生产查询](../../../../src/transbridge/ui/shell/start_center_directory.py) · [统计对照](../../../../tests/smart_assistant/test_workbench_statistics.py) · [目录和按名分派](../../../../tests/smart_assistant/test_tool_consolidation.py)

- `get_app_state()` · read
- `list_collections()` · read
- `switch_collection(collection_name?: str, slot_index?: int)` · write
- `get_current_filters()` · read
- `get_statistics()` · read
- `list_local_projects()` · read
- `get_current_project()` · read
- `get_tool_help(tool?: str, namespace?: str)` · read

### editor（7）

桌面表格的筛选、跨页选区、内联编辑、阶段和标签按钮使用同一套范围和工程命令。对照同一选区的完整身份、空筛选零写入、冲突时原值及实际写入的修订。[筛选、选区与标签](../story-02/2026-11-22-001-workbench-filters-selection-labels.md) · [编辑与阶段](../story-02/2026-11-22-002-workbench-entry-edits.md) · [标签目录](../story-02/2026-11-22-003-workbench-read-tools.md)

- `set_filters(stages?: list, categories?: list, labels?: list, search_query?: str, search_field?: str, clear?: bool)` · read
- `get_visible_entries(limit?: int, offset?: int)` · read
- `select_entries(entry_ids: list, action?: str)` · write
- `edit_translation(entry_id: str, new_translation: str, new_stage?: int)` · write
- `set_stage(entry_ids: list, stage: int)` · write
- `list_labels()` · read
- `manage_entry_labels(action: str, name?: str, color?: str, entry_ids?: list)` · write · 确认

### translator（13）

翻译窗口的范围、预设和 `AiTaskWorker` 同时服务翻译、混合、润色与助手预览。统计进度和控制通过任务运行时，历史报告通过 `TaskHistoryService`，合入走工程候选修订与回执。词典面板与助手按当前来源套用或存入词典，设置中心提供配置字段及术语顺序。[执行器](../story-04/2026-11-22-001-desktop-translation-execution.md) · [任务中心](../story-04/2026-11-22-005-task-center-control-results.md) · [词典按钮与工具对照](../../../../tests/smart_assistant/tools/test_dictionary_upload_authority.py) · [配置和术语](../story-04/2026-11-22-006-translation-terminology-config.md)

- `apply_dictionary()` · write
- `save_dictionary(mod_file_id?: str, scope?: str, selected_only?: bool, tags?: list)` · write
- `start_translation(profile?: str, mode?: str, entry_ids?: list)` · write · 确认
- `start_polish(profile?: str, entry_ids?: list, scope?: str, intensity?: str, strategy?: str)` · write · 确认
- `stop_task(task_id?: str, all_tasks?: bool, action?: str)` · write
- `get_task_status(task_id?: str)` · read
- `get_task_result(result_id?: str)` · read
- `merge_task_result(result_id: str, policy?: str)` · write · 确认
- `get_translation_config()` · read
- `set_translation_config(provider?: str, base_url?: str, model?: str, temperature: number, max_tokens: int, target_lang?: str)` · write
- `set_scope(stages?: list, labels?: list, categories?: list)` · write
- `get_scope_preview(profile?: str, mode?: str, entry_ids?: list, scope?: str)` · read
- `set_term_config(term_sources?: list, json_path?: str, csv_path?: str, excel_path?: str)` · write

### proofreader（2）

即时报告使用桌面候选报告投影和 `report_overview`，Excel 历史使用翻译窗口的 `list_exported_reports`。对照快照、历史候选、跨插件文件排序与上限。[即时报告对照](../../../../tests/smart_assistant/postprocess/test_report_system.py) · [历史报告](../story-04/2026-11-22-003-exported-report-history.md)

- `get_quality_report()` · read
- `list_quality_reports(limit?: int)` · read

### paratranz（9）

项目面板提供参与和全部两个目录，文件历史页固定每页 50 条。工程绑定共享成员校验，详情共用项目服务。文件比较与上传走桌面文件服务，下载走工程下载用例，触发导出及下载既有工件分别走桌面流程。以离线远端响应比对请求、传输身份、变更和回执。[文件操作](../story-01/2026-11-22-001-retire-assistant-tools.md) · [绑定与导出](../story-03/2026-11-22-001-paratranz-binding-export.md) · [目录及分页](2026-11-22-003-catalog-remote-archive.md)

- `list_projects(uid?: str)` · read
- `get_project_info(project_id?: str)` · read
- `compare_with_remote(mode: str, source_ids?: list, categorized?: bool, project_id?: int, file_keys?: list)` · read
- `upload_entries(mode: str, source_ids?: list, categorized?: bool, project_id?: int, file_keys: list)` · write · 确认
- `download_entries(project_id?: str, source_ids?: list)` · write · 确认
- `export_artifact(action?: str, output_path?: str, project_id?: str)` · write · 确认
- `get_upload_history(project_id?: str, page?: int)` · read
- `get_paratranz_project()` · read
- `switch_paratranz_project(project_id: int)` · write

### parser（5）

各文件工具调用工作台的 `prepare_open_file` 或 `prepare_translation_import`，随后经来源登记或词条变更命令提交。`replace_existing` 仅用于导入现有来源，插件空原文选项适用于登记。`import_json` 依格式决定可用操作。对照八种独立来源、九种译文文件、旧工程、重复导入、许可与取消。[格式和结果](2026-11-22-001-file-import.md) · [后台提交和显示](2026-11-22-004-source-publication-thread.md)

- `parse_esp(path: str, skip_empty?: bool, action?: str, replace_existing?: bool)` · write
- `parse_eet(path: str, action?: str, replace_existing?: bool)` · write
- `parse_xt(path: str, action?: str, replace_existing?: bool)` · write
- `parse_sst(path: str, action?: str, replace_existing?: bool)` · write
- `import_json(path: str, format?: str, action?: str, replace_existing?: bool)` · write

### writer（1）

工作台导出调用单来源或批量写回的材料、目标和发布接口。单插件另存用输出目录并保留原名，写回原文件要求显式选择和覆盖。对照成品字节、逐来源诊断、备份、实际发布文件及取消回执。[导出生产路径及对照](2026-11-22-002-file-writeback.md)

- `write_back(target: str, path?: str, output_dir?: str, destination_mode?: str, source_keys?: list, input_plugin?: str, input_plugins?: object, pack_bsa?: bool, overwrite?: bool)` · admin · 确认

### archive（4）

桌面 FOMOD 的解包、差异、过滤和发布阶段调用归档提取器、目录比较器、XML 资源发现、过滤器和 ZIP/7z 写入器。测试直接读取成品成员，并用平台正式回读器重开。[归档和目录](2026-11-22-003-catalog-remote-archive.md) · [XML 引用保留](2026-11-22-002-file-writeback.md)

- `extract_archive(archive_path: str, dest_dir: str)` · write
- `pack_archive(src_dir: str, archive_path: str, fmt?: str)` · write
- `diff_directories(old_dir: str, new_dir: str, skip_hash_exts?: list)` · read
- `filter_files(files: list, fomod_dir?: str, rules_json?: str)` · read

### terminology_sync（14）

工作台同步页通过 `TerminologySyncPresenter` 调用同一个工程术语服务的预检、计划、分页、映射、授权、执行、状态与结果、重试、核对及入站审核接口。预览中的修订由服务读取。更换映射授权、签发远端计划授权、提交入站草稿均需人工确认。真实 GUI 装配对照两个确认路径和写入后的 SQLite 草稿。[配置及双入口验证](../story-04/2026-11-22-006-translation-terminology-config.md) · [桌面调用](../../../../src/transbridge/ui/tools/terminology/sync_presenter.py) · [服务契约](../../../../tests/contracts/terminology_sync/)

- `terminology_sync_preflight(mode: str)` · read
- `terminology_sync_plan(mode: str)` · read
- `terminology_sync_activate_mapping(mode: str, replacement_token?: object)` · write
- `terminology_sync_mapping_confirmation(mode: str)` · write · 确认
- `terminology_sync_plan_page(plan_id: str, plan_hash: str, cursor?: str)` · read
- `terminology_sync_authorize(plan_id: str, plan_hash: str)` · write · 确认
- `terminology_sync_execute(plan_id: str, plan_hash: str, confirmation_token?: object)` · write
- `terminology_sync_status(job: object)` · read
- `terminology_sync_retry(job: object)` · write
- `terminology_sync_reconcile(job: object)` · write
- `terminology_sync_inbound()` · read
- `terminology_sync_inbound_get(change_set_id: str, offset?: int)` · read
- `terminology_sync_inbound_preview(change_set_id: str, choices: list)` · read
- `terminology_sync_inbound_commit(proposal_digest: str)` · write · 确认

## 交叉验证

术语同步的计划页每页 100 项，入站审核页每页 50 项。助手按桌面规则处理超出范围的起始位置。重试与核对接收已返回的任务引用，由服务校验归属并读取该任务的实际结果。旧请求传入自定页长或重试令牌时由参数校验拒绝。[桌面与助手的同输入对照](../../../../tests/contracts/terminology_sync/test_desktop_assistant_parity.py)

工作台「存为词典」和助手都在后台读取目标词典的最新内容，合并本轮选中条目，并在提交前检查取消。两个入口交替保存时，同名词典保留两次更新，不同名词典分别发布。工作台打开多个同名来源时，按唯一来源键或列表索引切换。[词典发布命令](../../../../src/transbridge/translation_memory/publication.py) · [交替保存及取消](../../../../tests/smart_assistant/tools/test_dictionary_upload_authority.py) · [来源切换](../../../../tests/smart_assistant/test_workbench_app_state.py)

任务成果详情与桌面复核窗口读取助手自动合入及桌面人工处理两处的覆盖前证据，按实际发布顺序排列。报告摘要与应用状态在同一次查询中核对各次提交的回执。测试在相同任务上先保留当前值，再采用候选，验证两端均能读取实际处理记录。[证据读取](../../../../src/transbridge/application/translation/task_merge_evidence.py) · [成果回归](../../../../tests/smart_assistant/tools/test_translation_task_lifecycle.py)

助手操作均以临时工程、离线服务响应和本地成品进行验证。各批记录列有完整命令及同输入断言。

## 扩大回归

以下命令使用仓库锁定的环境运行。工程存储回归曾有五个断言要求数据库完全没有插件路径，这与已发布的来源引用合同冲突。现在分别断言登记来源的路径正确、插件正文未复制、仅作为译文输入的文件路径未保留。[来源引用合同](../../../requirements/project-storage-redesign.md) · [工程存储回归](../../../../tests/application/projects/test_source_artifact_publication_cost.py)

```powershell
uv run --offline --no-sync --no-cache pytest tests/application/assistant_requests tests/application/translation tests/application/projects -q -m 'not llm' -rs
uv run --offline --no-sync --no-cache pytest tests/ui/tools/smart_assistant -q
uv run --offline --no-sync --no-cache pytest tests/contracts/terminology_sync tests/application/terminology_sync tests/integration/terminology_sync tests/contracts/security tests/integration/gui/test_assistant_terminology_sync.py tests/ui/tools/terminology/test_sync_presenter.py -q -rs
uv run --offline --no-sync --no-cache pytest tests/ui/test_task_center.py tests/integration/gui/test_task_storage_history.py tests/smart_assistant/postprocess/test_report_system.py tests/ui/tools/test_ai_result_lifecycle.py -q
```

上述四组分别通过 970、507、132 和 33 项。工程与安全边界各有一项需要 Windows 符号链接权限。桌面助手有 12 项需要原生 HWND，在 offscreen 平台跳过。

最终运行 `uv run --offline --no-sync --no-cache pytest tests/smart_assistant -q --tb=short --disable-warnings`，1,269 项全部通过。另以同输入复验文件登记、写回发布、归档与 FOMOD 回读，74 项通过。词典与选区的组合回归 114 项通过，原生任务控制的组合回归 130 项通过。注册表再次加载并对照本文全部 63 个签名，7 个角色的预设均可解析。`git diff --check` 通过。

全库 Ruff 在本方案的代码完成时通过。交付前使用 `--no-cache` 重跑时，工作区另有正在修改的 DSD 定位和交换保真代码，涉及 `dsd_projection.py`、`json_export.py`、`migration_import.py`、`dsd_entries.py`、`test_dsd_saved_locators.py` 和 `test_exchange_fidelity.py`。该次检查报告 4 处行长、6 个文件待格式化。保留这些并行修改后，对其余 281 个有变更的 Python 文件重新运行 Ruff，两项均通过。
