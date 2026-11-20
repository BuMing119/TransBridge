# 分页工程重开后的任务树与场景恢复

2026-11-20 · dialogue-context-editor · [Story 02／04](../../../../plans/dialogue-context-editor/plan.md)

## 用户可见变化

1. 重开插件工程，双击任务、话题或对白，编辑器先用已经加载的词条显示任务树和当前记录。完整索引在后台补全。
2. 新建插件工程或更新插件来源会同时保存任务、话题、场景的导航目录。删除原插件后重开仍能查看具名场景、空话题及场景引用的对白。
3. 旧工程继续使用已保存的词条关系和话题顺序。需要补齐场景时，可在 **项目 → 更新源文件…** 选择建项时的同一插件。回归用例验证了同字节更新后已保存的译文和阶段。

## 文件与数据流

- `parser/plugin/dialogue_catalog.py` 增加导航目录的序列化与校验，并在遍历插件时处理取消。`application/io/plugin_dialogue_catalog.py` 封装目录版本、已有来源指纹和导航记录。`application/io/legacy_adapters.py` 在解析插件时捕获一次。
- `application/projects/source_fields.py` 和 `source_extraction.py` 将目录纳入工程来源并能还原。`persistence/project_store/archive_native.py` 校验可携工程包里的目录。SQLite 表结构和词条身份保持兼容。新字段在来源的可选格式数据中，旧版导入器的字段白名单可能拒绝包含该字段的新工程包。
- `ui/project_rows.py` 在捕获词条迭代器时提供同一工程版本的延迟目录读取。`ui/dialogue/index_delivery.py` 将读取交给后台线程。`application/dialogue/loading.py` 按来源缓存目录和排序事实，对只有旧排序记录的工程恢复可证实的话题归属，返回补录提示。带字节快照仍按保存的读取选项解析。
- `application/dialogue/index.py` 统一支持任务树的记录类型，并在旧记录缺少名称时保留词条提供的名称。`ui/workbench/project_paging.py` 允许已显示的插件任务行直接打开。普通行沿用原有完整筛选列表导航。`ui/dialogue/controller.py` 先显示缓存页和所选行构成的局部树，再接收完整结果。
- `tests/persistence/project_store/test_dialogue_catalog.py`、`tests/application/test_dialogue_loading.py` 覆盖目录往返、工程包校验、旧顺序记录与不同来源缓存。`tests/ui/test_dialogue_paged_first_use.py` 在阻塞完整查询时验证真实双击、草稿和筛选。`tests/integration/gui/test_project_dialogue_catalog_reopen.py` 在删除原插件并重建服务后验证新工程与同字节更新过的旧工程。
- [开发说明](../../../dev/dialogue-context-editor.md)、本 Epic 计划及两处索引同步。此记录只涉及本轮任务树差异，工作区已有的文本编码与主界面改动另有归属。

## 验证

在本机 3DNPC.esp 工程的临时数据库副本上，对同一条 INFO 发送离屏 Qt 鼠标双击。整个来源有 53,648 条词条。修复前任务树约 7.72 秒可见。最终版本约 0.078 秒可见，完整索引约 11.78 秒就绪，含 51,944 条可定位词条及 503 个任务分组。耗时从双击开始，工作台已可用。本次测量仅代表该机器与样本。

合并回归 412 项通过，有 16 条依赖或旧接口弃用警告。收尾后针对首次双击、筛选、后台生命周期和离线重开的 19 项再次通过。全 `src/tests` Ruff、格式及 Git 空白检查通过。未运行全仓 pytest，未制作安装包。

```powershell
$dialogueTests = @(rg --files tests/ui -g 'test_dialogue*.py')
uv run --offline --no-sync --no-cache pytest @dialogueTests `
  tests/application/test_dialogue_loading.py tests/application/test_dialogue_index.py `
  tests/integration/gui/test_project_dialogue_catalog_reopen.py tests/ui/test_workbench_dialogue_reading_order.py `
  tests/ui/test_project_creation_file_workflow.py tests/ui/test_source_update.py tests/persistence/project_store `
  tests/parser/test_dialogue_catalog.py tests/parser/test_dialogue_order_evidence.py `
  tests/contracts/io/test_dialogue_order_cache.py tests/contracts/io/test_dialogue_order_projection.py `
  tests/contracts/io/test_dialogue_reading_view.py tests/integration/gui/test_project_paged_workbench.py `
  tests/contracts/io/test_plugin_export_destinations.py tests/contracts/io/test_localized_hydrated_publication.py `
  tests/contracts/io/test_legacy_format_adapters.py tests/application/projects/test_dialogue_order_publication.py -q
uv run --offline --no-sync --no-cache ruff check src tests
uv run --offline --no-sync --no-cache ruff format --check src tests
git diff --check
```
