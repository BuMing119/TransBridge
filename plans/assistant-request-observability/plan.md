# 请求生命周期过程记录与对话验收

- 状态：已实现（2026-09-12）；3/3 Story 完成，离线回归通过，未执行真实模型采集。
- 关联：[FR30](../../docs/requirements.md#fr30智能助手用户请求生命周期与长会话上下文)、[ADR-040](../../docs/adr/040-assistant-user-request-lifecycle.md)、[请求生命周期主计划](../assistant-user-request-lifecycle/plan.md)。

## 目标与边界

让维护者和用户能查看请求为什么等待、恢复或结束，并用真实对话场景检查模型提案经过程序校验后的实际归属。此次交付持久化过程记录、只读查看、合成验收集和录制结果回放；独立派生摘要服务留在主计划 S06，不在本轮顺带实现。不自动联网评测、不读取或导出用户会话及凭据。

## 当前约束与实施选择

- 请求写入已统一经过 RequestService.transact；事件与请求状态在同一 Session 修订发布，不靠 UI 日志或提交后的另一次写入猜测成功。
- 事件是诊断证据，不是命令，不用于恢复重放；原始消息与路由提案仍保留在原记录中。事件只保存引用、状态差异和受控诊断信息，避免重复整段用户内容与工具结果。
- CAS 重试以重新读取的状态计算事件；重复命令或任务回调无状态变化时不追加重复事件。明确记录轮次接纳及拒绝时可追加独立事实。
- 新字段为 schema 4 的 assistant_state 内部可选字段；旧会话无事件不补造历史，首次变更以后开始记录。关闭和删除仍由现有业务屏障决定，日志不能解除等待。
- RequestService 原有职责较多，事务提交抽到独立 transactions.py；事件构造、投影和读取在 journal.py，轮次接纳及失败证据在 turns.py。职责复核后 RequestService 486 行、RequestBinding 489 行，界面查看与模型评测各自独立。

## Story 与阶段进度

- [x] 范围与当前代码核对。
- [x] S01：事务内事件及因果引用。
  - 新增 application/assistant_requests/journal.py、transactions.py；最小修改 service.py、请求轮次与后台结果入口。
  - 覆盖输入接纳、提案及校验、请求状态/等待、事项/执行结果、轮次、确认、恢复和删除意图。
  - 验收：落盘失败无成功事件；重复命令不增事件；CAS 保留竞争写者事件；重开可查；后台 A 的事件不能归到当前 B；分页不漏事件；日志不成为模型输入。
- [x] S02：用户可见只读过程。
  - 新增 request_timeline_view.py，修改 request_list_view.py、request_management_binding.py 和请求绑定接线。
  - 展示中文操作、前后状态和等待原因，诊断详情按需查看；提供请求级及会话级入口，覆盖尚未成功创建请求的路由失败。
  - 验收：查看不暂停、不恢复、不触发模型；纯文本呈现；旧会话空状态、分页、读取失败和会话切换可解释。
- [x] S03：对话语料与可回放评测。
  - 新增专责 evaluation 模块、scripts/evaluate_assistant_requests.py、合成场景与说明、针对性测试。
  - 保存 provider-neutral 输出，实际经过控制协议解析和路由归约，再检查请求动作/归属/程序结果；输入摘要绑定录制结果，陈旧记录拒绝套用。
  - 覆盖追加约束、进度追问、新问题、取消换任务、确认插话、引用取消、歧义、错误修订、多指令。
  - 合成回归与真实模型采集分别计数；没有真实录制不能报告真实准确率。可提供显式 live 入口，但本轮不调用外部 API。
- [x] 聚焦测试、相关集成回归、Ruff 与最终差异复核。

## 现有能力收尾（2026-09-12，用户要求暂不新增功能）

- [x] 核对现有上下文、确认恢复与状态归约，不新增独立摘要服务。
- [x] 修复必需上下文绕过请求归属检查，以及小于默认预览阈值的结果无法按预算缩短；来源/修订/回答证据用于请求归属，路由控制批次不进入业务上下文。
- [x] 修复确认恢复选错请求/事项、旧确认消费和失效租约检查，保留独立等待原因；缺少身份或 epoch 过期的旧确认作废重提议。
- [x] 修正无对应等待原因时 unblock 意外重置事项状态。
- [x] 同步暂缓摘要的当前范围、运行针对性及相关集成回归。

## 验证与风险

实际先运行 journal/事务与请求仓库聚焦测试，修复内存元组和落盘列表造成的事件误判；独立审查修复澄清 JSON 诊断码、人工任务控制来源、模型标签可能复制正文等问题，新增回归。随后请求核心、任务中心及 Qt 相关测试 206 项通过。

最终综合命令：

```powershell
uv run pytest tests/smart_assistant tests/ui/tools/smart_assistant tests/application/sessions tests/application/assistant_requests tests/persistence/v2 tests/persistence/test_assistant_transcript_store.py tests/persistence/test_session_request_storage.py tests/contracts/test_task_runtime.py tests/contracts/test_task_runtime_backends.py tests/integration/bootstrap tests/config tests/ui/test_ui_settings_dialog.py -q -m 'not llm'
```

结果：**1319 passed, 34 warnings in 36.90s**；警告为既有 Swig/兼容 API 弃用。新增 journal 20 项、评测 32 项、过程 UI 9 项测试包含其中，不另行累加。

`uv run ruff check src tests`、`uv run ruff format --check src tests` 通过（1309 个文件格式合规）；评测脚本另跑 Ruff check 和 format --check，均通过。`git -c core.safecrlf=false diff --check` 与相关文档本地链接检查通过。

离线 CLI：`uv run python scripts/evaluate_assistant_requests.py --replay tests/fixtures/assistant_request_routing/synthetic_captures.json`。12 个合成场景通过，真实结果 0/9，`live_acceptance=not_passed`；加 `--require-live` 返回 1，阻止把合成结果当成真实通过。语料是带多轮背景的单次路由截面，不覆盖整段模型对话的端到端准确率；入口与限制见[语料说明](../../tests/fixtures/assistant_request_routing/README.md)。

`uv run python scripts/benchmark_assistant_requests.py --samples 20`：Python 3.12.12 / Windows 11，真实临时 repository、10,000 条消息、100 个请求。上下文组装 P95 66.739 ms，后台接纳 P95 240.693 ms，历史保存 P95 71.349 ms；相同历史保存样本 Qt 最大心跳间隔 54.372 ms。基准与综合回归存在运行时间重叠，不能据此与前轮样本计算精确性能回归比例；也不代表冷启动、有变化的全部保存或故障路径延迟。

事件随 Session 原子持久化，长会话会增加存储体积；当前不截断诊断历史，分页限制展示量。按结构化差异记录，避免每次保存完整请求或工具正文。真实模型语义准确率和大规模生产数据下延迟仍需后续测量，不能以合成测试代替。

本计划实施时未调用真实模型、未新增依赖、未迁移用户数据、未修改历史增量、未提交或推送。此后用户授权补齐摘要及长期存储能力，最新实现与验证见[主计划](../assistant-user-request-lifecycle/plan.md)；此前“暂缓”只描述该收尾阶段范围，历史增量保持原记录。

### 现有能力收尾验证（2026-09-12）

修复请求上下文的必需材料绕过归属过滤、中等长度工具结果无法缩短、续轮误占其他请求租约，以及解除审批等待意外重置失败/运行事项。确认记录增加准确事项、确认身份与授权 epoch；恢复展示保留审批等待，批准/忽略时原子消费，写失败不安装执行 gate。旧确认缺少身份或 epoch 失效时重新提议，不重放旧操作。

相关集成回归分两组执行：

```powershell
uv run pytest tests/smart_assistant tests/application/sessions tests/application/assistant_requests tests/persistence/v2 tests/persistence/test_assistant_transcript_store.py tests/persistence/test_session_request_storage.py tests/contracts/test_task_runtime.py tests/contracts/test_task_runtime_backends.py -q -m 'not llm'
uv run pytest tests/ui/tools/smart_assistant tests/integration/bootstrap tests/config tests/ui/test_ui_settings_dialog.py -q -m 'not llm'
```

分别为 **1156 passed, 34 warnings in 23.07s** 和 **179 passed, 3 warnings in 11.19s**；警告为已有弃用提示。上下文/归约新增用例先复现失败后通过；Qt 验证恢复 A 的输入不混入 B、续轮等待不占用 B 租约、恢复卡片仍需批准、独立资源等待保留、忽略确认消费和持久化失败无执行。

随后补齐“窗口切换回来后旧卡片不能借新租约获权”的会话 epoch 校验与 1 项回归，运行 `uv run pytest tests/ui/tools/smart_assistant/test_request_lifecycle_panel.py tests/ui/tools/smart_assistant/test_confirmation_lifecycle.py -q`，**46 passed**。这些测试与上一组有重叠，最终相关唯一测试为 1336 项，不能把 46 项直接累加。最终 `uv run ruff check src tests`、`uv run ruff format --check src tests`、`git -c core.safecrlf=false diff --check` 和相关文档链接检查通过。

本次未调用真实模型、未重新测量性能、未跑与修改无关的全仓库测试；原性能数据仅代表先前版本和已注明的测量条件。未创建任务专属临时文件。
