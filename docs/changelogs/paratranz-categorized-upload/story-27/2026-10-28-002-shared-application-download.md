# S27 追补：GUI 与 AI 助手复用应用层下载用例

日期：2026-10-28（Asia/Shanghai）。Epic：paratranz-categorized-upload；Story：S27。
关联：[计划](../../../../plans/paratranz-categorized-upload/plan.md)、[需求](../../../requirements.md)、[ADR-047](../../../adr/047-opt-in-categorized-project-upload.md)、[ADR-049](../../../adr/049-task-result-lifecycle.md)、[前轮目录下载与清理记录](2026-10-28-001-plugin-directory-download-cleanup.md)。

## 原因与边界

前轮只统一了 GUI 的插件目录读取和临时清理。只读审查发现，助手仍调用项目词条分页和
EntryTaskResults(kind=download)，没有获得目录全量读取或结束清理行为。用户明确指出 GUI 与助手应复用代码，
并授权子 Agent 协同补齐。

本次将完整工程下载编排下沉无 Qt 应用层，GUI 和助手只提供各自的交互、执行许可及结果转换。
不复制另一套下载实现，不新增索引、缓存、校验、依赖或工程 schema。
本记录只涵盖前轮记录之后的应用层抽取、助手接入和对应回归，不将前轮已记录的删除操作重复算作本次新增工作。

## 逐文件变化

- 新增 `application/sync/project_download.py`：从 GUI 会话抽取 `ProjectDownloadSession`，统一来源选择、
  插件目录文件读取、本地匹配、既有提交与本轮清理；传输、权限读取及执行器由入口注入。
- 新增 `application/sync/download_context.py`、`download_run.py`：显式远端目标适配和自动接续流程，
  两个入口使用相同实现，不在应用层创建 Qt 对象。
- 新增 `application/sync/download_merge.py`：统一译文/阶段合入、复核投影及正式版本保存，
  沿用既有内容匹配和原子命令；账号变化与结构变化提示不再声称数据可跨轮恢复。
- 新增 `application/sync/review_view.py`、`download_view.py`：无 Qt 的公共 DTO 与复核投影。
- 修改 `ui/operations/batch_download_session.py`：仅保留 `ProjectDownloadSession` 的 GUI 依赖注入及窗口组合，
  注入既有 TaskRuntime 执行器，不再保存下载业务实现。
- 修改 GUI 的 `batch_download_results.py`、`batch_download_view.py`、`download_context.py`、
  `download_run.py`、`sync_review_view.py`、`sync_review_projection.py` 为明确同名 re-export。
  兼容原公开导入，所有调用实际使用同一类/函数；不保留第二套规则。
- 修改 `infra/__init__.py`：MarkdownRenderer 同名惰性导出并保留 TYPE_CHECKING 支持。
  根因是应用层导入公共翻译合入模块时，原基础包会预先加载 Qt 渲染器；只延迟这个 GUI 能力，
  不改变其他基础包导出或实际渲染。
- 新增 `smart_assistant/tools/_project_download.py`：助手薄适配器直接调用公共用例与公共 run_download，
  默认当前来源，支持显式 source_ids。下载不再走项目 strings 分页，没有默认 2000 条截断，
  直接合入并保存已有译文，只返回统计及未完成说明，不产生 result_id 或返回完整下载载荷。
- 修改 `smart_assistant/tools/tool_paratranz.py`：download_entries 委托新适配器及更新参数/工具说明；
  既有 write、长任务及确认权限保持。删除下载的 EntryTaskResults 持久成果路径，其他工具的存储未改。
- 修改 `smart_assistant/prompts.py`、`tools/tool_translator.py` 的相关说明：下载直接完成，
  不再要求随后查询/合入下载成果；AI、字典与导入的独立成果处理规则继续有效。

助手的合入和写盘在后台完成，最终提交复用现有 ExecutionContext 请求许可。
生命周期锁和提交许可均释放后，只将界面交付确认派发到 Qt，推进原请求捕获的修订和 collection。
不跨线程转移已持有的生命周期锁，不把网络或写盘整体放到 Qt。
迟到取消、交付确认异常或迟到来源切换不反转已保存事实，也不把请求确认到其他来源。
无论成功、失败、取消或有未处理分歧，助手 finally 都清理本轮会话。

## 测试与结构验证

- 新增 `tests/contracts/paratranz/test_download_application_views.py`：新进程导入六个应用模块，
  不加载 transbridge.ui 或 PyQt6；旧 GUI 导入指向同一 DTO/函数，MarkdownRenderer 显式导入仍兼容。
