# S09：助手按需补输入、原请求恢复与保存原文合入

日期：2026-10-29（Asia/Shanghai）。Epic：application-preflight；Story：S09。
关联：[计划](../../../../plans/application-preflight/plan.md)、[ADR-050](../../../adr/050-application-preflight-and-optional-source-reference.md)、[需求](../../../requirements.md)、[桌面补输入 002](../story-05-to-08/2026-10-28-002-on-demand-source-inputs.md)。

## 原因与边界

用户追问后确认，AI 助手工具链没有经过 AI 翻译窗口的补输入入口。未知原文可能以空展示载体进入操作；有真实保存原文但无 artifact 的助手成果，又会因未知 context 被序列化为空字符串而无法合入。S09 补齐助手实际交互及请求恢复，保留原请求、原范围和已成功事项，不要求重新发出整条自然语言请求。

不新增校验规则或通用修复总管。原始路径仍仅可选参考；原文、模板、输出、权限及工程完整性的既有保护保持。本记录只归属本轮助手追加，既有下载重构和 S01～S08 不重复记入；001、002 保持不变。

## 逐文件变化

生产路径相对于 `src/transbridge/`。

- 新增 `smart_assistant/tools/input_prerequisites.py`：从实际目标条目和来源读取已有原文/模板可用性，复用 application facts；返回带原工程、版本、请求、事项及条目范围的 `source_input_repair`。原完整 source scope 与待补来源分别保留，混合批量写回补齐后不丢原本可用的来源。
- 修改 `smart_assistant/tools/tool_translator.py` 的 `_launch`、`postprocess_task_run.py` 的实际选区准备：未知原文返回可操作 repair，已知保存原文继续工作；不先启动模型任务再发现缺输入。`tool_editor.py` 的查询结果增加 `original_known`，空载体不冒充真实原文。`tool_translator.py` 中下载成果描述的其他修改属于既有下载工作线，不归此记录。
- 修改 `smart_assistant/tools/_entry_upload.py`：在既有选区与 `requires_original_match` 排除后，仅检查真正待上传行，未知原文在任何远端写入前返回同一 repair；不改变原重复原文跳过规则或下载行为。
- 修改 `smart_assistant/tools/tool_writer.py`：既有缺 snapshot 错误接入模板 repair，保留原批量来源范围；工具 schema/说明明确无当前参考时需指定输出。`write_back_preparation.py` 默认输出只取当前 slot 的可选参考，参考清空后不从 snapshot URI 写回旧文件；显式输出目录仍可使用保存的文件名，缺目标保留 OUTPUT diagnostic。
- 新增 `application/assistant_requests/source_input_waiting.py`；修改 `admission.py` 的 `record_effect_outcome` 和 `dispatch.py` 的 `reconcile_dispatches`：仅识别专用错误及有效结构化 repair，使相应事项等待 `source_input_required`。effect/step 保留真实失败回执；普通失败、其他失败步骤和部分结果保持原语义。纯补输入 dispatch 不折叠为 `plan_failed`，解除等待后也不由旧 dispatch 再阻断。
- 新增 `application/assistant_requests/source_input_selection.py`：人工映射保存到执行范围证据，原始 ingress 不改。prepare 原子保存映射、解除原事项该等待原因并取得新许可；调度忙或持久化失败保留原等待。范围绑定原 receipt、请求修订、事项、来源、原选择摘要及完整 EntryKey；空手动选区使用真实失败目标，模板条目身份未改变时无需虚构映射。已保存映射可跨同修订的暂停/恢复/重启保留，执行仍需新许可；确认显示失败可用 `readmit_source_input_confirmation` 重新准入同一映射。
- 修改 `smart_assistant/request_scope.py`：消费上述范围证据并收窄实际 collection，未映射行、新基线额外条目及同 local key 的其他来源不进入重试；执行版本回执保留映射证据，旧执行许可仍拒绝。
- 新增 `ui/tools/smart_assistant/source_input_binding.py`：在 GUI 主线程展示真实补输入按钮，调用共享 coordinator；补后重新生成原操作确认卡和 tool call，不复用旧许可，不用模型文本代替恢复。原文只检查本次目标；取消、忙状态、确认保存前后失败均可保留当前编辑并重试。临时许可明确转交或释放，关闭/过期回调不执行到新目标。
- 修改 `ui/tools/smart_assistant/chat_composition.py`：单工具结果接补输入动作；`plan_execution_binding.py`：计划步骤结果经 GUI dispatch 接同一动作；`chat_widget.py`：关闭补输入 binding；`request_background.py`：界面已销毁时仍执行资源 settled 清理；`session_workspace.py`：正确读取 callable/property 形式的投影分歧状态，修复真实主窗口的 bound method 被误当作恒真问题。
- 修改 `ui/coordinators/source_input_coordinator.py`：提供实际来源/条目映射，补齐后恢复原范围；忙时通知调用者，来源补齐期间结合请求是否仍有效处理过期回调，不给 worker 增加 Qt 交互。
- 修改 `application/preflight/inputs.py` 增加 `SOURCE_CONTEXT_KNOWN`；`application/projects/entry_source_text.py` 与 `ui/saved_translation_slots.py` 仅对 saved-only 读模型标记未知 context。真实来源解析的空 context 仍是已知空值，不能一概放宽 `None`。
- 修改 `application/translation/task_result_recovery.py` 的成果序列化/恢复、`smart_assistant/tools/translation_task_results.py` 的捕获与成果证据、`translation_task_merge.py` 的合入：保存并恢复明确的 context-knownness。缺 artifact 但有真实保存原文可完成 capture/save/merge；旧记录无标记时保持原保护，显式 context、locator 和内容身份变化仍受保护。
- 新增 `smart_assistant/tools/task_source_identity.py`：冻结来源稳定 key、可选参考和保存名称；动态词库复用 GUI 的 reference-or-stable-key 策略。`translation_task_results.py` 和 `postprocess_task_run.py` 向报告 worker 传递轻量来源描述，使报告生成与列举目录一致，不持有来源 bytes。先前 `tool_proofreader.py` 的保存名称目录改动仍属于 001，不重复归属。
- 新增 `tests/application/assistant_requests/test_source_input_selection.py`、`tests/smart_assistant/test_source_input_waiting.py`、`tests/smart_assistant/tools/{test_saved_source_task_inputs,test_source_input_prerequisites}.py`、`tests/ui/tools/smart_assistant/test_source_input_repair.py`；修改 `tests/smart_assistant/tools/test_write_back_options.py` 增加输出/repair 范围回归。`test_paratranz_tools.py` 仅新增 `test_unknown_original_requests_input_before_any_remote_write` 归本轮，其原下载测试改动不归本轮。
- 更新本 Epic plan、ADR-050、FR8.4/26.8 与直接相关索引：助手遗漏由审计转为本地实现与实际调用链验证，plan 草稿、ADR 提议标签仍保留。

