# 上传关联、云端来源保护与下载编辑上传往返

- 日期：2026-10-20
- Epic / Story：`paratranz-sync-operation-ux` / S08～S10
- 计划：[plan.md](../../../../plans/paratranz-sync-operation-ux/plan.md)
- 需求 / 决策：[FR22.3、FR22.4](../../../requirements.md)、[ADR-045](../../../adr/045-original-qualified-plugin-entries.md)
- 承接：[S05～S07 下载闭环](../story-05-to-07/2026-10-20-001-actionable-download-review.md)

## 问题与结果

下载已支持确定项先执行和人工复核，但上传仍按 key 匹配，原文限定身份被跳过；下载保存的远端关联未参与上传定位。已有条目的全字段上传可能改写云端原文和上下文，上传成功后的新关联也未保存到本地。

现在上传优先使用当前云端项目内已确认的远端 ID，支持云端改 key 和原文限定身份。已有记录只更新译文、状态，保留云端原文、上下文、key 和顺序。确定项先执行，冲突可选择具体目标、批量保留云端或暂缓，应用选择后重新预检再确认。

成功项的关联通过本地权威事务保存。部分失败、晚取消和关联保存异常保留远端成功事实及重试依据，不将其描述为整批未写入，也不因关联未保存而盲目重复新增。

## 文件增量

路径相对仓库根目录。工作区已有上轮下载及其他功能修改，本记录只归属以下上传增量；未删除生产文件。

### S08：领域匹配与执行

- 新增 `src/transbridge/application/sync/upload_review.py`：上传决定、过期异常，复用只读候选结构；`keep_remote` 表示本次保留云端。
- 新增 `src/transbridge/application/sync/upload_planner.py`：先按 ID、再按原文等证据定位；失效、占用、共享或重复标识进入复核；同文目标也建立关联；不能表达的本地独有冲突不盲目新增。
- 修改 `src/transbridge/application/sync/planner.py`：接入上传规划、拒绝跨方向决定；下载和独立双向合并保留各自语义。
- 修改 `src/transbridge/application/sync/models.py`：增加默认 `upload_review`，不把复核正文加入通用计划序列化。
- 修改 `src/transbridge/application/sync/use_case.py`：传递冻结的 `upload_decisions`。
- 修改 `src/transbridge/application/sync/__init__.py`：公开上传决定与异常接口。
- 新增 `src/transbridge/application/sync/remote_execution.py`：按精确引用校验与执行，验证返回 ID；已有记录上传只走译文/状态接口。
- 新增 `src/transbridge/application/sync/upload_associations.py`：整批保存已确认成功项的引用及修订，失败、取消或未执行项不绑定。
- 新增 `src/transbridge/application/sync/local_transaction.py`：从执行器提取完整事务端口和回调适配，保留提交前快照与一次性许可检查。
- 新增 `src/transbridge/application/sync/execution_result.py`：从执行器提取结果及重试投影；暂缓单列计数，关联批次失败保留远端成功项并标记 `association_pending`。
- 修改 `src/transbridge/application/sync/executor.py`：组合上述职责，确定项先执行，上传冲突记录为暂缓；重试补保存关联不重复远端新增。由上一轮 632 行降至 380 行。
- 修改 `src/transbridge/application/ports/paratranz.py`：增加 `update_entry_translation` 端口。
- 修改 `src/transbridge/paratranz/service.py`：现有部分 PUT 接口仅提交 `translation`、`stage`，校验返回 ID 和状态。依据仓库已有 API 包装器及 `update_entry_context` 的部分更新合同，未接触真实服务账户。

### S09：可操作复核

- 新增 `src/transbridge/ui/operations/sync_review_direction.py`：集中两方向的决定字段、保留方与动作文案。
- 修改 `src/transbridge/ui/operations/sync_review_view.py`：增加 `KEEP_REMOTE`，现有 DTO 兼容。
- 修改 `src/transbridge/ui/operations/sync_review_model.py`：按方向解释保留和候选动作，复用批量与筛选。
- 修改 `src/transbridge/ui/operations/sync_review_dialog.py`：上传明确表示向所选目标发送本地译文/状态，支持批量保留云端、恢复暂缓；不默认选候选。
- 修改 `src/transbridge/ui/operations/paratranz_dialog.py`：两方向决定隔离、重新预检、确定项先执行、完成后继续；异常结果先核验。主窗口 467 行。
- 修改 `src/transbridge/ui/operations/sync_preflight_presentation.py`：上传暂缓不再整批阻断或误报一致；分别显示更新、新增、保留和暂缓。
- 修改 `src/transbridge/ui/operations/sync_result.py`：明确远端部分成功不会自动回滚；关联提交失败用中文说明，原始诊断保留在详情，避免主界面重复英文。

### S10：生产链路与测试

