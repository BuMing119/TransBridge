# 纯插件对话阅读布局：真实来源与独立审阅

日期：2026-10-29。范围：[S28](../../plans/paratranz-categorized-upload/plan.md#s28-纯插件对话阅读布局)、[ADR-047](../adr/047-opt-in-categorized-project-upload.md) 与 FR3.4.7。本报告保存指标和边界，不保存用户词条正文、译文或完整导出文件。真实来源及正式接入验证完成；最终命令和逐文件变化见[S28增量记录](../changelogs/paratranz-categorized-upload/story-28/2026-10-29-001-plugin-dialogue-reading-order.md)。

## 结论

正式实现从已保存插件内容构造关系，完成 64,998 条词条、1,491 个分类、1,480 个对话任务的全量只读验证。1,473 个任务正常产生布局，7 个任务因插件内跨任务 TCLT 关系保守保留基线；没有删掉异常边后把这些任务算作排序成功。正常布局中 1,402 个选择 A、71 个选择 D，370 个任务实际移动对话块。布局完成不等于游戏播放顺序正确或与 XML 一致。

独立复核从逐任务摘要重新计数，得到相同的 1,473/7、1,402/71、370 和回退词条数 835；核对脚本的断言及输入保护。独立复核没有重复扫描大型来源，也没有读取 XML。本轮没有进行新的 XML 对照评分，不引用临时实验中的参考一致率作为正式多任务效果。

## 来源、隔离与不变量

- 使用既有 artifact `b47d938be4ebc6d855965e1d7242dd6710ec9702dcc3ab2f8b1e612e867a3110`，通过正式 artifact reader 校验 manifest 及其闭包；捕获插件 SHA256 为 `e198c3b85e5e48e0c92a6580d8f66e644256b68d812ee32b61735cf9b753df73`。仅使用已存内容，不从原始插件参考路径、masters 或外部 XML 补取关系。
- 关系提取保留 51,374 条相关记录及 7,804 条 TCLT 链接。真实来源有 4 条跨任务关系诊断，影响 7 个任务、835 条词条；任务回退率约 0.473%。本来源未触发分支软证据整体回退，不能因此省略合成坏证据回归。
- 全量身份集合、EntryKey、原文、基础 context、plugin.source_order、其余元数据、revision、string_id、provenance 保持；分类路径和各分类身份集合保持，原输入在验证前后摘要一致。
- 所有正常布局任务的 DIAL 块内 INFO/响应次序、SCC 内基线次序保持，全部跨 SCC TCLT 朝向满足；无输出节点参与图但不生成词条。
- 分类数组、显示编号与 wire 一致；JSON 内存往返及稀疏子集/模拟分片保持完整来源编号。这里的模拟分片检查不替代文件执行器的真实 413/补传回归。
- 验证进程禁止网络与 XML/reference 输入，限制写入到本轮临时验证目录；原工程与云端写入为 0。冻结 v3 结果只在正式输出已经计算后读取用于等价比较，不反向作为排序输入。

DA10 的 126 个 key、63 个 DIAL、候选指标和选择结果与用户接受的 v3 逐项相同。A/D 均满足 28 条跨 SCC 链接，8 条环内链接保持环内基线。D 有 9 对真实链接相邻，A 有 8 对；D 的基线逆序数 261，高于 A 的 254，按冻结指标优先级仍选择 D，不声称 D 每项都更好。

## 其他任务的结构样本

全体 1,480 个任务都执行不变量检查。下面是供人工理解的确定性结构样本：在读取冻结 v3 结果前按插件图条件选择，符合条件时取稳定 Quest ID 首项；最大规模样本按条目数选择。没有按 XML 排名、吻合率或输出美观程度挑选。独立复核另从同一全量已验摘要补列 DIAL 节点最多任务，未重新运行或调整算法。

- `0001EA5C`：条目最多的任务，1,030 条、249 个 DIAL、140 条链接；选择 D，移动 239 块。多 INFO 出口意见不一致时按基线软取舍，未新增硬边。
- `00045923`：DIAL 最多的任务，671 条、291 个节点、149 条链接；包含 43 个无输出节点，其中 2 个桥节点。选择 D，移动 219 块，149 条跨 SCC 硬边全部满足。分支额外片段 2→1、可避免入口晚序 1→0，但总链接间隔 149→154，说明指标间存在取舍。
- `00000E46`：2 条、2 个无边孤立节点；选择 A，不移动，保留基线。
- `00015CC2`：26 条、13 个 DIAL、7 条链接，最大出度 3；选择 D，移动 10 块，分叉的真实硬约束全部满足。
- `00018B4B`：343 条、117 个 DIAL、164 条链接，9 个多节点 SCC；选择 A，移动 77 块。98 条跨 SCC 边满足，66 条环内边保留原序，没有强行拆环。
- `0001CF25`：184 条、66 个 DIAL，含 3 个无输出桥节点；选择 A，移动 58 块。41 条跨 SCC 边满足，9 条环内边保持，空桥没有丢失或变成输出词条。
- `00013EB3`：802 条、65 个 DIAL，6 个多响应 INFO，无 TCLT 边；选择 A，不移动。多响应块内次序保持。

回退任务为 `0002522F`、`00025231`、`0002D515`、`00036193`、`0003FA16`、`00044C56`、`0004B2D9`。这些任务的原关系超出当前同 Quest 布局约束；保持基线是本轮明确的保守行为，不能计作改善。没有新增跨任务排序或推断剧情时间。

单次只读验证中关系提取约 9.66 秒、一次完整投影约 1.72 秒、含 artifact 读取与全部断言总计约 57.44 秒。这是本机单次观察，未测多轮分布、峰值内存或 Qt 端到端取消延迟，不作为性能承诺。

真实来源最终验证命令（PowerShell，仓库根目录）如下，退出码 0：

```powershell
$env:UV_CACHE_DIR = 'D:/MyCode/TransBridge/qa-tmp-dialogue-order-20261028/uv-cache-layout'
uv run python qa-tmp-dialogue-order-20261028/production-validation/validate.py
```

独立复核使用 `.venv/Scripts/python.exe -B -` 只读上述 summary/tasks 与诊断，重算数量并扫描本轮生产模块的物理行数/类方法数，退出码 0。没有再次读取大型插件输入。最终正式输出比较的冻结 v3 SHA256 为 `410315d0e1a84353d4f9429f0058b45d863e9a87c273aaa6caeb44d7a8a265a8`。

## 独立代码审阅与修复追踪

审阅识别并反馈了以下真实边界，修复由对应实现方完成：

- 身份多重集合比较必须使用字典的 keys；`Counter(dict)` 会把条目对象当计数并拒绝正常布局。
- 有效 QNAM 加损坏 BNAM 只禁用分支偏好；DLBR 的有效任务归属与损坏 SNAM 起点分开处理，保留有效硬图并退回 A。
- 保存图的 metadata 即使字段类型合法，也须拒绝缺失 INFO、重复记录或矛盾有效链接，回到已捕获字节重派生，不能在投影中抛 KeyError。
- 普通上传及下载/结果文字读取不应派生图；按调用显式启用，读模型缓存区分有/无图。取消异常必须越过保存原文降级逻辑。
- 完整来源先投影编号，之后按完整 EntryKey 取当前版本内容；脚本跳过空项也在编号之后完成，保持最新译文/状态并拒绝重复身份。

独立审阅中首次原样 `uv run pytest` 因沙箱默认 uv cache 无写权限未启动 pytest；把 `UV_CACHE_DIR` 指向本轮专用临时目录后重试。算法/提取专项首次 54 项通过。随后四组新测试联合运行得到 71 项通过、4 项 fixture setup 错误，原因是新增工程测试缺少 `timestamp_factory`；该中间结果已交主会话修复，不是最终验收结果。最终命令与计数由 S28 收尾记录统一报告，避免将重叠测试相加。

## 扩大回归中的既有失败复现

扩大回归发现的以下三项失败，已在本轮起始 HEAD `512e0b4ba4de458d281fe5d454d94f46df10fa96` 的生产源码下独立复现，不能归因于纯插件布局变更：

- `tests/parser/test_plugin_parser_failures.py::test_strict_parse_preserves_loading_exception_and_default_compatibility`：测试只替换 `from_file`，但 HEAD 的严格解析已经先读取并检查真实 TES4 文件头；不存在的 `test.esp` 先抛 FileNotFoundError，与测试期望的 mock OSError 对象不同。
- `tests/parser/test_plugin_parser_failures.py::test_strict_parse_accepts_valid_plugin_without_translatable_strings`：同样在 HEAD `plugin_parser.py:66` 的真实 `path.open` 处遇到不存在的 `test.esp`，尚未调用替换后的解析器。
- `tests/persistence/v2/test_source_artifact_archive.py::test_pure_record_legacy_import_preserves_translation_without_reading_reference`：导入、打开成功断言通过，但第 170 行仍读取已经退出当前来源缺失合同的 `result.value["read_only"]`，HEAD 同样抛 KeyError。当前缺来源内容不应使整个工程只读的合同见 ADR-050，本轮不恢复旧行为。

复现方式：使用 `.venv/Scripts/python.exe -B -` 启动隔离 pytest，以只读 `git show HEAD:<path>` 取得本轮修改的全部生产模块旧源码，通过进程内 import loader 执行；未改工作树文件或 checkout。实际加载的 8 个旧模块及内容摘要记录在临时证据 `production-validation/baseline-three-tests.txt`。pytest 参数为上述三个节点、`-q -p no:cacheprovider --basetemp=.tmp-dobr`，结果 **3 failed in 1.26s，退出码 1**，与扩大回归的三种失败逐项一致。短路径排除了此次 Windows 长路径因素；自建 basetemp 在复现后清理。此结论仅适用于这三项，不将其他未复现失败称为既有失败。

## 责任尺寸与验证限制

新增纯布局、图、指标、二进制提取、证据和投影各模块均少于 500 行，职责与数据流分开；不存在用 mixin 或通用 helper 隐藏大型控制器。已有 `legacy_adapters.py` 在本轮审阅时为 729 行，超过 700 行；本轮只增加捕获字节传递和独立证据模块的薄委托，没有把排序算法、图扫描或缓存责任放入该文件。已有 `gui_facade.py` 约 534 行、27 个方法，只转发可选证据/取消参数。为避免本次小范围接线夹带通用格式适配或工程命令重构，暂不拆分；剩余风险是这些既有入口继续增长，下次增加解析责任或 facade 业务逻辑前应先按格式适配与用例责任拆分，并运行对应输入/保存回归。

最终主回归1,014项通过、1项符号链接环境跳过、4项排除；其中EET测试在普通本地权限下单独复跑1项通过，合计1,015项通过，其余3项为上文真实复现的HEAD既有失败。全库及两脚本Ruff检查通过，2,327文件格式检查通过，git diff --check通过。中间fixture、_capture无参数兼容及只读模型字段断言已修复，Windows长路径改短自有目录后复验通过；重复批次不相加。

真实验证脚本和逐任务摘要位于任务临时区 `qa-tmp-dialogue-order-20261028/production-validation/`，正式仓库只保留本报告的无正文指标。未执行真实 ParaTranz 写入、人工桌面验收、游戏运行、新的 XML 相似度比较、全仓 pytest或构建。
