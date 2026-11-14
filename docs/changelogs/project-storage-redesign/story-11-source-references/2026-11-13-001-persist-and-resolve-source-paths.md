# S11：来源路径持久化与导出按需定位

- 日期：2026-11-13。
- Epic/Story：project-storage-redesign / S11；关联 [FR32](../../../requirements/project-storage-redesign.md)、[ADR-054](../../../adr/054-project-storage-and-independent-tasks.md)、[计划](../../../../plans/project-storage-redesign/plan.md)。
- 授权：用户要求先修正路径机制，并允许 GPT-6.1 Sol/high 子 Agent 编码和扫库；主会话整合、审查及验证。
- 归属：以任务开始时的工作树差异为基线，只记录本轮增量；不包含已有术语方案、定位缓存、模型配置或此前批量导出崩溃修复。未提交 Git，未写入用户真实工程。

## 行为与兼容

建项、添加和更新来源保存来源路径引用。重开项目、快照及便携包保留引用，但不保存插件本体、解析树或完整写回材料。路径只是输入提示，不成为输出默认位置或助手的目录授权。普通打开、编辑、保存及翻译数据使用不探测原文件。

插件导出优先验证现存引用；缺失、不匹配或缺少必要 Strings 时才补充输入。GUI 手选一个插件后，仅在该目录匹配本批尚未解决来源的完整文件名（不区分大小写），来源名和候选均唯一才尝试绑定，仍执行现有严格内容校验。取消、关窗、切换工程/版本或修订变化后不继续旧流程。助手保留显式输入优先、授权和预检边界。

验证成功后使用来源身份、指纹、旧路径和修订比较回填引用，不推进翻译修订。未保存的新增来源更新权威工作副本，随正常保存落盘，不隐式保存译文；工程元数据已先保存时也不能漏存新增来源。普通保存进行中或当前版本存在未确认保存时拒绝待保存来源回填。

现有 v1 SQLite 工程无引用表时仍可读取，首次补充时按需建表；缺少历史路径的工程无法凭空恢复路径，首次导出仍需定位。旧 JSON/artifact 工程不迁移。未改变多方案复制输出流程。

## 文件与改动

以下均为修改，标注“新增”的除外；同一文件中此前已有的无关改动不属于本记录。

