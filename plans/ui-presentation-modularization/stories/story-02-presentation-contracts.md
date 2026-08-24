# Story-02：建立展示层合同、Composition 与静态审计

- **所属 Plan**：[UI 展示层模块化与上帝窗口拆分](../plan.md)
- **状态**：已完成（2026-08-19）
- **优先级**：P0
- **前置依赖**：S01 的事件、依赖与资源基线
- **下游**：S03～S08

## 目标

提供足够轻、可测试、无业务状态所有权的展示层共同语言，并用审计阻止上帝类、隐式依赖和资源泄漏回生。

## 原始验收标准

- [x] 提供 Qt-free 的 ViewPort/不可变 ViewState/UiMessage/BusyState 基础合同，以及幂等 Subscription/Binding 生命周期合同。
- [x] 形成 composition 约定：具体 Qt View 只在 feature facade 或 shell 组装，Presenter 不 import 具体窗口。
- [x] 静态审计能识别新增跨组件私有属性、parent lookup、View 直连 repository/client、无 owner 订阅和模块级可写 UI singleton。
- [x] 规模审计执行目标与 hard gate，并支持带 owner/reason/expires_when 的临时豁免。
- [x] 合同不强制复制大型 table rows，也不为同线程交互增加 queued signal 层。

## 计划接口

- `UiMessage(code, text, severity, retryable=False)`：冻结 dataclass；不把原始异常或本地化策略塞入 View。
- `BusyState(active, operation, progress=None, cancellable=False)`：冻结 dataclass。
- feature-specific `Protocol`：只暴露 `render(...)`、`show_error(...)`、`set_busy(...)` 等最小操作；不创建万能 `BaseView`。
- `Subscription.close()`：幂等；可由 Binding 聚合，异常按诊断记录但继续释放其余句柄。
- `Binding.start()/close()`：同一实例 start/close 语义明确；close 后事件不再到达 View。
- 高容量数据只传 revision、stable IDs、分页/批次引用或现有 model port，不进入通用大 ViewState。

依赖方向：

```text
Qt View -> presentation DTO/Protocol
Presenter -> ViewPort + application/projection ports
Binding -> external events + Presenter/ViewPort
feature facade/composition -> concrete View/Presenter/Binding
```

## 审计规则

- 尺寸：目标 `module <=500`、primary class `<=30 methods`；hard gate `>700` 或 `>40`。
- 依赖：跨 feature `._private`、`_find_main_window`/parent walking、View import repository/client、循环 import、module-level mutable owner。
- 生命周期：新增 `.connect()`/callback registration 必须能定位 disconnect/Subscription owner；AST 不确定项允许人工审查，不误报为自动通过。
- 豁免记录必须包含 `path/rule/owner/reason/expires_when`，并由 S08 清点。

## 实施步骤

1. 从 S01 场景选择两个最小切片，用测试替身验证合同形态，避免先造抽象再找用途。
2. 在 `ui/presentation` 实现纯 DTO、Protocol 和可组合 Subscription；核心模块不得 import PyQt。
3. 编写 contract tests：冻结性、close 幂等、部分释放失败、close 后不分发、大数据引用不复制。
4. 实现审计脚本和 fixture，自测每条规则的正反例；对现存违规生成基线豁免。
5. 在开发/QA 命令中接入审计，但 S02 只阻止新增违规，不要求尚未迁移模块立即归零。

## 边界与错误

- Presenter 只有确需 signal/thread affinity 时才可继承 QObject，并在文档/审计豁免中说明。
- 不把所有 widget signal 包装成 event bus；同线程 intent 可直接调用 Presenter。
- `Subscription.close()` 不得吞掉主错误，但释放路径要 best-effort 完成所有句柄并聚合诊断。
- Protocol 是静态边界，不在热路径做反射式 runtime validation。

## 文件变更

- 新增 `src/transbridge/ui/presentation/{__init__,contracts,messages,subscriptions}.py`
- 新增 `scripts/audit_ui_modularity.py`
- 新增 `tests/contracts/ui/test_presentation_contracts.py`
- 新增 `tests/contracts/ui/test_ui_modularity_audit.py`

## 测试与建议命令

- `pytest tests/contracts/ui/test_presentation_contracts.py tests/contracts/ui/test_ui_modularity_audit.py -q`
- `python scripts/audit_ui_modularity.py`（实际参数由脚本帮助定义）

## 风险与回退

主要风险是过度抽象和误报。合同先服务两个真实切片；审计初期采用“基线内允许、新增阻止”。若公共 DTO 证明无复用价值，可下沉到 feature 内，但 ownership 与依赖方向不回退。

## 未决问题

- 豁免文件可复用仓库现有配置格式，若无合适位置则由 S02 选择 `scripts` 邻近的声明文件；不得埋在代码注释中难以汇总。
