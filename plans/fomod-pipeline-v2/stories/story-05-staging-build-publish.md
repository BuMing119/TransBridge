# Story 05：Staging Build、验证与原子发布

- 所属 Plan：[FOMOD Pipeline V2](../plan.md)
- 状态：实现完成，综合 QA 通过（2026-08-18）
- 追溯：FR23.6、NFR2.1；ADR-014/017；R-043/R-046
- 依赖：S01～S04、I/O S06、TaskRuntime S02

## 验收记录（2026-08-18）

- 综合 QA：提交后 manifest 失败按已发布的 `PARTIAL` 保真，typed pipeline 暴露 committed artifact；Windows staging replace 有界重试。最新 fomod-s05 EvidenceManifest `qa-20260818T130414.983224Z-20c33bd4356c`，76 passed。
- 10 passed（`tests/test_fomod_staging_publish.py`）；EvidenceManifest [fomod-s05](../../../docs/test-reports/requirement-code-review-2026-08-18/qa-evidence/fomod-s05/qa-20260818T111850.957164Z-c5b4f49d0733/manifest.json) 通过 verify；全 FOMOD 回归 84 passed。
- 新增 `application/fomod/publish.py`（StagingPackPublisher/FomodManifest/CleanupPolicy）；`fomod/stages.py::PublishStage` 改为 staging→重开验证→原子发布，产出 published_archive + publish_manifest。

## 目标与验收

每 run 独立 staging；失败/取消保留旧产物；成功归档可重开且 manifest 对应 input/policy/run_id；临时目录无泄漏。

## 事件顺序与接口

validated stage artifacts → assemble staging tree → output manifest/filter diff → pack staging archive → reopen/inspect/budget validate → hash manifest → commit guard → PublishCoordinator atomic replace/backup → cleanup。`BuildArtifact` 与 `FomodManifest` 是不可变 artifact refs。

## 实施步骤

1. staging 位于目标同卷受限目录，所有 builder/pack 输出不得直接指向正式路径。
2. assemble_output 只消费 approved resource manifest，检查必需 plugin/fomod 文件和路径冲突。
3. pack 后用 ArchiveInspector 重开，核对条目、hash/size、root layout 和安全预算。
4. commit 前检查 cancel/run_id/target fingerprint；失败不先删目标。
5. finally 根据 success/failure/debug policy 清理并记录残留诊断。

## 测试、边界与回退

真实成功链解包复验；磁盘满、权限、pack 异常、验证失败、目标并发变化、取消 race、cleanup 失败均 fault injection。断言旧 archive byte hash 不变、staging 无泄漏。旧 builder 可作为 stage adapter，不允许回退直接发布。
