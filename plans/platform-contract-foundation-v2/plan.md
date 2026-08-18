# Platform Contract Foundation V2

- **状态**：实现完成，综合 QA 通过（2026-08-18）
- **日期**：2026-08-18
- **需求**：FR17、FR18.8、FR20.1、FR22.5、NFR3.1、NFR4.1、NFR5.1、NFR6.1
- **架构**：ADR-016、ADR-012/013 的 2026-08-18 增量
- **问题**：R-001～R-012（R-007～R-012 的公共基础）、R-040 的共享脱敏边界
- **依赖**：无；其稳定公共合同被其余 V2 Plan 依赖

## 目标与边界

建立安装态包身份、公共请求/结果/错误、能力注册、单一 Composition Root、Application Use Case 与独立 MCP/CLI/GUI 入口。保留现有 UI、Agent、parser/writer、infra 和工具模块，以 compatibility facade 逐调用链迁移。

本 Plan 不实现具体格式映射、Variant schema、任务调度算法、ParaTranz 网络合并或 FOMOD 业务阶段；这些由后续 V2 Plan 承接。旧入口只有在等价合同测试、调用方迁移和删除门禁同时通过后才可删除。

## 迁移顺序

1. 冻结包、版本、依赖和能力基线，先修复可安装、可导入、可探测前提。
2. 建立无 PyQt 依赖的 application contracts 和 use-case ports。
3. 建立单一 Composition Root，并让 GUI/CLI/MCP 复用同一构造图。
4. 逐入口增加 facade，对比语义后切换；不做一次性目录搬迁。
5. 把 Tool schema、HITL、路径授权、结构化观察结果纳入公共合同。

## Story 清单

### Story 01：安装态包、版本、入口与依赖基线

[详细设计](stories/story-01-package-entry-dependency-baseline.md)

- **目标**：消除 `src.transbridge`、版本双源、无效 CLI 和声明/锁定/构建能力漂移。
- **文件落点**：`pyproject.toml`、`uv.lock`、`src/transbridge/__init__.py`、`src/transbridge/main.py`、新增 `src/transbridge/cli.py`、构建 spec/installer 配置、`tests/packaging/`。
- **实施**：选定版本单一来源；安装态 import 统一为 `transbridge...`；定义 `transbridge` 与 `transbridge-mcp` 入口；对 rank-bm25、py7zr、rarfile 等建立 declared/locked/bundled/capability 对照；修复或重建可复现 Python 3.12 环境说明。
- **验收**：源码树与安装态均可导入；`--help` 不启动 GUI；版本显示一致；锁文件可重建；缺可选依赖时能力为 degraded/unavailable 而非导入崩溃。
- **测试**：clean venv install/import/CLI smoke；依赖矩阵测试；打包收集探针；不得只在开发机全局环境验证。

### Story 02：公共 Operation 合同与能力注册

[详细设计](stories/story-02-operation-capability-contracts.md)

- **目标**：建立同步结果、后台 TaskRef、partial/failed/cancelled、诊断和能力可用性的唯一类型合同。
- **文件落点**：新增 `src/transbridge/application/contracts/`、`src/transbridge/application/capabilities.py`、`tests/contracts/test_operation_contracts.py`。
- **实施**：定义 RequestContext、OperationResult/Outcome、Diagnostic、CapabilityState、DomainError 与 Deferred TaskRef；错误分类覆盖输入、前置条件、权限、冲突、外部服务、取消和内部故障；明确序列化 schema。
- **验收**：同一条件下返回类型稳定；异常不得映射为 completed；入口可在执行前查询 available/degraded/unavailable 及原因；结果 schema 可被 GUI、Agent、MCP 使用。
- **测试**：状态互斥、schema round-trip、异常映射、能力缺失和部分成功合同测试。

### Story 03：Application Use Case 端口与单一 Composition Root

[详细设计](stories/story-03-application-composition-root.md)