- `src/transbridge/application/projects/storage_contracts.py`：为 SourceInfo/ExtractedSource 增加默认空引用及字符串校验，不参与内容身份。
- `src/transbridge/application/projects/source_extraction.py`：提取和 hydration 往返保留 reference_location；存储快照仍使用虚拟 URI。
- `src/transbridge/application/projects/source_content.py`：上移 registered_source_ids，供持久层和待保存来源使用同一注册身份规则。
- `src/transbridge/application/projects/source_references.py`（新增）：封装持久引用及待保存来源捕获/比较更新；保存状态由生命周期服务保护。
- `src/transbridge/application/projects/lifecycle.py`：增加两个加锁桥接入口；拒绝保存中、当前版本待确认保存及失效捕获。
- `src/transbridge/application/projects/durable_variant.py`：提交前在锁内读取最新待保存来源，避免旧捕获覆盖刚修复的路径。
- `src/transbridge/persistence/project_store/source_references.py`（新增）：可选引用表、规范文本读写及包校验；不访问目标文件。
- `src/transbridge/persistence/project_store/connection.py`：格式校验允许精确的可选引用表结构，保留原有缓存扩展规则。
- `src/transbridge/persistence/project_store/reads.py`：可选 JOIN 读取路径，无表返回空；保留 native_fields 开关。
- `src/transbridge/persistence/project_store/repository.py`：sources 透传 native_fields，纯引用查询不解码大份原生字段。
- `src/transbridge/persistence/project_store/writes.py`：插入来源时保存引用。
- `src/transbridge/persistence/project_store/records.py`：恢复 hydration 引用；增加带身份、指纹、旧值及可选读令牌的更新入口，保持 ProjectDto 的来源 location 不变。
- `src/transbridge/persistence/project_store/paged_reads.py`：注册身份函数改用应用层共享入口。
- `src/transbridge/persistence/project_store/lifecycle.py`：即使工程元数据修订已持久化，存在 source_additions 时仍保存来源。
- `src/transbridge/persistence/project_store/archive_validation.py`：验证引用文本和关联，不探测文件。现有数据库备份/打包机制自然保留该表，无新增插件归档流程。
- `src/transbridge/bootstrap/composition.py`：注册引用服务；`src/transbridge/ui/app.py`、`src/transbridge/ui/context.py`：透传该服务。
- `src/transbridge/ui/projection_types.py`：CollectionSlot 增加独立 reference_path。
- `src/transbridge/ui/project_rows.py`：分页来源投影携带引用，不改已有分页逻辑。
- `src/transbridge/ui/source_hydration.py`：恢复独立输入引用，防止虚拟工程来源获得隐式输出目标；空注册 location 不覆盖有效引用。
- `src/transbridge/ui/coordinators/parse_coordinator.py`、`open_file_coordinator.py`：新增来源的 hydration 保留原始引用。
- `src/transbridge/ui/coordinators/write_input_coordinator.py`：后台读取、验证和回填引用；只对未解决来源询问，手选后尝试同目录匹配，核验过期状态。
- `src/transbridge/ui/operations/facade.py`：仅在同次操作的覆盖确认重试复用冻结输入，独立导出重新验证；`coordinator.py`：传递对话框存活判断。
- `src/transbridge/smart_assistant/tools/write_back_preparation.py`：读取持久及待保存来源引用、授权自动输入和配套资源、预检成功后回填；捕获范围仍限定原来源。`tool_writer.py`：更新工具说明。
- `tests/persistence/project_store/test_source_references.py`（新增）：无表 v1 兼容、比较更新与版本保护、旧包读取。
- `tests/persistence/project_store/test_source_extraction.py`、`test_archive.py`：来源提取、引用保留及无插件包验证。
- `tests/application/projects/test_provisioning.py`、`test_source_update.py`：来源路径捕获/更新及原插件不持久化。
- `tests/integration/bootstrap/test_project_storage_composition.py`：真实 composition 下重开、引用更新后建版本、来源替换/恢复和包复制保留引用。
- `tests/integration/bootstrap/test_project_source_reference_lifecycle.py`（新增）：删除源插件后禁止访问旧/新引用目标，仍能编辑、保存和重开。
- `tests/integration/bootstrap/test_pending_source_references.py`（新增）：批量待保存路径修复、保存竞争、自动保存、跨版本确认及工程元数据先保存。
- `tests/ui/test_project_source_hydration.py`：路径不变成默认输出，空注册 location 保留输入引用。
- `tests/ui/operations/test_write_source_references.py`（新增）：真实插件/SQLite、后台任务回调、自动定位、同目录补齐、失效回调与待保存来源。
- `tests/smart_assistant/tools/test_safe_write_back.py`：自动引用、显式输入、错误内容、覆盖预检、授权、保存与待保存/元数据先保存情形。
- `docs/requirements/project-storage-redesign.md`、`docs/requirements.md`：纠正“不存插件等于不存路径”的错误边界。
- `docs/adr/054-project-storage-and-independent-tasks.md`：可选表、并发和待保存来源契约。
- `plans/project-storage-redesign/plan.md`、`stories/story-02-source-extraction.md`：修正路径约束并记录 S11；`docs/INDEX.md`、`plans/INDEX.md`、`docs/changelogs/INDEX.md` 只更新相关条目。

职责审查：待保存引用逻辑独立为 source_references 模块；已有大型生命周期和 UI 装配模块仅增加锁桥接/依赖接线，不承载新增定位算法。未引入依赖、新哈希或插件缓存。

## 验证

