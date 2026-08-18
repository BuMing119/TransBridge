# Story 03：Application Use Case 端口与单一 Composition Root

- 所属 Plan：[Platform Contract Foundation V2](../plan.md)
- 状态：实现完成，综合 QA 通过（2026-08-18）
- 追溯：FR17.1/17.2/17.4；ADR-016；R-003/R-007/R-008/R-012
- 前置依赖：S01/S02

## 目标与原始验收

建立无 PyQt 依赖的 application contracts/use cases 和唯一构造图。headless 构造不导入 PyQt；GUI、CLI、MCP 注入相同 RuntimeContext；未就绪上下文产生结构化前置条件错误。

## 当前调用链与目标流

当前 `ui.app.main()` 同时创建 QApplication、ToolRegistry、Agent、MCP，工具 getter 可惰性创建 AppContext/TaskManager。目标流：entrypoint → `build_runtime(settings, capabilities)` → `AppRuntime`（ports/use cases/task/security）→ 调用级 `RuntimeContext(owner, project/session refs, auth)` → adapter。AppContext 仅由 GUI adapter 映射 projection。

## 关键接口

- 计划新增 `bootstrap.composition.build_runtime()`、`AppRuntime.close()`、`RuntimeContext`。
- ports：Clock/Id/Filesystem/Security/Repository/Format/Task/Secret 等 Protocol。
- use case 依赖 ports 构造注入，不接受 QApplication、Widget、全局 registry。

## 实施步骤与文件

1. 创建 `application/ports/`、`application/use_cases/`、`bootstrap/`，先组合内存/旧实现 adapters。
2. 把配置加载、registry 初始化、安全策略和生命周期集中到 Composition Root。
3. 删除工具 getter 的隐式新建行为，改为缺上下文即 prerequisite diagnostic。
4. GUI 通过 adapter 创建 AppContext projection；CLI/MCP 使用 headless context。
5. 为 runtime close 定义反序释放顺序和幂等语义。

修改 `ui/app.py`、`main.py`、工具注册入口；不在本 Story 搬迁全部领域模块。

## 边界、迁移与测试

禁止双 runtime 共享 mutable registry；允许测试创建隔离 runtime。按入口切换 facade，旧函数只委托同一实例。测试覆盖构造图 identity、双 runtime 隔离、headless import、缺 Project/Secret、close 两次、端口注入 spy；静态扫描生产代码不得新增 `AppContext()`/`TaskManager()` 惰性构造。