- **目标**：将入口编排与具体 parser/writer/client/global singleton 解耦。
- **文件落点**：新增 `src/transbridge/application/ports/`、`src/transbridge/application/use_cases/`、`src/transbridge/bootstrap/`；修改 `src/transbridge/ui/app.py`、`src/transbridge/main.py`。
- **实施**：定义 AppRuntime/RuntimeContext；集中创建配置、安全、I/O、repository、TaskRuntime 和 use cases；工具模块禁止惰性构造 AppContext/TaskManager；建立 process/call scope；保留 facade 委托现有实现。
- **验收**：构造图只有一个权威入口；headless 构造不导入 PyQt；相同 RuntimeContext 可由 GUI、CLI、MCP adapter 注入；未初始化上下文返回前置条件错误。
- **测试**：构造图集成测试、双 runtime 隔离测试、headless import test、无隐式 singleton 测试。

### Story 04：工具注册、Schema、HITL 与安全合同

[详细设计](stories/story-04-tool-schema-hitl-security.md)

- **目标**：修复 wildcard 过早展开、非 JSON schema、先写后审批和跨入口路径策略分裂。
- **文件落点**：`src/transbridge/smart_assistant/tool_registry.py`、`src/transbridge/smart_assistant/tools/`、`src/transbridge/smart_assistant/guardrails/`、新增 application tool adapter。
- **实施**：Agent 能力在注册完成后解析；ToolSpec 使用规范 JSON Schema；写操作在副作用前完成授权/HITL；路径先规范化与解析链接再授权；Observation 保留完整结构化执行 schema，展示摘要单独截断；共享 secret redactor。
- **验收**：预置 Agent 获得预期工具且能力准确；非法 schema 启动即失败；拒绝审批不产生业务写入；绝对路径按授权根处理而非一律拒绝；敏感 canary 不出现在日志/结果。
- **测试**：真实 registry 构造、HITL 前后副作用、路径逃逸/链接、schema validation、redaction canary 与 Observation round-trip。

### Story 05：独立 MCP/CLI/GUI Adapter 与入口等价基线

[详细设计](stories/story-05-entrypoint-parity-mcp.md)

- **目标**：让 MCP 作为独立 stdio 进程运行，并建立多入口调用同一 use case 的第一组合同证据。
- **文件落点**：新增 `src/transbridge/entrypoints/mcp.py`、`src/transbridge/entrypoints/cli.py`、GUI adapter；迁移 `src/transbridge/smart_assistant/mcp/`；`tests/integration/entrypoints/`。
- **实施**：实现 initialize/version/capabilities 协商；stdout 仅协议、日志写 stderr；凭据从环境/安全存储注入；无 Project 上下文保持协议存活并返回明确错误；GUI/Agent/MCP adapter 只转换输入输出。
- **验收**：Windows stdio 子进程可握手、列工具和安全关闭；MCP 不依赖 GUI 内存状态；至少一个无状态能力和一个需上下文能力通过 GUI/Agent/MCP 语义等价测试。
- **测试**：真实子进程 JSON-RPC smoke、协议版本协商、stdout 污染检查、无上下文降级、入口 parity 集成测试。

## 追溯与历史状态纠偏提议

| 需求/问题 | Story | 历史 Plan 提议状态 |
|---|---|---|
| FR17.1～17.5；R-001/3/4/7 | S02、S03、S05 | `agent-upgrade`、`agent-tool-expansion`：`partially-verified`, `blocked_by: platform-contract-foundation-v2` |
| FR17.2/4；R-005/9/11 | S02、S04 | `tool-prompt-layering`：`partially-verified`; 工具相关旧步骤由本 Plan 部分取代 |
| R-008/12 | S03 | `smart-assistant-refactor`：`partially-verified`, application ownership `superseded_by` 本 Plan/持久化 Plan |
| NFR3/5/6；R-001/2 | S01、S05 | 旧“可运行/可发布”声明 `blocked_by: release-hardening-v2` |

## 风险、回退与完成门禁

- 风险：Composition Root 迁移期间出现双状态。控制：facade 只能委托；在测试中断言同一 runtime identity。
- 风险：批量改 import 影响用户未提交修改。控制：按包迁移并保留兼容重导出，不做全仓一次性移动。
- 回退：每个入口 adapter 可独立切回旧 facade，但新合同类型与测试保留；不得恢复隐式全局状态。
- 完成门禁：S01～S05 各自有真实成功链、独立 changelog；所有下游 Plan 可从 Composition Root 获取合同；删除旧入口需单独确认。