## 验证证据

以下分批有重叠，不合并为唯一测试总数。GUI 使用 `QT_QPA_PLATFORM=offscreen`。

请求应用层及真实 gate/回执组共 289 项，285 通过，4 项仅沙箱硬链接权限失败；授权定向复跑 4 通过、21 deselected。随后共用请求流最终组 49 通过，覆盖等待、解除等待、新 scheduler 许可重试、旧失败回执保留及其他失败仍失败。

```powershell
uv run --no-sync pytest tests/application/assistant_requests tests/smart_assistant/test_request_execution.py tests/smart_assistant/test_source_input_waiting.py -q --basetemp .tmp-ai-pilot/req --tb=short
uv run --no-sync pytest tests/smart_assistant/test_request_execution.py -q -k saved_result_query_ignores_missing_selection --basetemp .tmp-ai-pilot/req-link --tb=short
uv run --no-sync pytest tests/smart_assistant/test_source_input_waiting.py tests/smart_assistant/test_step_receipt_concurrency.py tests/smart_assistant/test_workflow_closure_audit.py tests/smart_assistant/test_plan_startup_failure.py -q --basetemp .tmp-ai-pilot/flow --tb=short
```

输出、写回发布/撤销与上传边界最终组在已授权环境运行，79 通过，无真实网络；混合批量 repair 保留全范围的最后调整后，写回选项模块 27 项再次全部通过。

