# ParaTranz 安全部分下载与可操作复核

- **日期**：2026-10-20
- **Epic / Story**：`paratranz-sync-operation-ux` / S05～S07
- **计划**：[plan.md](../../../../plans/paratranz-sync-operation-ux/plan.md)
- **需求**：[FR22.3](../../../requirements.md)

## 问题与用户可见结果

原下载窗口遇到任何歧义就阻断整次同步，提示查看明细却没有处理入口。现在可靠匹配的条目可以先更新；未确定的条目保持本地内容和身份不变。用户通过“处理待处理项…”对照本地与云端内容，明确选择具体候选、逐项或批量保留本地，也可继续暂缓。

应用复核选择会重新检查预计变化，正式下载仍通过主按钮确认。完成安全更新后保留剩余项的处理入口；已成功应用的选择会移除，避免下一轮被旧选择阻断。全冲突不再显示“已是最新”；本地保留、云端未采用、暂缓和执行失败分开统计。

## 实际文件增量

下列路径均相对仓库根目录。没有删除或移动生产文件。工作区同时存在其他功能的未提交修改，本记录仅归属下列同步行为及明确关联的文档段落。

### S05：下载匹配和执行

- **新增** `src/transbridge/application/sync/download_review.py`：冻结人工决定、候选和复核行，选择令牌关联作用域及本地/候选快照，提供过期决定异常。
- **新增** `src/transbridge/application/sync/download_planner.py`：通过唯一远端引用、兼容的唯一普通键以及原文/上下文证据匹配；歧义进入复核；重复 ID、其他本地条目占用的引用不允许采用；共享候选不被多个本地条目抢占。
- **新增** `src/transbridge/application/sync/local_candidate.py`：按计划中的候选摘要和引用取准确来源，使用列表保留重复与暂缓条目，拒绝对重复本地身份执行更新。原文参与身份的已有条目保留本地原文和上下文。
- **修改** `src/transbridge/application/sync/planner.py`：下载接入独立规划器，统一计划摘要组装；原文限定条目下载后按译文、状态和引用判定一致，避免再次预检重复更新。
- **修改** `src/transbridge/application/sync/models.py`：为计划增加仅用于复核的默认字段，不把业务正文加入通用计划序列化、摘要或日志表示。
- **修改** `src/transbridge/application/sync/use_case.py`：创建计划请求传递冻结的下载决定。
- **修改** `src/transbridge/application/sync/__init__.py`：导出新增下载决定接口。
- **修改** `src/transbridge/application/sync/executor.py`：下载冲突记录为暂缓而非失败；安全项沿用原子提交；候选构建交给独立模块；重试保留暂缓计数。上传及双向同步冲突语义保持原行为。

执行器仍超过 500 行；本轮责任审查保留已有授权、重试和事务调度，新匹配及候选聚合职责已独立。后续增加执行模式或持久化策略前，需要拆分既有事务端口和结果组装，不继续向执行器加入新职责。

### S06：可操作窗口

- **新增** `src/transbridge/ui/operations/sync_review_view.py`：无 Qt 的复核 DTO，包含候选可选性及禁用原因。
- **新增** `src/transbridge/ui/operations/sync_review_model.py`：表格模型、搜索和原因/状态筛选；批量决定一次发出模型通知，避免数千项筛选重算。
- **新增** `src/transbridge/ui/operations/sync_review_dialog.py`：本地/云端原文、译文、上下文和标识对照；候选需明确选中后采用；多选或筛选范围批量保留、恢复暂缓；取消不改请求。
- **新增** `src/transbridge/ui/operations/sync_preflight_presentation.py`：独立生成同步检查的主按钮状态及用户提示。
- **修改** `src/transbridge/ui/operations/paratranz_dialog.py`：连接复核入口、决定重检、忙碌锁定和完成后继续；只接受服务端重检返回的有效选择；未知、失败或取消的结果先重新检查。主模块保持 500 行以内。
- **修改** `src/transbridge/ui/operations/sync_result.py`：显示完成及暂缓数量，暂缓不再冒充执行失败。

### S07：生产适配与数据保护

- **新增** `src/transbridge/ui/operations/sync_review_projection.py`：决定编解码、领域复核行投影、中文原因及下载影响统计。
- **修改** `src/transbridge/ui/operations/preflight_view.py`：预检结果增加默认复核数据字段。
- **修改** `src/transbridge/ui/operations/mappers.py`：传递领域复核数据到界面预检结果。
- **修改** `src/transbridge/ui/operations/production_support.py`：请求与目标校验携带人工决定；提交前再检查活动工程/版本；原样保留跳过和暂缓条目的元数据、修订和外部引用。
- **修改** `src/transbridge/ui/operations/paratranz_sync.py`：下载歧义不再阻断安全项；过期决定清除并要求重检；切换远端目标、本地工程或命名空间清除选择；人工决定进入确认摘要；仅下载请求具有决定字段，保持上传 schema 兼容。

### 回归测试

