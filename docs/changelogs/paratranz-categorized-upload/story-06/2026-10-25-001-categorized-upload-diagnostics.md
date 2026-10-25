# 分类上传对照与执行诊断日志

- 日期：2026-10-25
- Epic / Story：`paratranz-categorized-upload` / S06；同步 S04 当前入口的文档说明
- 计划：[plan](../../../../plans/paratranz-categorized-upload/plan.md)
- 需求：[FR3.4 / FR3.4.1](../../../requirements.md)
- 决策：[ADR-047](../../../adr/047-opt-in-categorized-project-upload.md)
- 开发与排查：[分类上传说明](../../../dev/paratranz-categorized-upload.md)
- 类型：诊断补齐、离线回归与文档同步；不包含实际上传故障修复

## 问题与行为

用户报告分类上传后全部失败或结果待核实。原流程的执行器将异常转换为逐项回执，后台 worker 因正常返回而不记录异常；应用日志主要可见 HTTP 请求结果，无法确定创建文件、写入词条或本地关联保存中的实际失败点。HTTP 200 及响应字节数不足以确认文件已正确建立。

现在单独和批量分类流程共用 `PARATRANZ_CATEGORIZED_UPLOAD` 单行事件，记录会话、操作和可用计划身份、选择范围、对照统计、每个分类文件的执行结果及异常。HTTP 响应只投射有界结构与诊断字段，保留错误中的可用状态码和请求编号，排除凭据和翻译载荷。捕获后转为回执的失败仍留下具体操作及堆栈。

`phase_finished` 不表示业务全部成功；`execution_summary` 的回执计数保留失败、结果待核实、取消和待处理的区别。服务器正常返回但缺少文件身份时，日志能连接创建请求、响应结构、业务异常和最终结果待核实回执；没有增加请求、重试或修改上传策略。

当前分类入口已按本会话先前用户修正整合进单独/批量上传，默认普通模式。此次文档同步清除计划索引的“独立分类入口”旧描述；初版增量文件保持历史原样，不重写当时实现，也不将先前模式路由开发计为本次日志代码改动。

## 逐文件变化

以下只记录本会话可归属的日志实现及接续文档整理。工作区已有分类功能及其他未提交修改；Git 未跟踪状态不代表其全部内容均为本增量新增。

- **新增 `src/transbridge/application/categorized_upload/diagnostics.py`**：`diagnostic_scope` 管理关联身份和凭据作用域；`log_event` 输出脱敏单行 JSON；`log_failure` 保留异常类型、操作及堆栈，并移除错误内嵌响应正文；`log_plan` / `log_receipt` 统计实际计划和回执。
- **修改 `src/transbridge/application/categorized_upload/planner.py`**：`prepare_plan` 记录云端快照及逐文件/总体计划，不重复读取快照。
- **修改 `src/transbridge/application/categorized_upload/executor.py`**：`execute_plan` 包装执行作用域及最终回执；原执行体和词条操作在计划校验、建文件、写词条、移动、更新及关联保存失败处记录上下文，保持原回执及写入顺序。
- **新增 `src/transbridge/paratranz/categorized_response_diagnostics.py`**：`response_summary` / `error_response_summary` 有界投射诊断字段、文件身份和集合数量；不可解析正文仅记类型及长度。
- **修改 `src/transbridge/paratranz/categorized_upload.py`**：`_request` 调用原客户端一次并记录响应或异常；GET 正常响应使用 DEBUG，写请求使用 INFO；`create_file` 标记目标路径及提交条目数。原空 JSON 建文件载荷及单次尝试策略不变。
- **修改 `src/transbridge/ui/operations/categorized_upload_bridge.py`**：`CategorizedUploadSession` 建立 `session_id`，为本地预览、对照和执行附带工程、Variant、单独/批量范围及凭据作用域，记录选择和预览统计。
- **新增 `tests/application/categorized_upload/test_diagnostics.py`**：验证对照/执行关联与实际统计，覆盖七类失败操作、取消、过期、部分完成、作用域释放及凭据脱敏。
- **修改 `tests/paratranz/test_categorized_upload_gateway.py`**：增加 HTTP 200 无文件身份到最终结果待核实的端到端受控回归、响应字段投射与凭据/翻译载荷排除、HTTP 错误状态及请求编号测试；核验未发生额外请求。
- **修改 `tests/ui/operations/test_categorized_upload_bridge.py`**：验证真实工程会话的预览、对照、执行共享会话身份，并保留独立操作编号和相同计划身份。
- **修改 `docs/requirements.md`**：新增 FR3.4.1，将分类对照、逐文件执行、异常关联及诊断安全纳入验收；其余既有修改不归入本记录。
- **修改 `docs/adr/047-opt-in-categorized-project-upload.md`**：补充 UI、应用层与 HTTP 的诊断职责及结果判断边界，明确本地日志验证不代表线上故障修复。
- **修改 `plans/paratranz-categorized-upload/plan.md`**：补充 S06、文件落点、验收与本次 198 项验证；区分历史验证、当前文档接续及真实云端遗留项。
- **新增 `docs/dev/paratranz-categorized-upload.md`**：说明当前模式入口、整理选项、日志位置、事件字段、关联方法、结果解释、响应投射及故障排查步骤。
- **修改 `docs/dev/paratranz.md`**：链接当前分类说明，区分文件工作流接口与当前 GUI 路由，纠正打印 Token、无条件重试及未分类 RuntimeError 的过时描述；未修改客户端实现。
- **修改 `docs/INDEX.md`、`docs/dev/INDEX.md`、`plans/INDEX.md`、`docs/changelogs/INDEX.md`**：只补本工作线的 ADR、开发说明、当前状态与本增量入口。
- **新增本记录**：归档日志改动、实际验证及剩余问题，不改写初版增量文件。