```powershell
uv run --no-sync pytest tests/smart_assistant/tools/test_write_back_options.py tests/smart_assistant/tools/test_safe_write_back.py tests/smart_assistant/tools/test_write_back_publication_boundary.py tests/smart_assistant/tools/test_write_back_request_undo.py tests/smart_assistant/tools/test_paratranz_tools.py -q --basetemp .tmp-ai-pilot/output --tb=short
uv run --no-sync pytest tests/smart_assistant/tools/test_write_back_options.py -q --basetemp .tmp-ai-pilot/output-scope --tb=short
```

最终来源映射、保存原文任务及成果恢复兼容组共 152 项，151 通过，1 项 fixture 的 Windows `os.replace` 瞬时 WinError 5；该参数单独重跑通过。此前长临时路径造成 archive 环境失败，改用独占短路径 `.tmp-asel` 后通过对应范围。此组沿用硬链接发布所需授权环境，无网络或 LLM。

```powershell
uv run pytest tests/application/assistant_requests/test_source_input_selection.py tests/smart_assistant/tools/test_saved_source_task_inputs.py tests/smart_assistant/test_request_execution.py tests/smart_assistant/tools/test_translation_task_lifecycle.py tests/smart_assistant/tools/test_postprocess_task_lifecycle.py tests/application/projects/test_known_original_consumers.py tests/application/translation/test_task_recovery_results.py tests/application/translation/test_task_recovery.py tests/application/translation/test_legacy_task_recovery.py tests/ui/tools/test_recovery_retry_results.py tests/persistence/v2/test_task_recovery_archive.py --basetemp=.tmp-asel -q
uv run pytest 'tests/application/assistant_requests/test_source_input_selection.py::test_stale_or_unrelated_repair_does_not_change_request_or_ingress[epoch]' --basetemp=.tmp-asel -q
```

助手输入/来源 coordinator/既有请求面板组 89 通过。最终 helper、真实 GUI 与 coordinator 组 23 通过（7 + 6 + 10），无 skip/deselect 或授权复跑；最终组覆盖后来新增的混合批量范围案例。

```powershell
uv run --no-sync pytest tests/smart_assistant/tools/test_source_input_prerequisites.py tests/ui/test_on_demand_source_inputs.py tests/ui/tools/smart_assistant/test_request_session_scope.py tests/ui/tools/smart_assistant/test_request_lifecycle_panel.py -q --basetemp .tmp-as/p13
uv run --no-sync pytest tests/smart_assistant/tools/test_source_input_prerequisites.py tests/ui/tools/smart_assistant/test_source_input_repair.py tests/ui/test_on_demand_source_inputs.py -q --basetemp .tmp-as/p15
```

GUI 使用真实 MainWindow、AppContext、助手 Panel、ToolHandler、RequestService/gate、确认卡与 SourceSupplementService；fixture 实际解析、保存 artifact 并重开工程。模拟文件/来源菜单选择、映射对话框接受及既有权限框确认，工具确认使用真实 Qt 按钮点击。LLM 配置为 offline，任务启动替换为记录目标并返回成功，不执行真实 AI。四种真实窗口分支为正常、确认保存前失败、确认保存后失败、关闭；另两项验证忙时可重试及不阻断未选未知行。核对首次真实工具失败等待、取消后译文保留、补后新 tool call/新许可、确认前未启动、确认后仅映射目标启动一次、原 ingress 不变及关闭许可释放。

最终 `uv run --no-sync ruff check src tests` 全部通过；`uv run --no-sync ruff format --check src tests` 报告 2299 files already formatted。主会话 `git diff --check` 退出码 0，仅 CRLF 转换提示；文档收尾另执行差异与本地链接检查。

## 遗留与未执行项

- 本轮范围内无待修项。未跑全仓 pytest，未进行真实用户工程、真实 LLM、网络、人工桌面或性能基准验收；上述 offscreen 真窗口集成不等同人工桌面验收。
- 002 中已证实的两个 HEAD 既有源码字符串合同失败仍未混修；不宣称全仓测试全绿。002 后工程聚合 278 项中 276 通过、同两项失败的收尾事实已记入 plan。
- 动态词库原 stem 缓存到 stable-key 的自动迁移仍未实施，不承诺本机缓存随工程包迁移。
- 各创建者核实绝对路径后已清理 `.tmp-ai-pilot/`、`.tmp-as/`、`.tmp-assistant-source-fix/`、`.tmp-assistant-selection-fix/`、`.tmp-asel/`，均确认不存在；未读取或删除既有 `.transbridge/`。
