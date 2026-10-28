# 词条编辑与任务上下文

工作台双击词条后打开可复用的非模态 `EntryEditorDialog`。主导航不增加页面，窗口由 `MainWindow` 组合并持有；`DialogueEditorController` 负责定位、草稿和导航。需求见 [FR7.9](../requirements.md#fr79-workbench-interaction)，实施与验收见 [计划](../../plans/dialogue-context-editor/plan.md)。

## 筛选、定位与导航范围

- 工作台搜索、分类和状态筛选只改变表格投影，不缩减 `AppContext.collection`。双击通过完整 `EntryKey` 定位，不能把筛选后的行号用作源集合位置。
- 有任务关联的插件 QUST/DIAL/INFO 词条使用当前内容的完整关联索引，导航显示“任务／场景”。右侧话题可包含未命中工作台筛选的关联词条；任务树按 FormID 浏览记录，SCEN 仅汇总明确引用，不代表条件分支或演出顺序。
- 普通词条或不支持任务上下文的来源使用打开时的完整筛选／排序列表，导航显示“打开时列表”。纯 EET 可以编辑译文，但不加载任务树；插件叠加 EET/XT 译文仍按插件来源提供任务上下文。
- 主表复选框只选择词条；其余数据列双击打开编辑窗口，F2 保留译文行内编辑。应用、关闭和返回沿用既有草稿保护与工程写入流程。

## 首次加载与目录补全

索引在编辑器首次使用时准备，后续按来源和关系变化失效。大型已保存插件可能需要从 `SourceSnapshot.content` 重建只读场景目录；不能等完整解析结束后才显示已有的任务关系。

1. `DialogueEditorController._prepare_context()` 捕获当前内容的完整词条，交给后台加载器。准备期间窗口可打开和编辑，窗口顶部显示正在建立索引的原因。
2. `DialogueIndexLoader.build(..., on_preview=...)` 在来源目录缓存未命中时，先调用 `build_dialogue_index(entries)`，通过可选回调交付已有 `context`／父 DIAL 元数据形成的索引。这一阶段不从相邻顺序推测关系，也不制造 SCEN 节点。
3. 加载器再读取现有 `plugin` 或快照中的捕获字节，提取 QUST/DIAL/SCEN 目录并构建完整索引。快照恢复不重新打开可能已变化的源文件；同一来源对象的目录继续复用缓存。
4. `IndexDelivery.preview_ready` 把预览排队交回 GUI 线程，最终结果沿用 `ApiWorker.result`。控制器显示“正在补全话题与场景记录”，已有任务关系可先浏览和编辑；完整结果到达后清除提示。

预览只能提供已保存词条包含的关系，缺少关系时仍可能暂用“打开时列表”，直到完整目录提供关联。预览与最终结果均受代次、内容作用域和窗口寿命检查保护；预览不替换已有的记录选择，避免覆盖当前 SCEN 导航。完整结果刷新保持当前草稿、光标和工作台筛选。

## 状态与失败处理

`DialogueEditorView.context_reason` 放在窗口顶部，独立于会隐藏的任务树分栏。加载、来源能力不足及失败提示不能随分栏一起消失。

- 目录补全失败时保留已交付的可用关系索引和草稿，并显示包含原始失败原因的提示；允许后续重新打开词条或重新选择内容时重试。
- 没有任务关联的普通词条仍可编辑。纯 EET 显示任务树不可用的具体原因。
- `IndexDelivery` 只弱引用控制器；控制器关闭或销毁后丢弃交付，后台完成后释放工作线程。仅关闭弹窗时，晚到结果也不得重新打开窗口。

## 文件职责

- [application/dialogue/index.py](../../src/transbridge/application/dialogue/index.py)：与 Qt 无关的任务／话题关系和完整身份定位。
- [application/dialogue/loading.py](../../src/transbridge/application/dialogue/loading.py)：元数据预览、来源目录读取和缓存，回调本身不操作 UI。
- [ui/dialogue/index_delivery.py](../../src/transbridge/ui/dialogue/index_delivery.py)：预览／最终结果的跨线程交付、寿命检查及线程清理；由原控制器内的交付类提取。
- [ui/dialogue/controller.py](../../src/transbridge/ui/dialogue/controller.py)：完整内容作用域、记录选择、两阶段结果接入及草稿导航。
- [ui/dialogue/view.py](../../src/transbridge/ui/dialogue/view.py)：任务树分栏、独立可见的状态提示和正文编辑区域。

## 回归验证

[筛选与慢目录回归](../../tests/ui/test_dialogue_filter_context_loading.py) 用真实鼠标双击，并在目录读取尚未完成时断言任务树已经可见。覆盖实时插件与快照、补全成功与失败、草稿／光标／筛选保留，以及 EET 提示可见性。

[加载器回归](../../tests/application/test_dialogue_loading.py) 检查预览先于目录读取交付、完整结果补入 SCEN、迭代器输入不会被预览耗尽，以及缓存命中不重复交付预览。[工作线程寿命回归](../../tests/ui/test_dialogue_worker_lifetime.py) 覆盖控制器销毁后的晚到预览、结果和失败。

相关验证命令（PowerShell）：

```powershell
$taskDialogueTests = @(rg --files tests/ui -g 'test_dialogue*.py')
uv run --no-sync pytest @taskDialogueTests tests/application/test_dialogue_loading.py tests/application/test_dialogue_index.py -q
uv run --no-sync ruff check src tests --no-cache
uv run --no-sync ruff format --check src tests --no-cache
```

本次 133 项相关测试通过，未执行全仓 pytest。实际故障、真实保存来源的离屏测量口径及桌面复验限制见 [2026-10-28 修复记录](../changelogs/dialogue-context-editor/story-02-04-progressive-context/2026-10-28-001-filtered-task-context-loading.md)。本修复不改变 EntryKey、项目／翻译文件格式或依赖，无数据迁移要求。
