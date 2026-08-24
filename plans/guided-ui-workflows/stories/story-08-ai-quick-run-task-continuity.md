# Story-08：AI 快速运行、高级配置与任务结果连续性

- **所属计划**：[guided-ui-workflows](../plan.md)
- **状态**：草稿
- **需求**：FR26.7～FR26.9、FR21、NFR1.2
- **依赖**：S03、S05、S07；ADR-019、ADR-021

## 目标与现状

原样承接 plan S08。当前 `AITranslatorWindow` 已拆为 config/scope/run/result slices，但 `RunController` 使用本地整数 generation，单插件、mixed、polish 和 batch 仍可直接创建 QThread worker/progress window；legacy `ProgressCheckpoint` 与 application checkpoint 合同并未完全统一。

本 Story 既要重排 UI，又必须保证 translate/polish/mixed 的 RunSpec、checkpoint、取消与正式提交语义不变。

## 计划状态模型

- `AiQuickRunState`：mode、scope summary、entry count、token estimate/status、overwrite policy、enabled reason、active task ref。
- `AiAdvancedSettingsState`：长期 provider/model/embedding/terms/postprocess 配置及持久化状态；不包含活动 worker。
- `AiRunSpecSummary`：从已验证配置和 scope 冻结的只读摘要，关联 TaskRuntime JobSpec digest。
- `AiResultActionState`：report/artifact、locatable entry keys、applicable candidate、failed subset retry capability。

估算允许异步，但必须以 config/scope revision 过滤迟到结果；估算未知不能被伪装为 0。

## 数据流

```text
ConfigRepository snapshot + Workbench scope projection
  -> AiQuickRunPresenter -> preflight / enabled reason
  -> immutable application run request / JobSpec
  -> TaskRuntime (or bounded migration adapter recorded by S03)
  -> S03 TaskActivityViewState
  -> progress facade + task center
  -> canonical report/artifact -> result presenter -> locate/apply/retry intent
```

失败项重试重新构造只含失败稳定 EntryKey 的新 request，重新验证当前 source fingerprint/Variant revision，并产生新 Run ID。原 checkpoint 不得在 scope/spec 不匹配时复用。

## 实施步骤

1. 以现有 `TranslationRunRequest`、TranslatorConfig、scope presenter 和三模式 characterization 固定 RunSpec 输入。
2. 分离 quick-run 与 advanced view；长期配置仍由 ConfigRepository/config presenter 持久化。
3. 建立预检：依赖、credential reference、模型、scope、覆盖策略、估算状态；失败只更新 enabled reason。
4. 将现有 worker 路径逐模式映射到 S03 能力矩阵；能迁 TaskRuntime 的 workload 由 runtime 拥有终态，暂不能迁的 adapter 明确不支持的按钮和退出条件。
5. 让进度窗口与任务中心消费同一 activity projection；启动后 `show_and_activate` 任务上下文，避免后台运行无可见进度。
6. 让 result presenter 只消费 canonical report/artifact；定位使用 Workbench public port，应用结果走唯一 commit use case。
7. 实现失败 subset retry/new run、再次运行和关闭/迟到事件保护。

## 文件与测试

- 修改：AI `config_view.py`、`view_state.py`、config/scope presenters、`run_controller.py`、result slices
- 新增：`run_view.py`、`advanced_settings_view.py`（若现有 view 无法内聚承载）
- 修改：translation/mixed/polish/batch worker adapter 与 checkpoint composition
- 新增/修改：AI slice、三模式 characterization、TaskRuntime/checkpoint、result navigation、performance tests

重点测试：缺 `tiktoken`/模型/API、空 scope、估算迟到、重复开始、运行中改配置、cancel/stop/checkpoint、窗口关闭、mixed 部分失败、retry 新 Run ID、source/Variant revision 变化、结果定位。

## 回退与风险

UI 可回退到原配置布局和原进度 facade，但不得同时让旧 worker 与 TaskRuntime 提交终态或正式翻译结果。若某模式尚无稳定 application workload，则保留有界 adapter 并在 S03 inventory 标记为未迁移；不能为了统一按钮而声称支持恢复。
