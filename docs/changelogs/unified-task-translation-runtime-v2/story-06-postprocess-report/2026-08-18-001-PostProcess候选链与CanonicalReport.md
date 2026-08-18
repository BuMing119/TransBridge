# Story 06：PostProcess 候选链、Stage 与 Canonical Report

- 所属 Plan：`unified-task-translation-runtime-v2`
- 增量日期：2026-08-18
- 状态：实现完成，增量验证通过；待综合 QA

## 增量内容

将后处理从「直接 `update_entry_stages` 的 GUI 编排」收敛为候选值链与单一报告源：

1. **候选链**：`PostProcessCandidate`（不可变）经 refine→polish→arbitrate 逐阶段传递；每个阶段只读取上一候选并返回 typed `PostProcessStageOutcome`（phase/candidates/diagnostics/duration_ms）。
2. **Candidate DTO 适配器**（`application/translation/postprocess_stages.py`）：`CheckerStage` 以显式只读 entry view 桥接 legacy `BaseChecker`；`LlmPostProcessStage` 通过 `PostProcessLlmPort`（含真实 HTTP 的 `OpenAiPostProcessHttpPort`）驱动 refine/polish/arbitrate；arbitrate 产出 `accepted` 决策，拒绝/待定保留候选并附诊断，不覆盖正文。
3. **StagePolicy scope**：候选资格由 `stage_policy.evaluate(..., StageOperation.AI)` 决定；hidden/locked/空译文不进入候选。
4. **Typed batch outcome**：阶段 typed 失败聚合为 `PARTIAL`（保留 snapshot，`failed≥1` 且 `succeeded≥1`）；阶段异常聚合为 `FAILED`（value=None）；取消为 `CANCELLED`（value=None，progress 先落盘）；`REVISION_CONFLICT`（expected_revisions 不匹配）聚合为 `PARTIAL`/`FAILED` 并携带诊断；异常一律不被吞。
5. **Checkpoint**（`postprocess_checkpoint.py`）：`PostProcessCheckpoint` 每阶段完成后原子落盘 `stage + candidate hash`（含 text/original/before/revision 供无 LLM 重放的 resume）；owner/input_fingerprint 身份绑定、revision 单调、损坏文件拒绝；`resume_after_phase` 按位置跳过已完成阶段。
6. **Canonical Report**（`postprocess_report.py`）：`ReportSnapshot` 增加 `issues/failures/timing_ms/run_spec_summary`；JSON/CSV/Excel renderer 全部只消费 snapshot；`render_report` 渲染失败不回滚已提交业务，结果附 `REPORT_RENDER_FAILED` 诊断并以 PARTIAL/FAILED 表达；prompt/secret 不进入报告内容。

## 验收对应

- 小型受控 HTTP 成功链（`tests/integration/translation/test_http_postprocess_chain.py`）：真实网络栈证明 refine 输出进入 polish、polish 输出进入 arbitrate，最终 accepted 文本与阶段顺序、幂等键唯一性一致。
- 阶段失败/取消/恢复/revision conflict 均有合同测试；GUI/Excel/history 的 golden counts 由同一 snapshot 派生（JSON/CSV/Excel 三渲染器计数一致）。

## 验证

- 正式 uv 测试：`tests/contracts/translation` + `tests/integration/translation/test_http_postprocess_chain.py` 共 **51 passed**。
- EvidenceManifest：[task-s06](qa-evidence/task-s06/qa-20260818T105914.225150Z-70f6d9a4f364/manifest.json)，`verify_evidence.py` 结果 `VALID (passed)`。
- Ruff check/format 与定向 `git diff --check` 通过。

## 边界

- 本 Story 未将 legacy GUI `PostProcessor`/`ReportGenerator`/Dialog 的生产调用切换为 workload（属 S07 入口集成）；Excel 渲染依赖 openpyxl 可用性，缺失时按 degraded 由 renderer 失败语义承接。
- 未执行 Git commit/push，未修改 `.partial.md` 或既有正式审查报告。
