# S28：补齐软件内部的对话显示排序

日期：2026-10-29（Asia/Shanghai）。Epic：paratranz-categorized-upload；Story：S28。
关联：[计划](../../../../plans/paratranz-categorized-upload/plan.md#s28-纯插件对话阅读布局)、[内部显示验证](../../../test-reports/dialogue-order-internal-display-qa-2026-10-29.md)、[前轮分类接入](2026-10-29-001-plugin-dialogue-reading-order.md)。

## 原因与结果

前轮只接入分类输出，遗漏了用户要求的软件内部排序。本轮将同一套纯插件 DIAL 阅读顺序接入工作台默认列表及任务对话树。

只改变显示行/节点的位置与界面序号。原集合顺序、EntryKey、原文、译文、状态、context、metadata、来源序号及同一 DIAL 内 INFO/响应次序不变，不改变内部匹配或保存格式。XML 不参与运行排序。

完整来源先确定顺序，筛选及当前版本隐藏行再投影到可见身份；手动列排序继续生效，同一表头第三次点击恢复默认阅读顺序。新顺序后台计算，F2 未提交草稿期间延后应用，保持选中词条及编辑目标。

## 逐文件变化

生产路径相对 `src/transbridge/`：

- 新增 `application/io/dialogue_reading_view.py`：`ReadingEntry` 只携带来源结构；`DialogueReadingView` 返回不可变的完整身份顺序及任务话题映射。复用正式算法，包含无输出 DIAL 与任务，损坏证据保守回退，不生成输出 context 或改写词条。
- 修改 `application/io/dialogue_order_projection.py`：抽取共享 `restore_dialogue_source_order`，增加取消检查及按需覆盖全空话题任务；分类默认入口行为保持。修改 `paratranz_classification.py` 使用该基线函数，删除重复实现。
- 修改 `ui/projection_types.py`：`CollectionSlot` 增加可选 `reading_source_entries`，仅作内存显示输入。修改 `ui/source_hydration.py`：在版本 tombstone 投影前保留完整来源结构。修改 `ui/coordinators/parse_coordinator.py`、`project_coordinator.py`：在已有后台准备中捕获和传递完整结构，不在 Qt 主线程重新解析源文件。
- 新增 `ui/workbench/dialogue_reading_order.py`：分批捕获不可变结构、后台读取已捕获插件关系并构建顺序；完整准备结果直接复用。来源、版本、快照、完整来源结构及代际隔离迟到结果，编辑内容改变不重建图；失败清理旧排序并保守回退。
- 修改 `ui/workbench/step2.py`：创建、刷新、关闭委托给新 coordinator。修改 `translation_table_sorting.py`：默认/序号列使用全源身份顺序，其他列继续显式排序，同值稳定排列。修改 `translation_table.py` 与 `translation_model.py`：只安装显示索引和显示序号，沿用身份选择与编辑映射。
- 修改 `application/dialogue/loading.py`：从 captured snapshot 或已加载插件读取证据，调用共享显示服务；可接收完整来源结构。修改 `index.py`：仅重排 DIAL 槽位，保留任务日志、SCEN 引用和 INFO 次序；无映射时保留既有显示。
- 修改 `ui/dialogue/controller.py`、`index_delivery.py`、`index_state.py`：传递完整来源结构，跟踪其变化；切源、切版本、关闭和销毁主动取消，迟到结果不发布，worker 正常退出清理。
- 联合测试发现 Qt `destroyed` 默认参数闭包在跨文件对象回收时产生参数异常并终止进程。`index_delivery.py` 改为纯 Python token 的 `cancel(*args)`，工作台对应使用 `close(*args)`；回调只持有取消状态。相同测试序列注入原闭包重现四次异常，修正后为零，完整组合正常退出。

测试变化：

- 新增 `tests/contracts/io/test_dialogue_reading_view.py`：内部/分类同序、词条字段不变、全源筛选、空节点、坏证据、混合来源/重复身份和取消。
- 新增 `tests/ui/test_workbench_dialogue_reading_order.py`：真实捕获插件、后台执行、全源序号、筛选、手动排序/复位、选择、F2 草稿、编辑复用、切源、销毁及失败回退。
- 更新 `tests/application/test_dialogue_index.py`、`test_dialogue_loading.py`：空 DIAL、命名空间、场景、同 DIAL 顺序、完整来源子集和旧来源恢复。
- 更新 `tests/ui/test_dialogue_draft_focus.py`、`test_dialogue_worker_lifetime.py`、`test_dialogue_lazy_index.py`：重排保留草稿/光标/身份，实际 worker 取消与对象回收。更新 `test_dialogue_filter_context_loading.py`：以有界条件等待过滤完成，取代恰好 200 毫秒的固定等待。
- 更新 `tests/ui/test_project_source_hydration.py`：隐藏行仍保留在完整阅读结构，非插件不增加排序输入。扩展 `tests/application/projects/test_dialogue_order_publication.py`：磁盘工程重开且原插件删除后，经 hydration、共享显示投影和真实 loader 恢复一致排序，版本快照不变。

同步 FR3.4.7、ADR-047、开发说明、S28 计划及相关索引，新增内部显示 QA。前轮 `001` 增量及验证报告保持历史原样。既有大文件的局部委托例外与后续拆分条件见本轮 QA。

## 验证

真实保存来源共 64,998 条：1,480 个有输出任务的内部行序逐项等于分类结果；1,502 个任务话题映射全部经公共树索引核对，含无输出任务。DA10 的 126 个键、63 个 DIAL 与已认可 v3 相同。全部词条 `to_dict()` 和原集合顺序相等，XML 读取及真实工程写入均为零。真实来源树检查使用从插件证据构造的 DIAL catalog；Qt 与完整 loader 行为由以下回归覆盖，不宣称进行了大源人工 UI 逐项验收。

最终同进程回归 **315 项通过**，39.21 秒，21 条既有弃用警告；退出码为 0。缓存和 TEMP/TMP 指向本轮仓库临时目录，`QT_QPA_PLATFORM=offscreen`。命令在仓库根目录执行：

```powershell
$testFiles = @(rg --files tests/ui | Where-Object {
    $_ -match 'test_(dialogue_|translation_table_|step2_|entry_refresh|interaction_filters|workbench_filter|workbench_dialogue|project_source_hydration|parse_)'
} | Sort-Object)
uv run --no-sync pytest @testFiles tests/application/test_dialogue_index.py tests/application/test_dialogue_loading.py tests/contracts/io/test_dialogue_reading_view.py tests/contracts/io/test_dialogue_order_projection.py tests/application/projects/test_dialogue_order_publication.py tests/application/test_dialogue_order.py -q --basetemp=.tmp-doui-complete --tb=short
uv run --no-sync ruff check src tests
uv run --no-sync ruff format --check src tests
git diff --check
```

Ruff 全部通过，2,329 个文件格式检查通过，Git 空白检查通过。初次组合发现的 Qt 回调异常和固定等待问题均已修复；两项系统临时目录权限错误在改用仓库内 TEMP 后通过，未更改产品逻辑绕过测试。

未执行全仓库 pytest、真实云端上传或游戏运行；本轮没有改变这些外部路径。未提交或推送 Git。当前范围没有未解决的失败项；任务自建缓存和临时验证输出在收尾时清理，先前用户核对材料保留。
