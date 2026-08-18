# Story 02：Typed Pipeline、RunSpec 与阶段终态

- 所属 Plan：[FOMOD Pipeline V2](../plan.md)
- 状态：已实现并增量验证通过（2026-08-18）
- 追溯：FR23.1/23.2、FR20、FR21.1；ADR-014/019；R-043/R-044
- 依赖：S01、TaskRuntime S01～S04

## 目标与验收

发现、解包、差异、迁移、翻译、XML、过滤、构建、发布均返回 typed result；任一必要阶段失败/取消阻止发布；target locale 不回退默认；终态与 TaskRuntime/报告一致。

## 事件流与接口

`FomodRunSpec(input hashes, target locale, policies, run_id)` → stage DAG 顺序执行 → `StageResult(outcome, artifacts, diagnostics, metrics)` → 下一阶段仅消费已验证 artifact refs → publish gate。PipelineResult 聚合 stage outcomes，completed/partial/failed/cancelled 互斥。

## 实施步骤

1. 把 `FomodPipeline.run` 拆为 application workload 与 stage Protocol，现有函数作为 adapters。
2. RunSpec 固化 new/old archive、locale、TM/AI/filter/archive/publish policy 与 config hash。
3. `_ai_translate/_write_back` 不再吞异常/返回假 0；fatal diagnostic 中止 DAG。
4. 每阶段前后检查 cancel/run guard，取消后禁止 pack/publish。
5. stage events 进入 TaskRuntime/ReportSnapshot，UI 只投影。

## 测试与边界

真实小 FOMOD fixture 覆盖完整成功链；对九阶段逐点 fault/cancel，断言后续正式副作用不存在；spy 验证 target locale 进入 AI/XML/TM/output/report。未知可选阶段可 partial，必要阶段规则由 spec 明确。旧 pipeline facade 委托新 workload。
