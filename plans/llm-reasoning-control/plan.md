# LLM 请求级思考控制实施计划

- **状态**：已完成（2026-08-27）
- **日期**：2026-08-27
- **对应架构**：[ADR-029](../../docs/adr/029-request-scoped-llm-reasoning-control.md)

## 目标与非目标

目标是在不修改全局 LLM 配置的前提下，让 AI 翻译默认优先直接回答，并让严格流程中真正进入 LLM 裁决的疑难条目优先使用低强度思考；程序自动探测当前协议、地址和模型可用的控制方式并持久化结果。

本次不增加 Provider/模型能力表，不增加用户设置，不改变智能助手自身 LLM 运行时，不保证供应商内部绝对没有隐藏推理，也不改变标准校对每批一次业务调用的合同。

## 当前事实与约束

- `src/transbridge/infra/llm_client.py` 只实现 OpenAI-compatible 与 Anthropic 两种协议，Prompt cache 通过同一客户端注入与降级。
- `AutoTranslator` 是翻译、批量翻译、FOMOD 和部分工具翻译的共同入口；独立/混合校对另有组装路径。
- `ProofreadPipeline` 的严格策略目前给质量检测、修复、润色和裁决传入同一个客户端；`PostProcessor` 已先用本地规则筛出需要 LLM 裁决的条目。
- 智能助手的 `_workflow_llm_runtime.py` 直接组装 Provider、并发限制和日志包装，必须继续保持 `INHERIT`。
- ADR-028 要求 combined 校对每个业务批次恰好一次 LLM 调用，不能插入第二个业务判断阶段。

## Story 1：版本化能力缓存与单飞初始化（已完成）

**验收标准**

- 能力键只包含协议、规范化地址、模型和探测版本，不包含 API Key。
- 结果以独立 JSON 原子持久化；损坏、过期或未来版本记录安全降级为未命中。
- 同一进程内相同键并发初始化只执行一次，其他调用等待同一结果。
- 支持运行时失效，失效后后续作用域可重新探测。

**文件落点与步骤**

- 新增 `src/transbridge/infra/llm_reasoning.py`，定义意图、状态、能力记录、存储、单飞管理器和请求作用域包装器。
- 默认缓存路径使用 `get_data_dir()`；测试注入临时路径和探测器，不访问真实网络。

**测试**

- 新增 `tests/infra/test_llm_reasoning.py`，覆盖无凭据持久化、缓存命中/过期、损坏文件、并发单飞和失效。

## Story 2：Provider 协议探测与正式请求注入（已完成）

**验收标准**

- OpenAI-compatible 只探测有限协议字段；非法值被静默接受时不判定支持，明确校验后再验证合法关闭值。
- Anthropic 的直接回答映射为省略扩展思考参数；低强度不可证明时保守降级。
- Prompt cache 降级保留思考控制字段。
- 正式请求明确拒绝缓存机制时，能力失效并以继承语义重试一次；其他异常原样上抛。
- 未实现探测扩展的假客户端保持原调用行为。

**文件落点与步骤**

- 在 `src/transbridge/infra/llm_client.py` 为既有协议实现最小的探测、参数映射和带控制调用扩展；公共 `chat`/`chat_stream` 保持兼容。
- 让 `llm_reasoning.py` 的包装器负责意图解析、后台预热、缓存使用、降级和取消转发。

**测试**

- 扩展 Provider 客户端测试，验证三类 OpenAI-compatible 控制、Anthropic 省略语义、缓存参数合并、流式/普通请求和拒绝回退。

## Story 3：AI 翻译作用域与疑难裁决接线（已完成）

**验收标准**

- `AutoTranslator` 的翻译和术语请求使用 `PREFER_DIRECT`。
- UI 独立/混合校对的普通阶段使用 `PREFER_DIRECT`；严格策略的 LLM 裁决客户端使用 `PREFER_LOW`，且只处理现有规则无法裁决的条目。
- combined 校对不增加业务调用，智能助手 `_workflow_llm_runtime.py` 不安装思考包装器。
- 并发预算、暂停、取消和日志包装顺序保持有效。

**文件落点与步骤**

- 在 `AutoTranslator` 组装 Provider 后、并发限制前安装直接回答作用域。
- 为 `ProofreadPipeline.create` / `PostProcessor.register_default_checkers` 增加可选裁决客户端，保持默认回退到现有客户端。
- 在 `polish_runtime.py` 与 `_mixed_worker.py` 从同一 Provider 客户端派生 direct/low 两个作用域，再分别套用共享预算和日志。

**测试**

- 增加翻译组装、独立/混合校对和 ProofreadPipeline 聚焦测试，断言意图路由、共享预算与 combined 调用数不变。

## 依赖、风险与回退

Story 1 是 Story 2 的基础，Story 2 是 Story 3 的基础。主要风险是兼容网关静默忽略字段以及后台探测与首个请求竞争；非法哨兵握手和单飞等待分别约束这两类风险。删除作用域包装调用即可恢复原行为，删除能力缓存文件可重新初始化，不涉及配置迁移。

明确假设：OpenAI-compatible 服务若不对候选字段进行可观察校验，就视为无法证明支持；`PREFER_LOW` 无稳定映射时允许降级为已证明的直接回答或继承。

## 验证结果

- 新增能力、协议与路由测试 23 项通过；既有 Provider cache、校对流水线、翻译合同、FOMOD 与 UI/智能助手相关回归 140 项通过，共 163 项。
- `uv` 的仓库 Python 环境因本机解释器路径失效无法启动；改用可用的系统 Python 3.13 执行 pytest。Ruff 未安装在该解释器中，因此静态格式命令未能执行；已完成编译检查、120 字符行宽检查和 `git diff --check`。
