# 词条编辑与任务上下文

工作台双击词条后打开可复用的非模态 `EntryEditorDialog`。主导航不增加页面，窗口由 `MainWindow` 组合并持有；`DialogueEditorController` 负责定位、草稿和导航。需求见 [FR7.9](../requirements.md#fr79-workbench-interaction)，实施与验收见 [计划](../../plans/dialogue-context-editor/plan.md)。

## 筛选、定位与导航范围

- 工作台搜索、分类和状态筛选只改变表格投影，不缩减 `AppContext.collection`。双击通过完整 `EntryKey` 定位，不能把筛选后的行号用作源集合位置。
- **ESP 记录**显示当前记录的类型、EditorID、FormID，以及每个可翻译字段的 ID、原文和已应用译文。双击书名或正文都能看到该书的全部字段。直接打开 QUST 也进入此页，重复的日志和目标按出现次序分别列出。选中一行即可在下方编辑该字段，切换字段保留各自的草稿。
- **话题与对话**按任务浏览关联的话题和场景。双击有关联的 DIAL 或 INFO 默认进入此页。两个页签均可用时，切换页签会保留当前词条。场景仅汇总明确引用的话题。
- 展开左侧任务下拉框，输入名称片段或 FormID，可从当前插件已载入的任务、话题和场景中查找记录。完整名称或编号优先。点击结果或按回车跳转，按 Esc 收起列表。搜索无匹配时列表留空，当前编辑位置和草稿保留。
- 同一条插件记录有多个字段时，上一条／下一条在记录内移动。单字段记录在具备打开时列表的情况下沿用工作台筛选顺序。纯 EET 仍可从工作台打开正文编辑。插件叠加翻译后，字段归属仍取自插件来源。
- 主表复选框只选择词条；其余数据列双击打开编辑窗口，F2 保留译文行内编辑。应用、关闭和返回沿用既有草稿保护与工程写入流程。

## 首次加载与目录补全

1. 导入插件或更新源文件时，从当次解析结果提取 QUST、DIAL、SCEN 的记录标识、名称、话题类别及显式关联。工程随来源保存这份目录和对话顺序。删除或移动插件后，工程仍可据此恢复场景及没有可翻译文本的话题。
2. 重开工程后，双击已显示的任务、话题或对白即可打开编辑器。第一帧用当前选中词条和缓存页中的词条建立局部任务树；窗口顶部提示正在补全。即使工作台的完整筛选结果仍在加载，用户也可以编辑或浏览已有的关联词条。
3. 后台按打开时的工程版本读取完整词条、来源目录和顺序，再把完整任务树交回窗口。来源切换会使旧结果失效。补全保留当前选择、草稿、光标和筛选范围。对于内存中的插件或带原始字节的快照，加载器沿用词条关系预览，并按来源记录的编码读取快照。
4. 旧工程缺少场景目录时，编辑器直接展示能从词条关系及对话顺序恢复的任务与话题。需要补全场景时，可在 **项目 → 更新源文件…** 重新选择建项时的同一插件。后台恢复只读工程数据。

## 状态与失败处理

`DialogueEditorView.context_reason` 位于窗口顶部。普通插件记录可独立于场景目录加载，记录页只呈现与当前记录有关的提示。任务关联加载失败时，仍可编辑已经读取的字段。

- 目录补全失败时保留已交付的可用关系索引和草稿，并显示包含原始失败原因的提示；允许后续重新打开词条或重新选择内容时重试。
- 没有任务关联的普通词条仍可编辑。纯 EET 显示任务树不可用的具体原因。
- `IndexDelivery` 只弱引用控制器；控制器关闭或销毁后丢弃交付，后台完成后释放工作线程。仅关闭弹窗时，晚到结果也不得重新打开窗口。

## 文件职责

- [application/dialogue/index.py](../../src/transbridge/application/dialogue/index.py)：与 Qt 无关的任务／话题关系和完整身份定位。
- [application/dialogue/loading.py](../../src/transbridge/application/dialogue/loading.py)：元数据预览、来源目录读取和缓存，回调本身不操作 UI。
- [ui/dialogue/index_delivery.py](../../src/transbridge/ui/dialogue/index_delivery.py)：预览／最终结果的跨线程交付、寿命检查及线程清理；由原控制器内的交付类提取。
- [ui/dialogue/controller.py](../../src/transbridge/ui/dialogue/controller.py)：完整内容作用域、记录选择、两阶段结果接入及草稿导航。
- [ui/dialogue/view.py](../../src/transbridge/ui/dialogue/view.py)：任务树分栏、独立可见的状态提示和正文编辑区域。
- [record_fields.py](../../src/transbridge/application/dialogue/record_fields.py) 识别同一来源、记录类型和 FormID 下的字段。[record_reads.py](../../src/transbridge/persistence/project_store/record_reads.py) 为分页工程在内存中建立来源目录，从已有工程数据按完整词条键读取同组字段。
- [record_browser.py](../../src/transbridge/ui/dialogue/record_browser.py) 管理字段读取和过期结果。[record_view.py](../../src/transbridge/ui/dialogue/record_view.py) 展示记录标识并将行选择交给共享编辑器。

## 回归验证

[记录字段离线重开回归](../../tests/integration/gui/test_project_record_fields_reopen.py) 创建含 BOOK、MGEF、QUST 的工程，移除原插件后再打开，验证筛选外字段、重复字段、切换页签、独立草稿、保存和快速切换记录。

[筛选与慢目录回归](../../tests/ui/test_dialogue_filter_context_loading.py) 用真实鼠标双击，并在目录读取尚未完成时断言任务树已经可见。覆盖实时插件与快照、补全成功与失败、草稿／光标／筛选保留，以及 EET 提示可见性。

[加载器回归](../../tests/application/test_dialogue_loading.py) 检查预览先于目录读取交付、完整结果补入 SCEN、迭代器输入不会被预览耗尽，以及缓存命中不重复交付预览。[工作线程寿命回归](../../tests/ui/test_dialogue_worker_lifetime.py) 覆盖控制器销毁后的晚到预览、结果和失败。

相关验证命令（PowerShell）：

```powershell
$taskDialogueTests = @(rg --files tests/ui -g 'test_dialogue*.py')
uv run --no-sync pytest @taskDialogueTests tests/application/test_dialogue_loading.py tests/application/test_dialogue_index.py -q
uv run --no-sync ruff check src tests --no-cache
uv run --no-sync ruff format --check src tests --no-cache
```

工程重开与源文件更新由[离线重开回归](../../tests/integration/gui/test_project_dialogue_catalog_reopen.py)覆盖。它在新服务实例中打开已删除原插件的工程，验证双击、场景成员、空节点和已保存译文。[分页首次打开回归](../../tests/ui/test_dialogue_paged_first_use.py)在完整读取被阻塞时检查弹窗和局部任务树。