- **新增** `tests/contracts/paratranz/test_download_resolution.py`：19 项参数化后测试，覆盖可靠匹配、重复本地保全、手动精确候选、保留本地、陈旧决定、候选占用/共享、远端独有歧义、同内容引用绑定、重试和序列化边界。
- **修改** `tests/contracts/paratranz/test_sync_plan_confirmation.py`：原文限定身份的原有“上传/双向跳过”断言保留；下载改由新增匹配合同验证。
- **新增** `tests/ui/operations/test_paratranz_download_review_flow.py`：6 项生产适配回归，覆盖部分执行后人工处理、摘要变化、过期清除、切换工程、不可采用的候选及 legacy 原文限定身份的实际提交和再次零更新。
- **新增** `tests/ui/operations/test_sync_review_dialog.py`：12 项 Qt 回归，覆盖明确选择、字面显示、筛选多选/批量、取消、不可用候选，以及 2,181 行批量处理的一次模型通知。
- **修改** `tests/ui/operations/test_paratranz_sync_dialog.py`：覆盖安全项可执行、全冲突指引、决定重检、取消、忙碌保护、结果后继续、成功选择清理和异常结果重检。
- **修改** `tests/ui/operations/test_paratranz_download_authority.py`：扩展为 4 个参数组合，以真实 V2 权威工程验证安全下载、暂缓后精确候选更新及保存重开；包括云端独有记录。
- **修改** `tests/ui/operations/test_production_support_authority.py`：新增暂缓条目完整元数据保留，以及相同内容下工程切换仍拒绝旧提交的验证。

### 当前规范与索引

- **修改** `docs/requirements.md` 的 FR22.3：补足安全项先执行、可操作复核、暂缓保全、真实统计及继续处理约束；该文件的其他并行修改不归属本记录。
- **修改** `docs/adr/045-original-qualified-plugin-entries.md` 的同步身份策略段落：下载使用可靠证据/显式选择，保留参与身份的原文；上传保持既有约束。
- **修改** `plans/original-match-entries/plan.md` 的 S05 当前策略：引用本次下载闭环，历史完成证据保持原样。
- **修改** `plans/paratranz-sync-operation-ux/plan.md`：校准修复前事实、部分执行与复核交互，增加并完成 S05～S07，记录责任审查及验证结果。
- **修改** `plans/INDEX.md` 对应行和 `docs/changelogs/INDEX.md`：仅更新本 Feature 状态及新增记录链接。

## 验证证据

均使用仓库现有 Python 3.12.12 环境；`--no-sync --no-cache` 避开受限的用户缓存，不安装依赖，不改动锁文件。

```text
uv run --no-sync --no-cache pytest tests/contracts/paratranz tests/ui/operations tests/smart_assistant/tools/test_paratranz_sync_plan.py tests/smart_assistant/tools/test_paratranz_tools.py -q -p no:cacheprovider --basetemp D:\MyCode\TransBridge\.tmp-paratranz-pilot-root-complete
```

最终结果：**283 passed, 1 skipped, 3 warnings，11.49 秒**。跳过项为未配置真实账户的 ParaTranz 集成测试；警告为既有 SWIG 类型弃用提示。此前扩展回归发现上传 schema 多出下载字段，已限定为下载后复验通过。

```text
uv run --no-sync --no-cache ruff check src tests
uv run --no-sync --no-cache ruff format --check src tests
```

全库检查通过；全库格式检查有 1 个无关文件需要格式化：`tests/ui/tools/smart_assistant/test_request_lifecycle_panel.py`。该文件属于同时进行的助手工作，未擅自修改。同步相关目录的格式检查单独通过：

```text
uv run --no-sync --no-cache ruff format --check src/transbridge/application/sync src/transbridge/ui/operations tests/contracts/paratranz tests/ui/operations
git -c core.safecrlf=false diff --check -- src/transbridge/application/sync src/transbridge/ui/operations tests/contracts/paratranz tests/ui/operations docs/adr/045-original-qualified-plugin-entries.md plans/paratranz-sync-operation-ux/plan.md plans/original-match-entries/plan.md
```

中文复核窗口已用合成条目离屏渲染并检查布局。2,181 项在“待处理”筛选下批量保留有 Qt 回归，模型只发一次批量变更通知，避免原先逐行刷新造成长时间阻塞。

本轮专用 `.tmp-paratranz-pilot-root-v2`、`.tmp-paratranz-pilot-root-regression`、`.tmp-paratranz-pilot-root-final`、`.tmp-paratranz-pilot-root-complete`、`.tmp-paratranz-pilot-review.png` 在核对绝对路径和任务归属后清理；未删除其他工作中的临时目录。

## 兼容与遗留项

- 无存储格式迁移。V2 来源结构继续由本地来源管理；同步只更新可对应的译文状态及关联，不自动导入云端独有来源。
- 对不能安全选择的候选说明原因并允许保留本地或暂缓；不允许强制覆盖重复身份或抢占已有引用。
- 本次没有连接或修改用户实际 ParaTranz 项目，没有运行全库 pytest，也没有构建安装包；验证范围为上述套件。
- 功能范围内无已知阻断遗留项；全库格式检查的无关文件状态如上。未提交或推送 Git。
