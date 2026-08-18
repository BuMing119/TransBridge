# FOMOD Pipeline V2 / Story 01：ArchivePolicy 与 MOD 根安全发现

- **日期**：2026-08-18
- **状态**：已实现并增量验证通过
- **追溯**：FR16、NFR2.1/NFR3.1；ADR-015；R-043

## 变更与验证

- ZIP/7z/RAR 统一为枚举、全量预检、同目录 staging、在线预算和输出复核，拒绝路径逃逸、链接、特殊文件、压缩炸弹及超限项；保留旧 extract/pack facade。
- FOMOD MOD 根发现按 0/1/N 候选处理：唯一候选自动选择，多候选返回 confirmation-required。
- 锁定 uv 回归 **37 passed**；[EvidenceManifest](../../../test-reports/requirement-code-review-2026-08-18/qa-evidence/fomod-s01/qa-20260818T092907.990759Z-f31f394051b8/manifest.json) 已通过 verify，Ruff/format/diff-check 通过。

## 遗留边界

- RAR 缺 unrar 时明确 capability unavailable；S02 typed pipeline 负责消费多根确认计划。