## 验证证据

以下代码验证在本会话“先补齐日志”阶段实际完成。后续“文档也要补齐”阶段只编辑 Markdown，复用这些证据，不声称再次运行或全仓 pytest 通过。

使用仓库既有 `uv` 环境与锁定依赖，临时设置：

```powershell
$env:UV_CACHE_DIR = 'D:\MyCode\TransBridge\.tmp-categorized-logging-qa\uv-cache'
$env:TMP = 'D:\MyCode\TransBridge\.tmp-categorized-logging-qa'
$env:TEMP = $env:TMP
$env:QT_QPA_PLATFORM = 'offscreen'
```

最终相关回归（退出码 0，**198 passed**，11.46 秒）：

```powershell
uv run pytest tests/application/categorized_upload tests/application/projects/test_categorized_upload.py tests/paratranz/test_categorized_upload_gateway.py tests/ui/operations/test_categorized_upload_bridge.py tests/ui/operations/test_categorized_upload_dialog.py tests/ui/operations/test_upload_mode_routing.py tests/ui/test_logging_setup.py tests/contracts/paratranz/test_sync_execution_logging.py tests/contracts/paratranz/test_sync_diagnostics_logging.py -q
```

出现 3 条既有 SWIG 类型 `__module__` 缺失的 DeprecationWarning，无测试失败。该集合含分类原有行为、模式路由及普通同步诊断回归，不是 198 项全部新增。此前阶段性 108 项、71 项结果与该集合重叠，不相加。

最终静态检查（退出码 0）：

```powershell
uv run ruff check src tests
uv run ruff format --check src tests
```

检查通过，**2132 个 Python 文件**格式合格。最后只对两个日志相关文件修正格式后完成静态复验，没有改变测试通过时的业务逻辑。日志阶段临时目录已清理。

文档接续阶段对照任务开始时的文档副本审查增量，核对新增/修改行中的本地链接及代码路径、事件名称、索引回链和编号唯一性，并执行：

```powershell
git -c core.safecrlf=false diff --check -- docs/requirements.md docs/adr/047-opt-in-categorized-project-upload.md docs/dev/paratranz.md docs/dev/paratranz-categorized-upload.md docs/dev/INDEX.md docs/INDEX.md docs/changelogs/INDEX.md docs/changelogs/paratranz-categorized-upload plans/INDEX.md plans/paratranz-categorized-upload
```

`git diff --check` 退出码 0。未跟踪新文档另做行尾空白和相对链接检查，避免仅依赖 `git diff` 漏查新文件；共核对 10 份文档、22 个新增/修改行链接、10 个代码路径、15 个事件名称及 S06 编号，全部通过。

## 兼容与遗留

- 上传 payload、空 JSON 建文件方式、远端身份与内容核验、取消和回执语义不变；无数据迁移或 schema 变更。
- 需要重启应用加载新代码；既有应用日志不会追补缺失事件。
- 用户实际上传失败原因仍未确认，须依据新日志定位。模拟 HTTP 200 无身份用于验证日志，不代表已获知真实服务器响应正文。
- 真实云端空 JSON 建文件、移动及历史保留尚未验收；本次未调用真实账号上传、删除、移动或修改云端词条。
- 未运行全仓 pytest、打包或桌面真实上传验收。历史初版扩大回归中记录的基线失败不在本次“已修复”声明内。
- 未提交、推送或发布；工作区其他修改保持原样。