- 新增 `tests/contracts/paratranz/test_project_download_use_case.py`：应用用例无需 GUI 执行器，
  以真实工程命令完成读取、合入、磁盘保存及清理。
- 新增 `tests/smart_assistant/tools/test_project_download.py` 的 15 项实际工程测试：
  名称不同的任务目录、2101 条内容、后续重新读取、显式/默认来源、取消清理、版本切换、
  后台唯一一次提交许可、Qt 交付确认、迟到取消/来源切换、确认失败，以及并发保留当前的真实统计。
- 修改 `tests/smart_assistant/tools/test_entry_result_lifecycle.py`、`test_paratranz_tools.py`：
  移除已取消的助手下载持久恢复合同；字典、导入及其他 AI 成果回归保持。
- 修改 `tests/contracts/projects/test_authoritative_mutation_paths.py` 的下载合入路径，
  `test_batch_download_session.py`、`test_batch_download_feedback.py` 的 monkeypatch 落点跟随应用层移动。
- 修改 `test_batch_download_dialog.py` 中真实线程验证：有界等待 GUI 定时器事件，
  替代固定 30ms 调度假设；仍验证事件可处理、worker 尚在运行和全部保存工作不在 GUI 线程。

## 实际验证命令与结果

```powershell
uv run pytest tests/paratranz tests/contracts/paratranz tests/ui/operations tests/persistence/v2/test_legacy_project_task_assets.py tests/smart_assistant/tools/test_project_download.py tests/smart_assistant/tools/test_entry_result_lifecycle.py tests/smart_assistant/tools/test_paratranz_tools.py tests/smart_assistant/tools/test_translation_task_lifecycle.py tests/smart_assistant/tools/test_postprocess_task_lifecycle.py tests/smart_assistant/test_request_execution.py tests/application/translation/test_result_merge.py tests/application/translation/test_task_recovery.py tests/application/translation/test_task_recovery_results.py tests/config/test_task_result_preferences.py tests/smart_assistant/test_markdown_renderer.py tests/ui/foundation/test_theme_adapters.py --basetemp=D:/MyCode/TransBridge/.q28-f6a2 --deselect=tests/paratranz/test_entry_upload_order.py::test_assistant_selected_upload_retains_full_collection_order_and_local_context --deselect=tests/paratranz/test_entry_upload_order.py::test_assistant_upload_preserves_sparse_plugin_order_and_free_text --deselect=tests/paratranz/test_entry_upsert.py::test_assistant_upload_reuses_only_the_target_projects_known_identity -q --tb=short
uv run pytest tests/smart_assistant/tools/test_project_download.py tests/smart_assistant/tools/test_entry_result_lifecycle.py tests/smart_assistant/tools/test_paratranz_tools.py --basetemp=D:/MyCode/TransBridge/.a27f-812c -q --tb=short
uv run ruff check src tests
uv run ruff format --check src tests
git diff --check
```

联合回归：927 passed、1 skipped、4 deselected，58.00 秒。4 项排除沿用前轮已经以 HEAD 复现的助手上传基线失败。
联合集合收集后补入迟到来源切换用例，助手最终聚焦集合 53 passed，其中 15 项为新共享下载回归；
它覆盖新增用例及最终助手适配器代码，不把重复集合相加冒充新的独立用例数。
全库 Ruff 检查通过，2258 个文件格式通过；差异空白检查通过。

首轮联合集合有两项未通过：诊断 monkeypatch 仍指向 GUI 模块，及真实 worker 测试假定 30ms 内必有定时器回调。
修正测试落点和有界事件等待后，完整联合集合通过，未放宽生产下载或并发处理规则。
独立子 Agent 复核了应用层/GUI/助手的依赖方向、执行器注入和清理生命周期，无剩余功能性问题；
其中指出的一处“成果保留”旧文案已在规范源修正。

## 兼容与清理

本次改变助手 download_entries 的返回及执行语义：现在直接保存，返回统计，不再提供下载 result_id、
原始 entries 或随后 merge_task_result 的下载恢复路径。工具描述和默认提示已同步。
字典、导入、AI 翻译/润色成果仍使用各自原有独立存储，不因下载清理而删除。
保留旧 GUI 导入兼容；没有新持久格式、依赖、索引或校验。

两轮联合测试和子 Agent 独有测试目录已核实归属后清理。前轮用户真实插件的只读云端验证见关联记录；
本轮以真实 AppContext、正式工程保存和受控文件 API 复验共享调用，没有再次操作真实云端。
未构建安装包、未运行全库 pytest、未提交 Git。原有 `.transbridge/` 未改。
本次目标无未完成实现项。
