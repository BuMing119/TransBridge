# Story 07：术语工作台、Agent 与 MCP 等价入口

- **所属 Plan**：[项目术语库与 ParaTranz 备份/双向同步计划](../plan.md)
- **状态**：已确认
- **追溯**：FR5.17.1、FR5.17.4～FR5.17.6；ADR-016、ADR-019、ADR-021
- **前置依赖**：[Story 03](story-03-three-way-sync-planner.md)～[Story 06](story-06-ai-run-snapshot-echo-filter.md)
- **下游依赖**：S08 端到端/入口 parity 验证

## 目标

把同一套 terminology sync application use case接入术语工作台、Smart Assistant Agent和MCP。所有入口共享target resolution、preflight、plan hash、confirmation、TaskRuntime、retry/reconcile和inbound import语义；View和transport只做分页展示/参数映射，不实现第二套diff或直接调用raw terms API。

## 原始验收标准

- 术语工作台提供“备份已发布版本”和“双向同步”两个显式动作，显示当前 Project/Variant、本地 version、目标/账号/endpoint、映射状态和上次结果。
- preflight/plan 可分页展示上传、下载、冲突、跳过、有损映射和删除，删除/覆盖及 Variant 映射替换必须结果导向确认；计划 stale 后按钮失效并要求重新预检。
- 运行通过现有 TaskRuntime 展示连续进度、取消、partial/unknown、retry/reconcile；UI 主线程不执行网络、全量 diff 或 SQLite 大查询。
- 入站 change set 可从同步结果进入现有草稿复核；未发布前 UI 明确说明“不会影响当前翻译术语版本”。
- Agent/MCP 暴露同一 plan、authorize、execute、status/retry 和 inbound-import capability；写操作遵守现有 HITL/权限，返回与 GUI 同一 plan hash 和 counts。

## 当前入口与责任边界

- 术语工作台已有 `TerminologyUiServices`、`TerminologyPresenter`、`TerminologyWindow`和分页models；`window.py`已超过500行，sync UI必须拆成独立view/presenter/task adapter。
- `ProductionTerminologyComposition.services_for()`和`effective_adapter()`是当前composition seam；同步use cases应在production composition中注册并通过services facade取得。
- TaskRuntime已提供owner scope、cancel、progress、terminal guards；S04 task adapter是唯一执行入口。
- Smart Assistant工具由`tools.register_all_tools()`显式注册；现有`tool_paratranz.py`超过847行，新能力必须独立`tool_terminology_sync.py`。
- MCPAdapter投影ToolRegistry并通过同一工具中间件执行；不新增MCP专属网络/确认逻辑。

## 共享 application facade

计划新增 `TerminologySyncApplicationService` 或等价窄facade，暴露：

- `preflight(context, mode)`：返回Project/Variant/version/target/profile/mapping/capability diagnostics。
- `create_plan(context, mode)`、`page_plan(plan_ref, request)`：无副作用计划和分页。
- `issue_confirmation(plan_ref, owner)`、`submit(authorized_plan, owner)`：一次性确认与TaskRuntime job ref。
- `status/result(job_ref, actor)`、`retry(retry_token, actor)`、`reconcile(reconcile_ref, actor)`。
- `list_inbound/get_inbound/preview_import/commit_import`：S05 change set复核。

facade返回application DTO/`OperationResult`，不返回Qt model、ToolResult或JSON-RPC结构。

## GUI 交互顺序

```text
Versions/Overview context action
  → mode selection
  → async preflight
  → paged plan summary/details
  → conditional result-oriented confirmation
  → TaskRuntime progress/result
  → retry/reconcile or inbound review
```

“备份已发布版本”和“双向同步”是两个不同动作/说明；不能用一个模糊“同步术语”按钮再依赖隐藏配置判断模式。

## 依赖有序的实施步骤

