# ESP 记录字段编辑

2026-11-20 · dialogue-context-editor · [Story 10](../../../../plans/dialogue-context-editor/plan.md)

以前弹窗只为任务、话题和场景组织关联文本。双击书籍正文时，弹窗只显示正文，无法直接对照书名。新增的「ESP 记录」页按同一来源、记录类型和 FormID 展示全部可译字段。双击 QUST 也直接进入记录页。有关联的 DIAL 和 INFO 默认进入「话题与对话」。

## 实现

- 新增 `application/dialogue/record_fields.py`，统一从已有字段元数据或旧版词条键识别记录。每个字段出现位置保留完整词条身份。
- 新增 `persistence/project_store/record_reads.py`，由 `repository.py` 提供版本绑定的只读查询。`ui/project_rows.py` 将分页查询结果与当前版本的编辑状态合并。旧工程可从已保存的词条重建字段列表，无须迁移或重新导入插件。
- 新增 `ui/dialogue/record_browser.py`，在分页工程后台读取记录，快速切换时只交付当前请求。新增 `record_view.py` 并扩展 `models.py`、`view.py`，提供记录标题、三列表格与字段选择。
- `ui/dialogue/controller.py` 在同一编辑窗口接入两种浏览方式。打开字段时定位到双击的词条，读写复用原有草稿与工程命令。普通记录独立于任务索引读取。

## 验证

- 新增应用层分组、分页存储、Qt 表格以及工程离线重开的回归，并更新编辑窗重开和分页首次打开用例。筛选命中一本书的正文时可切到书名，重复的任务日志和目标均可分别选择。测试也检查独立草稿、应用保存、页签切换和延迟交付。
- 在含 53,648 条词条的实际工程临时副本中，工作台筛选精确命中 `YngvarsListofBards:0515E9F9|1~BOOK:DESC`，离屏双击后出现同书的 FULL 与 DESC。点击 FULL 后原文切换为书名，工作台仍只显示匹配的正文。
- 299 项相关回归通过。Ruff 检查和格式检查通过。未运行全仓 pytest，未制作安装包。

```powershell
$recordTests = @(rg --files tests/ui tests/application tests/persistence/project_store tests/parser tests/integration/gui -g 'test_*dialogue*.py' -g 'test_*record_field*.py' -g 'test_project_paged_workbench.py' -g 'test_plugin_tree_order.py')
uv run --offline --no-sync --no-cache pytest @recordTests -q
uv run --offline --no-sync --no-cache ruff check src tests
uv run --offline --no-sync --no-cache ruff format --check src tests
```
