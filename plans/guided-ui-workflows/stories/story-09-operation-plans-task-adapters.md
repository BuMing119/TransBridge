# Story-09：上传/下载/写回/FOMOD 操作计划与任务接入

- **所属计划**：[guided-ui-workflows](../plan.md)
- **状态**：草稿
- **需求**：FR26.8、FR26.9、FR22、FR23、NFR2.1
- **依赖**：S03、S05～S07；ADR-019、ADR-021

## 目标与边界

原样承接 plan S09。共享的是操作计划的呈现语言和生命周期，不是把上传、下载、文件写回和 FOMOD 合并成一个业务 request 或通用执行器。各 application use case 继续拥有验证、幂等、合并和提交规则。

## 计划模型

- `OperationPlanViewState`：operation kind、目标、scope、mode、conflict/overwrite、backup、warnings、estimated impact、editable fields、submit enabled reason。
- `OperationPreflightResult`：冻结 request digest、目标 revision/fingerprint、credential/capability、path/archive policy、预计副作用和 one-shot confirm token。
- `OperationResultActionState`：success/failed/skipped/cancelled 对象、report/artifact、failed-subset retry 和导航 intents。

Presenter 使用 discriminated operation kind 选择专用 mapper/use case；禁止一个巨大 if/else executor 获得所有服务依赖。

## 操作顺序

```text
context projection -> editable operation draft
  -> domain/application preflight (no formal side effect)
  -> summary + one final confirmation
  -> TaskRuntime JobSpec / bounded adapter
  -> candidate/staging or remote plan execution
  -> commit guard / atomic publish
  -> result projection + navigation/retry actions
```

返回编辑使 confirm token 失效。远端上传/下载重试必须重新验证 remote revision/permission；文件写回/FOMOD 必须重新验证 source fingerprint、输出路径和备份策略。

## 分域要求

- **上传**：项目、文件、scope、锁定/隐藏条目、覆盖/合并策略、credential/permission；retry 只处理未确认成功项。
- **下载**：远端 revision、目标 Variant、merge/conflict、备份与部分结果隔离；不能直接覆盖当前 dirty Variant。
- **写回**：format capability、source fingerprint、输出路径、覆盖、备份、staging/atomic replace。
- **FOMOD**：归档预算/路径安全、旧包迁移源、target language、AI capability、typed stages、资源保真和 atomic publish。

## 实施步骤

1. Characterize 现有 modal chain、request builders、worker signals、确认点和正式副作用边界。
2. 定义 Qt-free plan/preflight/result ViewState 与每个 operation mapper；先覆盖只读摘要。
3. 将现有校验移动或委托到 application preflight；UI 只展示稳定 diagnostic 和修复 intent。
4. 逐项替换 modal chain，确保只有最终 token 提交一次；返回编辑不丢 draft。
5. 按 S03 inventory 接入 TaskRuntime 或有界 adapter；capability、log、artifact、retry 逐项证明。
6. 实现部分失败 result 和 failed-subset new-run；每类任务复用自身幂等/checkpoint/commit guard。
7. 最后接入 FOMOD，保留 typed pipeline，不用通用 UI executor 重写阶段。

## 文件与测试

- 新增：`src/transbridge/ui/operations/plan_view.py`、`plan_presenter.py`、`preflight_view.py` 和分域 mapper
- 修改：Workbench upload/download/write slices、`operation_coordinator.py`、FOMOD panel/entry
- 修改：相关 application sync/io/fomod task entrypoints 与 composition adapter（仅在现有合同缺口处）
- 新增：operation plan/preflight/single-submit/partial/retry/navigation tests

重点测试：credential/permission、locked/hidden、remote revision、dirty Variant、不可写/长路径、归档 traversal/预算、覆盖/备份、取消、部分失败、retry 新 Run ID、commit 后迟到取消、重复点击。

## 回退与风险

每个 operation kind 可独立回退原 facade；已迁移的 TaskRuntime/commit guard 不回退到双终态。共享 ViewState 不得演化为跨域业务 DTO。原进度窗口保留到对应分域验收通过，之后只作为同一 S03 projection 的备用 View。