1. 在application层新增/组合shared facade，确保GUI/Agent/MCP所有命令走同一RequestContext、owner、target resolver和plan repository。
2. 在`bootstrap/terminology.py`构造S01 remote terms service、S02 state、S03 planning、S04/S05 executor/import和facade；在composition registry注册稳定name/capability。
3. capability计算区分未配置凭据、未绑定、目标未验证、无published version、sync storage只读/损坏；不可用时不创建remote client请求。
4. 新建`sync_presenter.py`定义纯view state：summary counts、target/version/mapping、plan page、confirmation copy、result/diagnostics/retry/inbound navigation。
5. 新建`sync_view.py`，作为现有Overview/Versions area的上下文card/dialog/stacked child；`window.py`只挂载signal/view，不加入业务方法。
6. 新建`sync_task_adapter.py`订阅TaskRuntime event并转为presenter update；所有网络/plan/diff/query在worker/task中，主线程只处理有限DTO。
7. stale plan清除确认token并禁用执行；重新预检产生新plan。delete/overwrite/Variant mapping替换的确认文案展示具体counts、target和不可逆远端影响。
8. 双向结果提供“复核远端变化”入口，打开S05 paged change set；明确展示“尚未影响当前翻译术语版本”，commit import后仍显示“待发布”。
9. 新建`tool_terminology_sync.py`注册plan/execute/status/retry/reconcile/inbound preview/import工具；schema要求mode显式，破坏性execute必须confirmation token/HITL。
10. Agent工具把application result转`ToolResult`，MCP继续通过ToolRegistry投影；禁止在tool里实例化`ParatranzTermsAPI`或复制planner。
11. 对长计划只保存server/application plan ref并分页返回；Tool/MCP默认响应只含counts和前一页，不把5万item塞入context。
12. 确保application close释放remote service、TaskRuntime订阅和repository resource；窗口销毁只取消自己的UI订阅，不关闭共享runtime。

## 文件变更清单

- **新增** application facade（建议 `src/transbridge/application/terminology_sync/service.py`）。
- **修改** `src/transbridge/bootstrap/terminology.py`、`bootstrap/composition.py`：构造/注册use cases、capability和resources。
- **新增** `src/transbridge/ui/tools/terminology/sync_presenter.py`、`sync_view.py`、`sync_task_adapter.py`。
- **最小修改** `src/transbridge/ui/tools/terminology/window.py`、`presenter.py`、`shell_entry.py`：挂载和导航委托。
- **新增** `src/transbridge/smart_assistant/tools/tool_terminology_sync.py`。
- **修改** `src/transbridge/smart_assistant/tools/__init__.py`/工具注册清单；MCP transport通常无需业务修改。
- **新增** `tests/ui/tools/terminology/test_sync_presenter.py`、`test_sync_view.py`、`test_sync_task_adapter.py`。
- **新增** `tests/smart_assistant/tools/test_terminology_sync_tools.py`、`tests/contracts/terminology_sync/test_entrypoint_parity.py`。

## 边界条件与错误处理

- 未打开Project、无active Variant、无published version、未绑定/未验证、凭据缺失：显示明确prerequisite和修复入口，无网络写。
- target在preflight后变化：旧plan/storedconfirmation全部stale，不提交到新target。
- 计划含blocked/conflict时execute不可用；仅有non-destructive create时可不要求额外确认，但仍fresh-check。
- UI取消只请求TaskRuntime取消；不直接终止线程或丢弃unknown outcome。
- partial/unknown结果必须提供retry/reconcile，不能只显示“同步失败”。
- Agent/MCP owner/token不能跨会话重放；tool output/log不含credential或raw response。
- inbound import与publish是两个不同命令；工具描述不得暗示导入后已影响AI。

## 测试策略与建议命令

- presenter/view：prerequisite、两种mode、分页counts、lossy/conflict/delete、stale、confirmation、partial/unknown/retry和inbound文案。
- Qt responsiveness：slow remote/5万plan期间事件循环仍响应；window close释放subscription且任务按用户选择继续/取消。
- tool/MCP：相同fixture的plan hash/counts/action reasons一致；无confirmation写拒绝；owner/token重放失败。
- composition：capability可用/不可用、remote client resource关闭、无绑定时不构造网络调用。
- accessibility：按钮/plan rows/diagnostic的accessible name、focus、键盘操作和长文本裁切。
- 建议命令：`uv run pytest tests/ui/tools/terminology/test_sync_presenter.py tests/ui/tools/terminology/test_sync_view.py tests/ui/tools/terminology/test_sync_task_adapter.py tests/smart_assistant/tools/test_terminology_sync_tools.py tests/contracts/terminology_sync/test_entrypoint_parity.py -q`。

## 风险、回退与未决问题

- current terminology window职责已偏大；若挂载需要大量导航/状态修改，应先抽`sync_host`/area controller，不把同步状态塞进主window。
- Agent/MCP的交互确认能力可能受transport限制；应用层仍要求一次性confirmation/HITL，transport不支持时明确返回需在支持入口确认，而不是绕过。
- 回退可从capability registry移除sync入口并隐藏UI/tool；后台history/baseline/change sets保持只读。
