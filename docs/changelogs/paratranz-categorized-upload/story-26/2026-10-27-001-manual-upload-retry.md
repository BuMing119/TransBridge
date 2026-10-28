# S26：上传结束后手动补传未完成文件

日期：2026-10-27（Asia/Shanghai）。
Epic：`paratranz-categorized-upload`；Story：S26。
关联：[实施计划](../../../../plans/paratranz-categorized-upload/plan.md)、[FR3.4.2](../../../requirements.md)、[ADR-047](../../../adr/047-opt-in-categorized-project-upload.md)、[使用说明](../../../dev/paratranz-categorized-upload.md)。

## 原因与范围

用户实际批量上传出现 54 个网络传输异常，文件回执均为待核实。原流程对结果未知的写入不自动重放，
再次点击普通上传会处理当前勾选的全部文件，缺少仅补传未完成项的入口。
用户明确要求：等本轮全部结束后，由用户点击补传按钮；因此保留现有自动重试边界，增加手动补传。

本记录涵盖本次对话已完成的补传实现、测试及后续文档补齐。工作区已有大量工程恢复、下载、AI 和写回改动，
仅下述符号和段落归本 Story；同文件其他差异不归入本次。既有 S01～S25 增量文件未改写。

## 实现与逐文件增量

- 修改 [application/file_upload.py](../../../../src/transbridge/application/file_upload.py)：
  `FileUploadReceipt` 增加带默认值的 `retry_files`。执行器保留失败、待核实、取消、跳过及本地准备失败的文件；
  `_publish_parts` 保留实际失败分片的路径和对应输入，排除成功及服务端确认无变化的文件和分片。
- 修改 [categorized_upload_bridge.py](../../../../src/transbridge/ui/operations/categorized_upload_bridge.py)：
  `CategorizedUploadSession.retry_upload` 通过共享互斥入口执行；会话保留上轮选择、固定内容及剩余文件，
  补传重新读取文件列表、按当前完整路径定位。列表失败时保留输入；捕获前取消/失败按原选择重新捕获。
  新回执替换剩余项；诊断记录 `retry` 和本次来源/分类范围。生产窗口注入补传回调。
  本文件既有配置修订与显式目标适配差异不归本 Story。
- 修改 [categorized_upload_dialog.py](../../../../src/transbridge/ui/operations/categorized_upload_dialog.py)：
  `UploadReceipt` 增加默认 `retry_count`，窗口增加可选 `retry_upload` 回调和“补传未完成文件（数量）”。
  运行中隐藏按钮并阻止重入；本轮结束后有剩余项才显示，补传成功后隐藏，错误后可再点。
  补传沿用既有 worker、进度和取消生命周期，状态区分“上传”与“补传”。
- 新增 [test_file_upload_retry.py](../../../../tests/paratranz/test_file_upload_retry.py)：
  验证仅重传失败的 413 分片及原行、权限失败后的未尝试文件、取消前零网络请求和本地校验失败范围。
- 新增 [test_categorized_upload_retry.py](../../../../tests/ui/operations/test_categorized_upload_retry.py)：
  验证普通/分类未知创建后复用云端文件、不重传成功项、列表失败保留固定译文、取消后继续、
  工程/版本/账号变化拒绝网络操作、按钮时机以及与当前勾选/策略隔离。
- 修改 [test_categorized_upload_dialog.py](../../../../tests/ui/operations/test_categorized_upload_dialog.py)：
  现有真实 worker 测试同时覆盖读取、上传和补传回调均在后台运行，以及补传完成态。
- 修改 [test_upload_mode_routing.py](../../../../tests/ui/operations/test_upload_mode_routing.py)：
  模式启动替身提供补传回调，并断言生产对话框获得该回调；该文件其他既有路由修改不归本 Story。

## 文档与索引增量

- 修改 [requirements.md](../../../requirements.md) 的 FR3.4.2：记录用户确认的手动触发、精确剩余范围、
  固定输入/策略、分片恢复、后台取消和仅窗口内保留边界。
- 修改 [ADR-047](../../../adr/047-opt-in-categorized-project-upload.md) 的应用层回执与重试契约：
  结构化 `retry_files` 决定补传范围，现有文件 API、共享节流和未知写入不自动重放规则保留。
- 修改 [plan.md](../../../../plans/paratranz-categorized-upload/plan.md)：增加 S26 验收清单、实现落点、
  验证与兼容边界，更新当前状态，保留 S25 及此前阶段。
- 修改 [paratranz-categorized-upload.md](../../../dev/paratranz-categorized-upload.md)：
  更新日期、规范的执行恢复段落及代码入口，说明按钮、结果、输入尚未捕获、确定性错误和关闭窗口后的行为。
- 修改 [plans/INDEX.md](../../../../plans/INDEX.md)：仅更新本 Epic 的当前阶段与 S26 记录入口。
- 修改 [docs/INDEX.md](../../../INDEX.md)：仅更新 ADR-047 的功能摘要。
- 修改 [docs/dev/INDEX.md](../../../dev/INDEX.md)：更新该模块及 `CategorizedUploadDialog` 的补传职责。
- 修改 [docs/changelogs/INDEX.md](../../INDEX.md)：增加本记录入口。

## 验证

前一实现轮已取得下列同一集合的 133 项通过证据；本次文档同步后再次执行：

```powershell
uv run pytest tests/paratranz/test_file_publication.py tests/paratranz/test_file_upload_retry.py tests/ui/operations/test_categorized_upload_bridge.py tests/ui/operations/test_categorized_upload_dialog.py tests/ui/operations/test_categorized_upload_retry.py tests/ui/operations/test_upload_mode_routing.py tests/ui/operations/test_categorized_upload_checkpoints.py tests/ui/operations/test_file_upload_production.py -q
uv run ruff check src tests
uv run ruff format --check src tests
```

结果：**133 passed，3 个既有 SWIG 弃用警告，7.71 秒**；没有排除或跳过用例。
全库 Ruff 检查通过，格式检查通过（2250 个 Python 文件）；各命令退出码为 0。
两轮复验为同一集合，不相加计数。测试使用受控 HTTP 响应、真实本地工程和 Qt worker，不访问真实云端。
文档相对文件链接、涉及源码/测试路径和目标差异空白在记录完成后单独核验。

实现过程中组合 Qt 测试曾提前退出；将真实 worker 验证合并到现有窗口测试的 QApplication 生命周期后，
上述完整组合通过。本轮补文档未再修改业务代码或测试。

## 兼容性与剩余项

无持久化迁移、依赖或 lockfile 变更；默认参数保持既有回执和对话框调用兼容。
补传只在当前窗口内保存剩余项，关闭后不恢复；新普通上传替换补传范围。
当前选择/模式变化不修改旧轮输入；需要采用新内容和策略时开始普通上传。
权限不足、仅译文模式下云端缺文件、本地输入无效等原因仍需先解决，补传不绕过原约束。
部分文件内译文分批失败仍以整个文件为未完成单位，不宣称词条级恰好一次或原子并发保护。

遗留修复项：无。未执行全量 pytest、构建/打包或新按钮的真实账号验收；本次为文档补齐与本地复验，
没有再次操作真实云端。此前用户单独授权的人工补传成功，不能替代新按钮的线上验收。
