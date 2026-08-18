# Story 06：PostProcess 候选链、Stage 与 Canonical Report

- 所属 Plan：[Unified Task and Translation Runtime V2](../plan.md)
- 状态：实现完成，综合 QA 通过（2026-08-18）
- 追溯：FR21.5～21.7；ADR-019；R-037/R-038
- 依赖：S03～S05、I/O StagePolicy

## 验收记录（2026-08-18）

- 综合 QA：生产 GUI/Agent/AutoTranslator 已接入同一 candidate-only workload 与单一提交；partial report 不再被成功 commit 覆盖为 completed。最新 task-s06 EvidenceManifest `qa-20260818T125309.913470Z-4d4ed7c14d10`，72 passed。
- 51 passed（`tests/contracts/translation` + `tests/integration/translation/test_http_postprocess_chain.py`）；EvidenceManifest [task-s06](../../../docs/test-reports/requirement-code-review-2026-08-18/qa-evidence/task-s06/qa-20260818T105914.225150Z-70f6d9a4f364/manifest.json) 通过 verify。
- 新增 `application/translation/postprocess_checkpoint.py`（stage/candidate hash 原子 checkpoint + resume）、`postprocess_report.py`（canonical renderer 合同）、`postprocess_stages.py`（legacy checker DTO adapter + HTTP LLM 阶段）；`postprocess.py` 扩展 accepted/conflict/checkpoint/timing。

## 目标与验收

检测→细化→复验→润色→仲裁逐阶段读取上一候选，只有最终 commit 修改集合；异常不吞；GUI、Excel、历史由同一 ReportSnapshot 派生并保持 run_id/计数一致。

## 数据流与接口

CandidateSet/input snapshot → check issues → RefineCandidate → recheck → PolishCandidate → ArbitrationDecision → AcceptedCandidateSet → ChangeSet commit。每阶段返回 typed outcome/diagnostic，不接收可变 TranslationEntry 作为唯一载体。`ReportSnapshot` 包含 RunSpec summary、counts、entry before/after/candidate/action/stage、issues/decisions/failures/timing/run_id/schema。

## 实施步骤

1. 为现有 checker/refiner/polisher/arbiter 加 candidate DTO adapters，明确输入字段。
2. PostProcessor orchestration 改为 workload，不直接 `update_entry_stages`。
3. scope 使用稳定 EntryKey；StagePolicy 决定候选资格与最终 stage patch。
4. 所有 batch exception 聚合为 partial/failed；checkpoint 保存 stage/candidate hash。
5. ReportGenerator、Dialog、history reader 仅消费 ReportSnapshot；Excel 是 renderer。

## 测试、边界与迁移

小型真实/受控 LLM 成功链证明 refine 输出进入 polish/arbiter；覆盖某阶段失败、取消、恢复和 revision conflict。golden 验证 GUI model/Excel/history counts、诊断和 before/after 一致。报告 renderer 失败不回滚已提交业务，但运行结果附 report diagnostic；敏感 prompt/secret 不落报告。
