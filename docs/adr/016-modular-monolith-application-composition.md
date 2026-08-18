# ADR-016：模块化单体应用层、Ports/Adapters 与 Composition Root

- **状态**：已接受（2026-08-18）
- **日期**：2026-08-18
- **对应需求**：FR17、FR21、FR22、FR23、NFR3.1、NFR5.1、NFR6.1
- **关联 ADR**：ADR-004、ADR-008、ADR-010、ADR-012、ADR-014、ADR-015
- **承接根因**：R-001～R-012、R-040～R-050

## 背景与约束

当前 GUI、Agent、MCP 和 FOMOD 分别构造 parser、writer、TaskManager、AppContext、LLM/ParaTranz 客户端或流水线。`ui/app.py` 同时承担注册、MCP 启动和 GUI 构造，部分工具在缺少注入时自行创建新的 `AppContext()`/`TaskManager()`。这使同一业务能力在不同入口拥有不同数据流、错误语义和生命周期。

本轮必须保留现有资产并渐进迁移，不进行微服务化、全面事件溯源或一次性目录重写。正式分发采用安装器加 onedir 载荷，可附带便携包。PyInstaller 官方将 onedir 定义为默认的一文件夹载荷，适合同时承载 GUI、CLI、MCP 入口及共享依赖。

## 决策

### 1. 采用模块化单体和显式应用层

TransBridge 继续作为一个可安装 Python 分发和一个模块化单体演进。逻辑依赖方向固定为：

```text
GUI / CLI / Agent / MCP / FOMOD entry adapters
                    ↓
          Application use cases
                    ↓
     Domain model + application ports
                    ↑
 Parser/Writer/Persistence/LLM/ParaTranz/FileOps adapters
```

- Application use case 是跨入口业务编排的唯一权威入口，负责校验请求、打开事务、调用端口、提交结果和产生统一诊断。
- Domain/Application 层 SHALL 不依赖 PyQt、具体 HTTP 客户端、具体 parser/writer 或进程 stdio。
- UI、Agent、MCP 和 FOMOD 只做参数适配、权限上下文建立、结果呈现和入口特有交互，不复制业务编排。
- `infra/`、`parser/`、`writer/`、`persistence/`、`paratranz/`、`fileops/` 是端口实现或兼容适配器，不拥有应用级状态。

初始目录以最小增量建立，具体文件拆分由 Plan 决定：

```text
src/transbridge/application/
  contracts/       # 公共 request/result/error/capability
  ports/           # repository、I/O、task、security、remote service 端口
  use_cases/       # parse/write、project/session、workflow、sync、publish
src/transbridge/bootstrap/
  composition.py   # CompositionRoot / AppRuntime 构造
  entrypoints.py   # GUI、CLI、MCP 入口装配
```

上述目录是迁移目标，不要求先搬迁全部现有模块。

### 2. 单一 Composition Root 与进程级 AppRuntime

每个进程 SHALL 由一个 `CompositionRoot` 显式构建一个 `AppRuntime`。只有 Composition Root MAY 创建 repository、TaskRuntime、业务 use case、外部客户端和入口 adapter；业务模块不得通过模块级单例、惰性 getter 或 `AppContext()` 自建替代实例。

`AppRuntime` 至少暴露：

- 已注册 use case 与 capability registry；
- 当前进程的安全策略、配置快照和版本信息；
- Project/Session context factory；
- 统一 TaskRuntime 与可释放资源；
- `shutdown()` 生命周期入口。

`RuntimeContext` 是一次调用或一个 owner 的不可变上下文，至少包含 `owner_id`、可选 `project_id/variant_id/session_id`、权限声明、工作目录授权和 Run ID。它替代工具对裸 `AppContext` 的依赖。

### 3. 公共请求、结果和错误语义

跨入口 use case SHALL 使用有类型的请求和结果。规范操作结果包含：

- `status`: `completed | partial | failed | cancelled`；
- `value` 或 `artifact_refs`；
- `diagnostics`、`warnings`、统计和 Run ID；
- 对长任务返回 `JobRef`，而不是在同一签名中混用业务数据与字符串 Task ID。

错误按输入、能力缺失、权限、冲突、外部服务、取消和内部故障分类。入口 MAY 改变显示形式，但不得改变状态或吞掉诊断。

### 4. 能力注册而非存在性推断

每项公开能力 SHALL 由 capability registry 报告 `available | degraded | unavailable`、原因和前置条件。类或函数存在、模块可导入、可选依赖偶然安装均不得作为能力可用证明。

格式支持能力由 ADR-017 的矩阵提供；检索、归档后端、ParaTranz、MCP、GUI 和发布能力均使用相同机制。入口在 unavailable 时 SHALL 隐藏、禁用或返回结构化先决条件错误。

### 5. MCP 为独立 stdio 应用入口

`transbridge-mcp` 是独立 console entry point，由 MCP 客户端作为子进程启动；它不在 GUI 进程中读取 stdin，也不导入或创建 `QApplication`。MCP 进程通过 Composition Root 构建 headless AppRuntime。

