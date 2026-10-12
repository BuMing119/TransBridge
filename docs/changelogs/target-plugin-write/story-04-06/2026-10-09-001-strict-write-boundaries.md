# 收拢严格写回入口与跨插件套用边界

- 日期：2026-10-09
- Epic / Story：[target-plugin-write / S04～S06](../../../../plans/target-plugin-write/plan.md)
- 需求：普通、批量、助手和 FOMOD 写回不能采用单文件跨插件套用的跳过策略；按用户确认方案执行 bm-pilot。

## 行为与边界

只有 GUI 单文件显式选择新 ESP 模板时允许逐项保留并报告未匹配内容。普通和批量写回始终使用各自来源快照；现有输出文件只是目的地。定位失败阻止整份文件发布，不静默丢条目。批量可以部分文件成功，但全失败、取消及真实部分提交仍保留逐文件状态、全部诊断和恢复目录。

批量预检汇总插件和 Strings 的实际输出路径，拒绝跨来源冲突及路径别名；提交时再次检查，冲突批次不会先写出任何文件。执行使用已确认的冻结术语投影。

FOMOD 保持新版解析、迁移/TM/AI 决策和冲突阻断逻辑；插件写回必须与新版冻结集合完整对应，再经过暂存、保真校验和提交。插件及全部候选伴随文件的存在状态、字节指纹和路径指向在解析前固定，解析后、执行前及提交时复核。预先存在的未使用伴随文件保留；读取或翻译期间新增、改变伴随文件则阻断。临时工作副本不生成会混入成品包的长期备份，原始归档仍作为恢复来源。

单文件覆盖确认不允许覆盖运行期间的外部更改。助手保留真实发布结果、备份和产物；即使发布后的撤销凭证写入失败，也能看到已产生的文件，任务不报告成功。

## 逐文件变更

- 修改 `src/transbridge/ui/workbench/cards/write_card.py`：移除 Plugin/EET/XT 直接 writer、全版本临时切换集合等回退。三个公开写回入口统一走计划 facade，缺来源上下文时明确提示。DSD 保留为独立导出按钮。
- 删除 `src/transbridge/ui/workbench/cards/write_views.py`：旧回退专用的四个对话框已无调用方，不保留死分支。
- 修改 `src/transbridge/ui/operations/write_feature.py`：批量缺失任一来源证据即拒绝；聚合输出冲突预检及提交复核；执行复用 `checked_batch` 的冻结投影。
- 修改 `src/transbridge/ui/operations/write_batch.py`：新增全输出规范路径查重；保留成员 PARTIAL 的真实产物、失败/取消计数和原始诊断。全失败或取消的逐文件回执存于唯一的 `WRITE_BATCH_RESULTS` 结构化诊断，遵守既有 OperationResult 契约。
- 修改 `src/transbridge/ui/operations/runtime_adapter.py`：统一 `operation_object_results` 读取成功 payload 或失败/取消批次回执。
- 修改 `src/transbridge/ui/operations/write_dialog.py`：显示逐文件状态、恢复目录及部分提交产物提示。
- 新增 `src/transbridge/fomod/plugin_publishing.py`：负责新版快照冻结、严格集合校验、取消与最终源复核，委托 HydratedWrite 发布；提取独立职责，避免继续扩张超过 500 行的 pipeline。
- 修改 `src/transbridge/fomod/pipeline.py`：新来源准备改用冻结快照，`_write_back` 委托严格发布；本轮未改变已有 AI 身份处理及候选决策。
- 修改 `src/transbridge/application/io/publish/coordinator.py`：渲染后无条件检查目标变化，最终提交守卫内再次核对目标路径及指纹；冲突保留验证和备份证据。
- 修改 `src/transbridge/smart_assistant/tools/tool_writer.py`：保留完整发布 outcome、artifacts、diagnostics、counts 及原始结果；发布后凭证失败保留已提交证据。保留此前已有的 `technical_error` 处理。
- 新增 `tests/ui/operations/test_strict_write_routes.py`（19 项）、`test_batch_output_conflicts.py`（4 项）：真实插件严格路由、整文件失败、冻结投影、批次终态 UI 与输出冲突。
- 新增 `tests/application/fomod/test_plugin_publishing.py`（24 项）：真实 inline/localized 发布、身份/定位不符、失败保真、取消、伴随文件变动、空白原文以及最终 ZIP 无发布临时物。
- 新增 `tests/smart_assistant/tools/test_write_back_publication_boundary.py`（8 项）：输出路径非模板、缺失来源、整文件失败、部分发布和发布后凭证故障。
- 修改 `tests/integration/publish/test_atomic_publish.py`：覆盖策略参数化的渲染期间变化、提交内竞态回归；不重复归属本文件此前的保真修复。
- 修改 `tests/contracts/io/test_localized_strings_adapter.py`、`test_original_match_migration.py`：FOMOD 测试改走正式冻结来源准备，保留本地化、锁定阶段和同键不同原文验收。
- 修改 `tests/smart_assistant/tools/test_safe_write_back.py`：移除对已删除卡片私有符号的 mock，继续断言不能直接调用 writer。
- 修改 `docs/requirements.md` 的 FR4、`plans/target-plugin-write/plan.md`，最小更新计划及 changelog 索引。

本记录不重复归属 S01～S03、之前日志/保存/空白定位修复，也不包含工作区原有的依赖、安装包、AI、助手其他模块及其他需求变更。

## 验证

```powershell
uv run --no-sync pytest tests/integration/publish tests/contracts/io -q
```

344 passed、1 skipped；跳过原因是当前 Windows 环境不允许创建测试符号链接。初轮发现两条旧 FOMOD 测试缺冻结来源证据，更新到真实准备链后复验通过。

```powershell
uv run --no-sync pytest tests/ui/operations tests/smart_assistant/tools/test_safe_write_back.py tests/smart_assistant/tools/test_write_back_publication_boundary.py tests/smart_assistant/test_tool_consolidation.py tests/ui/test_workbench_theme_migration.py tests/contracts/tasks/test_operation_task_adapter.py -q
```

最终 165 passed，包含最后补充的全部输出冲突检查。

```powershell
$fomodTests = @(rg --files tests -g 'test_fomod*.py')
uv run --no-sync pytest tests/application/fomod @fomodTests tests/contracts/io/test_localized_strings_adapter.py tests/contracts/io/test_original_match_migration.py -q
```

最终 181 passed，包含最后补充的预存在伴随文件和解析期间新增伴随文件回归。其中 38 项 I/O 契约与第一组重叠，三组去重后为 652 passed、1 skipped。

```powershell
uv run --no-sync ruff check src tests
uv run --no-sync ruff format --check src tests
git -c core.safecrlf=false diff --check
```

全部通过；1592 个 Python 文件格式符合要求。测试仅报告既有依赖/兼容 API 弃用警告。未遗留本轮临时文件；保留工作区原有 `.tmp_tests`、`qa-tmp-s03`。

## 兼容性与未验证项

不新增批量套用、助手套用工具或正式 MCP/CLI 写回能力；DSD 独立导出保留。项目 schema 无变化，无数据迁移。缺少来源上下文的旧写回入口明确拒绝，不再尝试直接覆盖。

未运行全仓库所有测试、真实 LLM/FOMOD 联网流程或安装包构建；未使用朋友原始 Neiva ESP 现场复验。没有提交或发布安装包。范围内实现无未完成项。