Windows / Python 3.12.12，沿用 uv 环境与锁文件；Qt 使用 `QT_QPA_PLATFORM=offscreen`。UV_CACHE_DIR、TEMP、TMP 和 pytest basetemp 指向本任务独有 `.tmp-path-reference`。SQLite 原子发布所需 Windows 硬链接在获准的沙箱外测试进程执行。以下测试均为隔离合成数据，不访问真实 LLM/API，不改用户文件。

最后边界验证：

```powershell
uv run --offline --no-sync pytest tests/ui/operations/test_write_source_references.py tests/smart_assistant/tools/test_safe_write_back.py tests/integration/bootstrap/test_pending_source_references.py -q -p no:cacheprovider --basetemp=.tmp-path-reference/pending-final
```

结果：40 passed，3 warnings，3.97 秒，退出码 0。

最终扩大回归：

```powershell
uv run --offline --no-sync pytest tests/persistence/project_store tests/application/projects/test_provisioning.py tests/application/projects/test_source_update.py tests/application/projects/test_source_update_plugin.py tests/application/projects/test_durable_variant.py tests/ui/operations tests/ui/test_project_source_hydration.py tests/ui/test_parse_hydration_authority.py tests/ui/test_open_file_workflow.py tests/ui/test_plugin_drop_workflow.py tests/integration/bootstrap/test_composition.py tests/integration/bootstrap/test_project_storage_composition.py tests/integration/bootstrap/test_project_source_reference_lifecycle.py tests/integration/bootstrap/test_pending_source_references.py tests/integration/gui/test_ui_foundation_startup.py tests/integration/gui/test_project_paged_workbench.py tests/smart_assistant/tools/test_safe_write_back.py tests/smart_assistant/tools/test_write_back_source_scope.py tests/smart_assistant/tools/test_write_back_publication_boundary.py tests/smart_assistant/tools/test_write_back_options.py --deselect='tests/integration/gui/test_project_paged_workbench.py::test_workbench_applies_results_to_cached_pages_and_filtered_rows[apply-and-save]' --deselect='tests/integration/gui/test_project_paged_workbench.py::test_workbench_preload_ignores_stale_rows[direct-save]' -q -p no:cacheprovider --basetemp=.tmp-path-reference/final-confirmation
uv run --offline --no-sync ruff check src tests
uv run --offline --no-sync ruff format --check src tests
```

结果：804 passed，2 deselected，7 warnings，74.71 秒，退出码 0；Ruff 全仓通过，2652 文件格式通过。范围包含工作树既有功能回归，不代表这些功能由本任务实现。warnings 为现有 SWIG/TranslationEntry 弃用提示。

已知失败与复验：

- 早期扩大 GUI 回归为 558 passed、2 failed。上述两项分页保存测试在 `translation_epochs.publish_alignment_epochs` 读取已过期 deferred rows 时抛 ProjectStorageConflict。使用 HEAD `43df54bc` 的源码叠加任务开始前捕获的 source diff，在隔离目录重建初始工作树并切换 PYTHONPATH，运行同两个测试仍然 2 failed、堆栈一致。它们属于开始前已存在问题，本次未修改，最终回归明确排除。
- 中间合并回归曾为 794 passed、1 failed、2 deselected：`test_workbench_open_filter_sort_and_save_preserve_complete_range` 在完整筛选尚未就绪时报等待错误。该项无代码改动单独重跑 1 passed，并在上面的最终扩大回归通过；记录为观测到的时序波动，不宣称已修复其时序问题。
- 未运行全仓 pytest、发行包或真实桌面人工点击验收；使用 Qt offscreen 和后台回调测试，不宣称完成用户整批文件的本轮人工导出。此前崩溃诊断另见[原记录](../../file-io-dialogs/story-01-to-03/2026-11-13-002-export-responsiveness-and-stack-capture.md)。

本任务的基线副本、运行日志和测试临时目录在收尾核验归属后清理；保留已有用户数据和此前诊断材料。