- 可独立从磁盘项目构建的能力直接在 headless RuntimeContext 中运行。
- 依赖 GUI 未保存内存状态的能力在 IPC 尚未实现前报告 degraded/unavailable，不得伪造空 AppContext。
- 若未来引入 GUI↔MCP IPC，IPC 仅作为 RuntimeContext/端口的 adapter，不改变 use case。
- stdio 的 stdout 只允许 MCP JSON-RPC 消息，日志写 stderr；协议版本和能力在初始化阶段协商，不硬编码单一版本。
- stdio 凭据从受控环境或本地安全存储注入，不在每条请求的自定义 `_meta` 中传递明文 token。

### 6. 包、入口、版本与分发单一来源

- 安装态包名统一为 `transbridge`；新代码不得导入 `src.transbridge`。
- 版本 SHALL 有单一权威来源，并被 GUI、CLI、MCP、安装器和报告读取。
- `transbridge`、`transbridge-mcp` 以及需要的诊断入口 SHALL 在 clean install 后可执行。
- onedir 载荷共享依赖和数据文件；安装器、便携包只改变部署方式，不改变运行时契约。

### 7. 兼容 facade 与删除门禁

现有 `AppContext`、TaskManager、parser/writer 类、ToolRegistry 工具函数和 GUI worker 暂时保留。迁移采用 strangler 路线：

1. 先建立公共合同和 Composition Root；
2. 用 adapter 包装现有实现；
3. 逐入口切换到 use case；
4. 对新旧路径执行等价合同测试；
5. 调用方、文档和发布 smoke 全部迁移后，单独确认删除旧 facade。

兼容 facade SHALL 只委托新合同，不继续拥有第二套状态或业务规则。

## ADR-001～ADR-015 评估矩阵

| ADR | 评估结果 | 处理 |
|---|---|---|
| ADR-001 | 统一条目模型保留；`id == key` 与来源字段丢失不再成立 | 由 ADR-017 追加身份合同 |
| ADR-002 | Collection 中枢保留；AppContext 广播与直接 mutation 不再是业务所有权 | 由 ADR-017 追加聚合与 mutation port |
| ADR-003 | 三轮策略保留为翻译域策略 | 受 ADR-019 JobSpec/checkpoint/commit 约束，无需改写原决策 |
| ADR-004 | QThread 保留为 GUI adapter；“唯一后台通道”被替代 | 追加 ADR-019 关系说明 |
| ADR-005 | TOML/string.Template 选型保留 | Prompt/Profile 作为不可变 JobSpec 输入，无需新 ADR |
| ADR-006 | JSON 资产保留；AppContext 所有权、overlay、字段推导被替代 | 由 ADR-018 部分取代 |
| ADR-007 | 动作规则保留；MixedWorker 不再是权威业务引擎 | 由 ADR-019 和应用 use case 部分取代 |
| ADR-008 | UI/后端分层方向保留；具体后端仍直接依赖 AppContext | 追加本 ADR/ADR-018/019 边界 |
| ADR-009 | 文件知识解析、记忆和 Reflexion 保留 | 与 ADR-017 格式 I/O 分离；重试受幂等/错误分类约束 |
| ADR-010 | 共享基础设施包保留 | 限定为 adapter，实现 ports，不承载 use case |
| ADR-011 | 图编排保留为 Agent workload adapter | checkpoint/owner/终态统一到 ADR-019 |
| ADR-012 | 护栏/观测方向保留；MCP 拓扑与认证需更新 | 追加独立进程、RuntimeContext 和官方 stdio 约束 |
| ADR-013 | BM25/向量算法保留 | 追加可选能力、依赖锁定和 disabled 零加载约束 |
| ADR-014 | TM/FOMOD 资产保留 | 追加 typed transactional pipeline 与统一 Stage/ArchivePolicy |
| ADR-015 | fileops/migrator 独立包保留 | 追加 ports、ArchivePolicy 和来源命名空间约束 |

## 备选方案

### 继续由各入口直接调用具体实现

短期修改少，但无法满足 FR17 等价结果、统一错误和任务终态，拒绝。

### 一次性目录重写

可快速得到整齐布局，但当前实现和历史入口过多，回归与合并风险不可控，拒绝。

### 微服务或全面事件溯源

会引入部署、IPC、版本和运维成本，超过桌面工具需求，拒绝。

## 影响与风险

- 正面：入口行为可合同测试；状态和依赖创建可追踪；MCP/CLI 可独立安装运行。
- 成本：迁移期存在 facade 和新接口并行，需要严格 owner 与等价测试。
- 风险：Application 层变成新的“大杂烩”。缓解方式是按 use case、port 和业务聚合拆分，并由 ADR-017～019 固定核心合同。

## 迁移与回退

- 每个迁移 Story 只切换一个明确调用链，保留旧 facade 回退开关或委托路径。
- 若新 use case 未通过等价合同，回退调用方到旧 facade，不回滚已兼容的数据模式。
- 删除门禁需要：替代入口可用、成功链/失败链/取消链通过、clean install smoke 通过、无活跃调用方、用户确认。
