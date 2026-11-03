# 纯插件对话内部默认显示验证

日期：2026-10-29。结论：S28 内部默认显示接入已完成，最终 315 项组合回归通过，独立审阅未发现剩余阻断。范围为内部显示补齐，复用已认可算法；[增量 002](../changelogs/paratranz-categorized-upload/story-28/2026-10-29-002-plugin-dialogue-internal-display.md)记录实现。前轮分类导出验证见[专项报告](dialogue-order-production-qa-2026-10-29.md)，其测试数量不作为本轮 UI 完成证据。

## 独立审阅

检查公共显示投影、完整来源 hydration、工作台显示索引、对话树及 worker 生命周期。本轮审阅发现并促成修正：当前版本 tombstone 会使可见集合不完整，因此在既有 hydration worker 中保留完整 `ReadingEntry` 结构，之后仅按可见身份投影；工作台取消 token 必须转换成公共投影所需 callable；无话题映射的树预览保留既有排序；实际 `IndexDelivery` 需接通取消 token，而非只给 loader 增加参数；同来源结构失败清除旧秩并保守回退。

当前代码以完整身份关联秩与当前条目，不替换 collection 或词条字段。工作台自定义列排序继续生效，第三次点击返回默认阅读序；F2 编辑期间延迟发布新秩。对话树只替换 DIAL 节点槽位，journal、SCEN 引用及 INFO/多响应原序保留。来源、版本、快照和代际隔离迟到结果；取消/关闭不发布半成品。该结论来自代码审阅，实际执行证据另列。

## 真实来源数据验证

主会话执行 `uv run python qa-tmp-dialogue-order-20261028/internal-validation/validate.py`，独立审阅其脚本与摘要。脚本从只读 source artifact 构造公共显示投影，与分类器输出逐任务比较，再用 DIAL catalog 核对树索引。它不等同于真实 Qt 窗口或 SCEN loader 全流程。

- 64,998 个词条身份及原集合顺序保持，所有 `entry.to_dict()` 比较相等。
- 1,480 个有输出对话任务的内部行序与分类输出相同。
- 公共显示服务产生 1,502 个任务话题顺序映射，包含无输出节点/任务；树索引实际核对 1,502 个任务，并断言已核身份集合与全部映射相等。
- DA10 的 126 个词条、63 个话题逐项匹配用户认可的冻结 v3；比较发生在本次计算完成后。
- 读取审计禁止 XML 和网络，限制写入验证目录；真实工程写入为 0。

来源 artifact：`b47d938be4ebc6d855965e1d7242dd6710ec9702dcc3ab2f8b1e612e867a3110`；捕获插件 SHA-256：`e198c3b85e5e48e0c92a6580d8f66e644256b68d812ee32b61735cf9b753df73`；冻结 v3 SHA-256：`410315d0e1a84353d4f9429f0058b45d863e9a87c273aaa6caeb44d7a8a265a8`。临时证据路径仅供本轮核验，不作为仓库长期依赖，不收录用户词条文本。2026-10-30 恢复收尾时核对原始测试日志与摘要后，已清理本轮 11 个 `.tmp-doui-*` 目录及 `internal-validation` 临时验证目录；结果保留在本报告与增量 002，先前用户核对材料保持。

## 聚焦检查与限制

主会话的 56 项 application/contracts/算法组合回归通过。其中 `test_reopen_without_live_plugin_restores_same_order_and_does_not_save` 实际创建工程、删除原插件、通过 `current_project_opener.open_path` 重开磁盘成果，再从返回的 hydration 建立完整来源结构、公共显示投影及真实 captured catalog 的 `DialogueIndexLoader`；内部顺序与分类输出一致，前后版本快照相等。此项不同于只关闭再打开对话弹窗。

Qt 组合执行发现 `destroyed` 回调的默认参数 lambda 发生 `missing ... 'event'` 异常。实现改为独立纯 Python token 的 bound method。相同 40 项测试序列，新实现没有未捕获异常；只在进程内注入旧回调后复现 4 次相同异常，未改其他生产代码，形成差分证据。工作台同类生命周期回调也改为独立 token；不声称工作台旧回调已被证实触发相同异常。

这组差分通过临时 wrapper 调用 `pytest.main(["tests/ui/test_dialogue_editor.py", "tests/ui/test_dialogue_filter_context_loading.py", "tests/ui/test_dialogue_input_visuals.py", "-v", "-s", "--basetemp", "<本轮独占临时目录>"])`，同时启用 `faulthandler` 和收集未处理异常的 `sys.excepthook`。旧回调虽显示 40 项 pytest 通过，但出现 4 次未处理异常，wrapper 退出 1；新回调同序 40 项通过、未处理异常 0、退出 0。保持新版生产文件，仅进程内注入旧回调再次复现 4 次异常、退出 1。临时脚本在保留这些事实后清理，后续常规回归不依赖该脚本。

最终命令由主会话执行，退出码 0，**315 passed，21 个既有 deprecation warnings，39.21 秒**。较早 56 项与各 agent 的聚焦集合有重叠，不累加为新的通过总数。Windows 原生临时目录显式使用本轮独占路径，避免系统临时目录权限与长路径干扰。

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
$env:UV_CACHE_DIR = "D:/MyCode/TransBridge/.tmp-doui-cache"
$env:TEMP = "D:/MyCode/TransBridge/.tmp-doui-native"
$env:TMP = "D:/MyCode/TransBridge/.tmp-doui-native"
$testFiles = @(rg --files tests/ui | Where-Object { $_ -match 'test_(dialogue_|translation_table_|step2_|entry_refresh|interaction_filters|workbench_filter|workbench_dialogue|project_source_hydration|parse_)' } | Sort-Object)
uv run --no-sync pytest @testFiles tests/application/test_dialogue_index.py tests/application/test_dialogue_loading.py tests/contracts/io/test_dialogue_reading_view.py tests/contracts/io/test_dialogue_order_projection.py tests/application/projects/test_dialogue_order_publication.py tests/application/test_dialogue_order.py -q --basetemp=.tmp-doui-complete --tb=short
uv run ruff check src tests
uv run ruff format --check src tests
git diff --check
```

全库 Ruff、2,329 个 Python 文件的格式检查及差异空白检查均通过。组合覆盖默认/自定义/重置排序、筛选、完整来源缺行投影、F2 文本和弹窗草稿、当前对象刷新、取消/销毁/切源/切版本、磁盘重开及与分类输出的一致性。此次没有运行全仓 pytest、真实云端写入、游戏运行或人工逐任务阅读；这些不属于本轮内部接线自动验收。

## 责任尺寸审阅

新增生产切片均少于 500 行、主类少于 30 个方法。既有受改文件中，`parse_coordinator.py` 为 513 行/13 方法，`step2.py` 为 700 行/48 方法；本轮只增加结构传递及独立 coordinator 的生命周期委托，没有新增工作台主类方法或把算法/worker 实现放入该类。既有 `legacy_adapters.py` 729 行、`gui_facade.py` 544 行/27 方法继续采用薄边界委托。这里保留局部接线例外，风险是大型宿主仍难整体维护；下次扩大这些宿主的来源准备或显示职责前，必须按完整职责拆分，不继续添加内嵌业务流程。
