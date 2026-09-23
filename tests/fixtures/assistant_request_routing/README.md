# 请求路由验收语料与回放

`corpus.json` 是人工编写的合成中文多轮情境。包含补充约束、进度追问、明确要求跟踪的分析、取消换任务、等待确认时插话、引用取消命令、歧义目标、路由期间版本变化和多指令，以及问候、完成后的感谢、普通问题和问候混合工作；另有 3 个仅用于回放的协议边界场景。

`history` 通过生产 `routing_messages()` 的限量近期聊天投影进入模型背景，不能成为当前 ingress 或执行权限。实际输入使用已有请求快照和当前输入；终态候选遵循生产筛选规则。`visible_requests` 模拟模型看到旧版本、程序应用时版本已更新的竞态；`requests` 是实际应用时的权威状态。语料目前是多轮场景的单次路由截面，不覆盖后台调度、工具执行、审批点击或整段对话的端到端模型表现。

`synthetic_captures.json` 是人工编写的控制输出，明确标记 `origin=synthetic`。它们验证回放器及生产校验边界，不是已采集的真实模型回复，也不证明模型能正确识别中文意图。

## 离线回放

```powershell
uv run python scripts/evaluate_assistant_requests.py --replay tests/fixtures/assistant_request_routing/synthetic_captures.json
```

每个输出经真实 `LlmTurn` / `parse_control_turn` / `parse_proposal` / `apply_proposal`，比对动作、目标、程序回执、原请求是否被意外修改，以及新请求的归属、条目类型和来源。`expected` 不进入模型输入。独立指令按输入中的顺序验收；语义上允许的其他拆分方案应先经人工审阅，再更新语料，不能看到模型输出后自动接受。

捕获记录绑定生产路由提示、工具 schema、来源输入及当前请求状态的摘要。修改这些内容后，旧记录会失败，需重新采集；不能只重新计算真实模型记录的摘要来宣称重新验证。

## 显式采集真实模型输出

仅在操作者明确准备好发送合成语料时运行以下命令。使用现有 `LLMClient`，不访问桌面配置或系统凭据库；凭据通过环境变量 `TRANSBRIDGE_EVAL_API_KEY` 提供，不要写入语料或命令行参数。

```powershell
uv run python scripts/evaluate_assistant_requests.py --live --provider openai_compatible --model YOUR_MODEL --output $env:TEMP/transbridge-routing-capture.json --require-live
uv run python scripts/evaluate_assistant_requests.py --replay $env:TEMP/transbridge-routing-capture.json --require-live
```

可使用 `--base-url` 指定 OpenAI 兼容接口，或 `--provider anthropic` 使用现有 Anthropic 客户端的默认端点。Anthropic 客户端不支持自定义 `--base-url`，同时提供会明确报错。输出路径必须尚不存在。仅给模型路由控制工具，不执行任何业务工具；不采集用户翻译文件。输出保留模型文本和完整控制参数，人工审阅后再决定是否留档，默认建议存入临时目录。API 调用失败会保留已采集记录并返回 2，错误报告只写异常类型，不输出 SDK 异常全文或凭据。

`origin` 是记录者声明，摘要只能校验输入一致性，不能证明真实供应商来源。记录包含模型、供应商和采集时间；请保留可信的采集过程。合成结果与模型结果分开计数，缺失结果标记 `not_captured`。只有全部 13 个可调用场景都具有通过的真实模型记录时，`live_acceptance` 才为 `passed`；3 个协议边界场景不计入真实模型通过率。2026-09-13 因生产入口增加聊天分流及近期背景，重新生成合成输入摘要与四个新场景；未采集真实模型输出。

退出码：0 为已提供记录的回放通过；1 为校验失败或 `--require-live` 未满足；2 为未采集、命令参数错误或采集故障。不带任何模式运行只列出未采集结果，绝不自动联网。本轮未执行真实模型采集。
