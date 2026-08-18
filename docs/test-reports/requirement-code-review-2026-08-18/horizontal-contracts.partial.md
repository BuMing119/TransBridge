# 横向实现契约审查（阶段性结果）

- Agent：`implementation_contract_review`
- 状态：阶段性；后续独立续跑。

## 已确认的调用链断裂

1. `tool_parser.py` 的 ESP/EET/XT/SST 分发统一调用不存在或错误的 `.parse()`/模块，成功路径不可用。
2. 桌面输入使用绝对路径，但 Agent 和全局 guardrail 无条件拒绝绝对路径；Archive/Parser 等工具与产品输入模型冲突。
3. MCP 启动链：`ui/app.py` 引用未导入的 `ToolRegistry`；Adapter 启动时没有 AppContext；auth token 未按配置注入；Windows stdio select 有兼容风险。
4. `pyproject.toml` console script 指向 `transbridge:main`，包 `__init__` 无 main；实际 main 使用 `src.transbridge...` 导入。源码大量采用 `src.transbridge`，与标准 src-layout 安装命名空间存在风险。
5. pyproject/runtime 版本不一致；7z/RAR 运行依赖未进入项目依赖。
6. FOMOD pipeline 存在结果计数未赋值、temp 未清理、取消后仍打包、异常吞噬、TM context 未传、冲突未上报等契约断层。
7. TM 直接写 translation 不更新 Stage；pipeline 忽略 ApplyResult 的 conflicts/needs_review。

## 初步改造方向

- `ParserAdapterRegistry + ParseApplicationService`。
- `WriteBackService + OutputTransaction`。
- `TranslationApplicationService + JobRuntime`。
- `PostProcessSession + CommitPolicy`。
- `ParaTranzGateway + typed Sync Use Cases`。
- `EntryMutationService + VariantStateV2 + LabelStore`。
- 单一 composition root 注入 AppContext、配置、SecretStore 与服务。

正式横向报告将补全多入口契约矩阵、兼容层策略与移除旧入口的顺序。

