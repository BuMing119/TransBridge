# 任务、话题和场景搜索

2026-11-20 · dialogue-context-editor · [Story 04](../../../../plans/dialogue-context-editor/plan.md)

「话题与对话」的任务下拉框可按名称片段或 FormID 搜索当前插件已载入的 QUST、DIAL 和 SCEN。输入时忽略大小写，完整匹配优先。展开时先列出任务，搜索话题、场景时同时列出所属任务。无匹配时结果列表留空。

- [quest_picker.py](../../../../src/transbridge/ui/dialogue/quest_picker.py) 增加带输入框和结果列表的任务选择器。点击或回车确认后交付对应的任务和节点，Esc 收起列表。后台刷新目录时保留搜索词，按记录身份恢复仍存在的高亮项。
- [view.py](../../../../src/transbridge/ui/dialogue/view.py) 将选择器接入原有的任务和话题导航。刷新候选项和输入搜索词都保留当前编辑的词条及草稿。
- [搜索回归](../../../../tests/ui/test_dialogue_quest_search.py) 覆盖大小写、子串、精确匹配优先、同名节点、无匹配、键盘与鼠标选择、草稿保留和分页索引补全。

## 验证

在实际 `3DNPC.esp` 工程的临时副本中，从截图所在的任务展开列表，共有 503 个任务。分别输入 `050028B1`、`RongeirShelurScene2` 均得到同一个唯一结果。按回车后打开该任务的 QUST:FULL。输入无匹配内容时结果行数为零，回车保留原词条。

以下命令覆盖搜索、词条编辑器与工程重开，共 135 项回归。Ruff 静态和格式检查覆盖全部 src 与 tests。未运行全仓 pytest，未制作安装包。

```powershell
$dialogueTests = @(rg --files tests/ui -g 'test_dialogue*.py' -g 'test_record_fields_view.py')
uv run --offline --no-sync --no-cache pytest @dialogueTests tests/integration/gui/test_project_dialogue_catalog_reopen.py tests/integration/gui/test_project_record_fields_reopen.py -q
uv run --offline --no-sync --no-cache ruff check src tests
uv run --offline --no-sync --no-cache ruff format --check src tests
```