- 新增 `src/transbridge/ui/operations/sync_preparation.py`：从主适配器提取远端目标检查、规划、过期选择清除与预检投影。
- 修改 `src/transbridge/ui/operations/paratranz_sync.py`：上传决定进入确认摘要，切换目标清除旧选择；复用 TaskRuntime 和权威本地提交。主模块降至 380 行。
- 修改 `src/transbridge/ui/operations/production_support.py`：冻结、解码上传决定，保持目标身份和修订保护。
- 修改 `src/transbridge/ui/operations/sync_review_projection.py`：增加上传候选、禁用原因和保留云端/暂缓统计。
- 新增 `tests/contracts/paratranz/test_upload_resolution.py`：30 项，覆盖精确匹配、原文身份、人工决定、过期/重复/占用引用、关联保存、部分失败、晚取消、重试、两字段 PUT 及异常回包。
- 修改 `tests/contracts/paratranz/test_sync_execution.py`：受控远端实现新的译文/状态端口。
- 修改 `tests/contracts/paratranz/test_sync_context_order.py`：上传保持云端顺序。
- 修改 `tests/contracts/paratranz/test_sync_order_refresh.py`：上传不刷新顺序，独立双向合并继续验证既有刷新与重试。
- 修改 `tests/contracts/paratranz/test_sync_plan_confirmation.py`：旧整体跳过断言仅保留给双向合并；上传由精确匹配合同覆盖。
- 修改 `tests/ui/operations/test_paratranz_sync_dialog.py`、`test_sync_review_dialog.py`：决定隔离、上传复核、批量、确认、全暂缓、继续处理及失败提示；合计 65 项通过。
- 修改 `tests/ui/operations/test_production_facade.py`：上传请求增加 `upload_decisions` 的 schema 期望。
- 新增 `tests/ui/operations/test_paratranz_upload_review_request.py`：3 项，覆盖摘要、候选变更及本地工程切换。
- 新增 `tests/ui/operations/test_paratranz_upload_roundtrip.py`：12 项真实 V2 命令与实际 service 的内存端点测试，覆盖下载、实际编辑、上传、保存重开；包括普通/原文限定、自动/手选、云端改 key、新增防重复和关联提交失败。

### 当前规范

- 修改 `docs/requirements.md` 的 FR22.3、FR22.4：上传部分执行、复核、字段保护和关联持久化；其他并行需求改动不归本轮。
- 修改 `docs/adr/045-original-qualified-plugin-entries.md` 与 `plans/original-match-entries/plan.md` 的当前同步策略；历史增量不修改。
- 修改 `plans/paratranz-sync-operation-ux/plan.md`：增加 S08～S10 及验证；仅联动 `plans/INDEX.md` 对应行及 `docs/changelogs/INDEX.md` 新链接。

## 验证证据

使用仓库 Python 3.12.12 环境；`--no-sync --no-cache` 避开受限缓存，不安装依赖。

```text
uv run --no-sync --no-cache pytest tests/contracts/paratranz tests/ui/operations tests/paratranz tests/smart_assistant/tools/test_paratranz_sync_plan.py tests/smart_assistant/tools/test_paratranz_tools.py -q -p no:cacheprovider --basetemp D:\MyCode\TransBridge\.tmp-paratranz-upload-pilot-final
```

最终 **461 passed, 1 skipped, 11 warnings，28.26 秒**。跳过真实账户测试，警告来自既有 SWIG 和 legacy 集合兼容入口。

```text
uv run --no-sync --no-cache ruff check src tests
uv run --no-sync --no-cache ruff format --check src tests
uv run --no-sync --no-cache ruff format --check src/transbridge/application/sync src/transbridge/application/ports/paratranz.py src/transbridge/paratranz/service.py src/transbridge/ui/operations tests/contracts/paratranz tests/ui/operations
git -c core.safecrlf=false diff --check -- src/transbridge/application/sync src/transbridge/application/ports/paratranz.py src/transbridge/paratranz/service.py src/transbridge/ui/operations tests/contracts/paratranz tests/ui/operations docs/adr/045-original-qualified-plugin-entries.md plans/paratranz-sync-operation-ux/plan.md plans/original-match-entries/plan.md
```

- 全库 Ruff 检查通过，本轮相关 81 个文件格式通过，目标 diff 无空白错误。
- 全库格式检查曾通过 1,935 个文件；后续并行工程创建修改后复查，`src/transbridge/ui/coordinators/project_creation_operation.py` 有一处待格式化，未擅自改动。
- 生产测试发现远端全成功、本地关联保存失败时原 `PARTIAL` 计数不合法而丢失回执；现计入独立关联批次失败，保留远端成功事实，复验通过。
- 独立审查发现超时或 PUT 后校验异常不能断言“远端未提交”；已改为无法确认、要求重新检查，并补异常回归。
- 合成数据离屏检查上传复核窗口：中文布局和方向文案正确，候选默认未选。

本轮根任务临时目录 `.tmp-paratranz-upload-pilot-regression`、`.tmp-paratranz-upload-pilot-final` 与截图 `.tmp-paratranz-upload-pilot-review.png` 已校验路径后清理，`Test-Path` 均为 False；协作者清理各自专用目录，未删除其他任务文件。

## 兼容与遗留

- 无数据格式迁移；本地身份和来源保持不变。新增项单独展示，云端独有内容保留。
- GUI/应用上传支持安全部分执行；独立 `BIDIRECTIONAL`、离线转换和旧单条上传工具未改为隐式三方同步。
- 远端逐项写入不能整批回滚；失败或取消后保留逐项事实并重新检查，未知网络结果不声称未写入。
- 功能范围内无已知阻断遗留项。未运行全库 pytest、未操作用户实际 ParaTranz、未构建安装包、未提交或推送 Git。
