# 筛选后首次打开词条的任务上下文渐进加载

- 日期：2026-10-28
- Epic / Story：dialogue-context-editor / Story 02、04 后续修复；Story 数维持 9。
- 计划：[词条弹窗与任务／话题关联编辑](../../../../plans/dialogue-context-editor/plan.md)
- 开发说明：[词条编辑与任务上下文](../../../dev/dialogue-context-editor.md)

## 问题与定位

用户在工作台默认搜索框筛选后双击 DIAL，对话编辑窗口只显示正文和“打开时列表 · 14 / 33”，看不到已有的任务树。

最初的相关测试等待了后台索引全部完成，遗漏了等待期间的状态。使用保存的 Skyrim 来源离屏复验后确认：首次打开要等待完整快照解析／场景目录构建，已有词条关系也被延迟到最终交付；而加载原因在随任务树一起隐藏的分栏内部，用户看不到等待或失败原因。筛选未删除源集合关系，问题发生在筛选后首次使用编辑器的索引交付与展示阶段。

## 本次可归属的代码变化

- 修改 [application/dialogue/loading.py](../../../../src/transbridge/application/dialogue/loading.py)：`DialogueIndexLoader.build()` 新增可选 `on_preview` 回调；词条输入先固定为元组，目录缓存未命中时先交付现有关系，再解析 plugin／捕获的快照字节，最终补入源目录记录。继续复用目录缓存，不更改原返回值合同。
- 新增 [ui/dialogue/index_delivery.py](../../../../src/transbridge/ui/dialogue/index_delivery.py)：从控制器内的 `_IndexDelivery` 提取索引交付责任；`IndexDelivery` 统一接入预览和最终结果，保留控制器弱引用、销毁检查、工作线程引用及结束清理。
- 修改 [ui/dialogue/controller.py](../../../../src/transbridge/ui/dialogue/controller.py)：接入独立交付对象；`_loaded(..., complete=False)` 显示可用元数据索引及补全提示，代次／内容检查与最终结果共用；不让预览覆盖已有记录选择。`_show_context()` 传递状态原因，目录读取失败时可用预览和草稿继续保留。
- 修改 [ui/dialogue/view.py](../../../../src/transbridge/ui/dialogue/view.py)：把 `context_reason` 从任务树内部移到窗口顶部，加载、失败或 EET 能力提示在任务树分栏隐藏后仍可见。
- 新增 [tests/ui/test_dialogue_filter_context_loading.py](../../../../tests/ui/test_dialogue_filter_context_loading.py)：5 项回归覆盖真实鼠标双击、插件／快照、慢目录未完成时的树可见性、补全失败、草稿／光标／筛选保留，以及 EET 原因可见性。
- 修改 [tests/application/test_dialogue_loading.py](../../../../tests/application/test_dialogue_loading.py)：增加插件／快照两项交付顺序回归，检查预览先于目录读取、迭代器输入复用、SCEN 最终补全和缓存命中不重复预览。
- 修改 [tests/ui/test_dialogue_worker_lifetime.py](../../../../tests/ui/test_dialogue_worker_lifetime.py)：让受控后台构建在控制器销毁后交付预览，再交付结果或失败，验证晚到回调不触碰已销毁窗口。

只记录上述符号的本次变化；不把同一工作区内先前的惰性索引、投影／编辑权威边界调整、测试夹具迁移或其他任务改动归入本轮。

## 文档补齐

- 新增 [docs/dev/dialogue-context-editor.md](../../../dev/dialogue-context-editor.md)：集中说明筛选列表与任务导航的范围、两阶段数据流、线程交付、可见状态、失败保护和聚焦回归入口。
- 修改 [docs/dev/ui.md](../../../dev/ui.md)：替换过期的 `_EntryDetailDialog`／`itemChanged` 描述，按当前模型说明七列表格、选择接口和正文编辑入口；纠正 `plugin=None` 必然为 EET 的错误判定，并补入任务上下文目录与文档入口。
- 修改 [docs/requirements.md](../../../requirements.md)：在 FR7.9 原位置纠正双击／F2 与重复筛选弹窗的边界，补齐 FR7.9.7 渐进任务上下文及可见失败提示；不保留互相矛盾的编辑行为条款。
- 修改 [原计划](../../../../plans/dialogue-context-editor/plan.md)：同步 Story 02／04 的已实现行为、当前验收证据和限制，保留旧阶段验证记录，不新增 Story。
- 修改 [开发索引](../../../dev/INDEX.md)、[文档总索引](../../../INDEX.md)、[计划索引](../../../../plans/INDEX.md)和[增量索引](../../INDEX.md)：只增加本说明入口或更新本 Epic 的状态与记录链接；既有增量文件不改动。

## 验证

5 项新增 UI 回归在旧实现全部失败，修复后通过。最终相关回归为 **133 passed**（10 条弃用警告）；全 `src/tests` Ruff 检查、格式检查（2,252 个文件）以及目标 diff 空白检查通过。

实际执行的相关命令如下；`$taskQaRoot` 为本轮创建并已清理的仓库内临时目录 `qa-tmp-filter-context`，测试进程的缓存和临时路径也定向到该目录：

```powershell
$taskDialogueTests = @(rg --files tests/ui -g 'test_dialogue*.py')
uv run --no-sync pytest @taskDialogueTests tests/application/test_dialogue_loading.py tests/application/test_dialogue_index.py -q --basetemp "$taskQaRoot/pytest-all-dialogue"
uv run --no-sync ruff check src tests --no-cache
uv run --no-sync ruff format --check src tests --no-cache
git diff --check -- src/transbridge/application/dialogue/loading.py src/transbridge/ui/dialogue/controller.py src/transbridge/ui/dialogue/view.py tests/application/test_dialogue_loading.py tests/ui/test_dialogue_worker_lifetime.py
```

本次补文档沿用上述同一任务的代码验证证据；另检查新文档的本地链接、FR7.9 锚点、编号唯一性与本轮文档 diff。未重新运行全仓 pytest、构建、发布或外部写入。

## 真实保存来源的离屏证据

- 输入：只读使用保存的 Skyrim 来源，64,998 条词条；默认搜索输入 `I am not `（含末尾空格），命中 33 条，筛选前后定位同一 `DIAL:FULL` 词条。未复制用户译文到仓库文档，未修改用户工程或来源文件。
- 环境：Windows、Python 3.12.12、现有 uv 环境，离屏 `QApplication`；已先恢复来源和建立工作台，计时从 `open_entry()` 返回后进入事件处理等待开始。
- 修复前：约 **13.610 秒**等待完整目录后，任务树才可用。
- 修复后：约 **0.953 秒**已有任务关系可见，完整目录仍在后台补全，约 **12.750 秒**完成；最终树为 53 条记录，当前任务导航包含 177 条词条，超出 33 条工作台筛选结果。清除筛选后打开同一 DIAL，任务树和定位正常。
- 口径：本机单次诊断测量，不包含来源恢复、`open_entry()` 内同步准备或最终屏幕绘制，不代表端到端启动耗时、所有硬件的承诺或完整解析成本已经消失。临时诊断脚本、缓存和测试目录均已清理。

## 兼容性与遗留项

EntryKey、项目／翻译文件格式、工程写入流程和依赖保持既有合同，无数据迁移要求。普通词条、纯 EET、完整任务／场景导航的范围继续区分。

代码范围内无已知未完成项。修复后的用户桌面对照尚未执行：Astra 接手时没有运行中的 TransBridge 窗口，因此真实来源证据仅为离屏验证；不声明已完成用户桌面验收。
