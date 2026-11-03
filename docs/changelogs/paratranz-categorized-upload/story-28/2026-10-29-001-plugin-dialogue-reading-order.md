# S28：分类导出接入纯插件 DIAL 阅读排序

日期：2026-10-29（Asia/Shanghai）。Epic：paratranz-categorized-upload；Story：S28。
关联：[计划](../../../../plans/paratranz-categorized-upload/plan.md#s28-纯插件对话阅读布局)、[FR3.4.7](../../../requirements.md)、[ADR-047](../../../adr/047-opt-in-categorized-project-upload.md)、[专项验证](../../../test-reports/dialogue-order-production-qa-2026-10-29.md)。

## 行为与边界

用户接受临时 v3 的 The House of Horrors 完整 DIAL/INFO 排列，授权接入正式程序并检查其他任务。
此前分类器只恢复来源次序；现在分类上传与两个分类转换脚本使用捕获插件中的真实连接、分支归属和入口来排列完整 DIAL 块。
内部 EntryKey、key、原文、译文、基础 context、plugin.source_order 及同 DIAL 内 INFO/多响应顺序保持；仅分类输出数组与独立展示编号改变。
XML、译文、字符串编号、EditorID 名称不参与选择或排序。普通上传、仅译文请求和下载匹配合同保持。

本轮开始时 HEAD 为 `512e0b4ba4de458d281fe5d454d94f46df10fa96`，已跟踪工作区无差异；未跟踪 `.transbridge/` 为既有用户内容，未读取或改动。
本记录只涵盖正式接入阶段，不把先前临时 v1/v2/v3 的试验代码或测试次数算作新增正式实现。

## 逐文件变化

生产路径均相对 `src/transbridge/`：

- 新增 `application/dialogue_order/models.py`：来源/任务作用域的 Topic、Item、Link、Branch、FieldOrder；键支持完整 EntryKey。
- 新增 `application/dialogue_order/graph.py`：验证输入、迭代 SCC 和弱连通分组；损坏硬关系明确保留基线，不删边掩盖错误。
- 新增 `application/dialogue_order/candidates.py`：冻结 A/D 候选；分支、入口及 INFO 出口字段序只影响合法候选优先级，不制造硬边。
- 新增 `application/dialogue_order/metrics.py`：仅插件内指标比较，逆序计数和入口前驱查询避免复制原型的全图可达矩阵。
- 新增 `application/dialogue_order/layout.py` 与 `__init__.py`：纯函数入口、候选选择及诊断；无 I/O、XML 或用户数据依赖。坏分支提示强制 A，SCC 内保留基线。
- 新增 `parser/plugin/dialogue_order_binary.py`：有边界检查的捕获字节扫描与既有解析对象遍历，共用字段序和取消语义；处理压缩与 XXXX，保留无 FULL/无输出节点。
- 新增 `parser/plugin/dialogue_order_evidence.py`：验证 DIAL/INFO 所属、TCLT、DLBR/QNAM/SNAM/BNAM，区分硬错误与软偏好错误；提供不可变、可校验的 metadata 载荷。
- 新增 `application/io/plugin_dialogue_order.py`：新解析结果捕获证据；旧 artifact 仅从 SourceSnapshot 捕获字节派生，不读 live URI。缓存按插件摘要及提取版本隔离，最多四份图；损坏 metadata 诊断后重派生，取消不缓存部分结果。
- 新增 `application/io/dialogue_order_projection.py`：按来源和 Quest 的原槽位移动完整 DIAL，校验身份与记录归属；失败任务单独回退，完整来源投影后再选输出。
- 修改 `parser/plugin_parser.py::parse_plugin`：增加可选 `captured_content`，由保存内容完成解析和本地化标志读取；默认公开调用兼容。消除 adapter 先读字节、再读路径可能得到不同插件的二次读取问题。
- 修改 `application/io/legacy_adapters.py::SsePluginAdapter.parse`：传递同一捕获字节，委托新模块保存关系 metadata；保留既有 codec/schema，取消返回原取消结果。
- 修改 `application/projects/source_hydration_cache.py`：只读模型可携带小型证据；按调用启用，缓存键区分有/无证据，取消不走来源原文降级。
- 修改 `application/projects/categorized_upload.py`：使用保存来源证据，分别保留完整分类输入与当前版本实际输出；版本缺行不压缩显示编号，当前译文/状态沿用权威版本。
- 修改 `application/categorized_upload/models.py`：UploadSource 追加可选 `dialogue_order` 与 `classification_entries`，原三个位置参数兼容。
- 修改 `application/categorized_upload/classification.py`：完整输入先分类和编号，再按完整身份筛选并采用当前实际译文/状态；重复身份和未覆盖选择明确拒绝。
- 修改 `application/io/paratranz_classification.py`：复用来源基线及新投影；启用布局时枚举完整来源生成展示编号，不覆盖原 plugin.source_order。无可用证据沿用既有行为，分类名和路径规则不变。
- 修改 `application/projects/gui_facade.py`、`ui/operations/categorized_upload_bridge.py`：分类入口显式传递证据开关和既有取消 token；普通上传及下载来源读取不派生关系，仍在既有 worker 执行。保留 `_capture()` 无参数兼容。
- 修改 `converter/translation_entry_collection_export.py`：本地导出复用相同分类/选择路径，writer 保留完整来源编号。
- 修改 `scripts/convert_plugin_to_jsons.py`、`scripts/plugin_eet_to_json.py`：复用已解析插件证据，含空项完成布局后才按原选项筛选；EET 只提供译文，不提供排序。

新增回归：

- `tests/application/test_dialogue_order.py`：纯图策略、坏证据回退、来源隔离、无输出桥、循环、任意 Hashable/EntryKey 及大链/孤点。
- `tests/parser/test_dialogue_order_evidence.py`：对象/捕获字节一致、压缩/XXXX、坏字段/端点、软硬分层、旧成果、缓存隔离、有界性、取消与损坏 metadata 重派生。
- `tests/parser/test_plugin_parser_captured_content.py`：捕获字节权威、默认兼容和严格错误保留。
- `tests/dialogue_order_support.py`：无用户数据的真实小插件 fixture，包含反向物理 DIAL 和多响应 INFO。
- `tests/contracts/io/test_dialogue_order_projection.py`：身份、内部顺序、全源/子集编号、当前译文、wire/writer、下载匹配、普通路径及脚本空项筛选。
- `tests/application/projects/test_dialogue_order_publication.py`：实际工程保存重开、原路径删除、旧成果无 metadata、按需读取/缓存和取消不降级。
- `tests/paratranz/test_dialogue_order_file_retry.py`：正式执行器和 HTTP gateway 实际 413 分片、部分失败、receipt 补传；同一 INFO 跨分片仍保序，纯译文请求只有 key/translation/stage。
- 更新 `tests/application/projects/test_file_source_performance.py`：只读模型字段合同增加可选证据，普通读取仍不保留图或源字节，内存预算断言保持。

文档：最小更新 `docs/requirements.md`、ADR-047、`docs/dev/paratranz-categorized-upload.md`、S28 计划与索引；新增专项 QA 报告并登记本增量。既有大型 adapter/facade 仅薄接线的责任尺寸例外与再拆分条件见专项报告。

## 真实来源验证

使用既有保存成果，只读核验 64,998 条、1,491 个分类、1,480 个对话任务。1,473 个正常布局、370 个任务实际移动块；7 个任务因 4 条跨任务 TCLT 保守回退，涉及 835 条，未将回退统计成优化。
全部身份、基础上下文、原文、来源序号、块内 INFO/响应与正常布局的跨 SCC 边检查通过。DA10 的 126 个 key、63 个 DIAL、候选指标与选择结果精确等于用户接受的冻结 v3。
其他样本按插件结构选择，覆盖最大规模、无边、分叉、循环、无输出桥与多响应；具体 ID、数量、取舍和回退原因见专项报告。

验证没有读取 XML 或访问网络，没有修改真实项目数据。本轮不重新计算 XML 相似度，不以旧参考评分描述其他任务效果。单次完整核验约 57.44 秒，不是多轮性能基准。

## 最终验证命令与结果

使用仓库既有 uv 环境与 lockfile，未升级依赖。以下 PowerShell 命令在仓库根目录执行；缓存/TEMP 定向本轮临时区。

```powershell
$env:UV_CACHE_DIR = 'D:/MyCode/TransBridge/qa-tmp-dialogue-order-20261028/uv-cache'
$env:TEMP = 'D:/MyCode/TransBridge/qa-tmp-dialogue-order-20261028'
$env:TMP = $env:TEMP
$uploadTests = @(Get-ChildItem tests/ui/operations -Filter '*.py' | Where-Object { $_.Name -match '^test_(categorized_upload|file_upload)' } | ForEach-Object { $_.FullName })
uv run --no-sync pytest tests/application/test_dialogue_order.py tests/application/projects tests/contracts/io tests/parser tests/scripts/test_categorized_conversion.py tests/paratranz/test_file_upload_retry.py tests/paratranz/test_file_publication.py tests/paratranz/test_dialogue_order_file_retry.py tests/persistence/test_source_artifact_store.py tests/persistence/v2/test_source_artifact_archive.py tests/ui/test_project_source_hydration.py tests/contracts/paratranz/test_project_download_use_case.py @uploadTests -q --basetemp=.tmp-do-final --tb=short --deselect=tests/parser/test_plugin_parser_failures.py::test_strict_parse_preserves_loading_exception_and_default_compatibility --deselect=tests/parser/test_plugin_parser_failures.py::test_strict_parse_accepts_valid_plugin_without_translatable_strings --deselect=tests/persistence/v2/test_source_artifact_archive.py::test_pure_record_legacy_import_preserves_translation_without_reading_reference --deselect=tests/contracts/io/test_legacy_format_adapters.py::test_agent_eet_tool_preserves_source_snapshot_in_created_slot
uv run --no-sync pytest tests/contracts/io/test_legacy_format_adapters.py::test_agent_eet_tool_preserves_source_snapshot_in_created_slot -q --basetemp=.tmp-do-native --tb=short
uv run --no-sync ruff check src tests scripts/convert_plugin_to_jsons.py scripts/plugin_eet_to_json.py
uv run --no-sync ruff format --check src tests scripts/convert_plugin_to_jsons.py scripts/plugin_eet_to_json.py
git diff --check
```

- 最终主回归：**1,014 passed、1 skipped、4 deselected**，74.28 秒，退出码 0。跳过项为本环境不可创建目录符号链接。
- 单独 EET 测试：沙箱拒绝临时文件替换；获准在普通本地权限下仅重跑该项，**1 passed**，退出码 0。与主回归不重叠，合计 **1,015 项通过**。
- 余下 3 项为既有失败：两个 strict parser fixture 使用不存在的 test.esp，与已有文件头检查冲突；legacy archive 旧断言读取已不存在的 read_only。独立 Agent 以 HEAD 的旧生产源码在内存隔离加载后仅执行这三项，真实复现 **3 failed**，没有 checkout 或改写工作树，详见专项报告。
- 首轮扩大回归 8 failed/1,010 passed/1 skipped 为中间结果。本次可归属的新增只读字段断言及 `_capture()` 参数兼容已修复；长临时路径的 Windows 路径错误用短自有目录复验通过。早期默认 TMP/uv cache 权限失败也已用任务目录重试，不混同代码失败。
- 全库及两个脚本 Ruff 检查通过；格式检查 2,327 个文件通过（其中 src/tests 2,325 个）；diff 空白检查通过。未将先前重叠批次相加。

## 兼容、留存与未执行项

无需迁移工程或重新导入旧来源，原始来源排序和匹配字段不变；已有 ParaTranz 展示需按正常分类原文/上下文更新流程刷新，纯译文操作不刷新编号。本轮未进行真实云端写入、构建/发布、游戏运行、人工桌面验收、跨插件加载覆盖或全仓 pytest。

本轮测试目录与工具缓存按精确自有路径清理；用户要求留存的临时核对材料及验证摘要供后续查看，未纳入 Git。正式有用结论已提炼至本记录和专项报告。未提交或推送。

本次接入无未完成项。上述 3 项既有测试失败、真实云端与跨插件验证范围仍明确保留，不据此声称整个仓库或全部游戏剧情已验收。
