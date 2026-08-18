# Story 05：StagePolicy 与 Localized Strings 数据完整性

- 日期：2026-08-18
- Epic/Story：`translation-io-kernel-v2/S05`
- 追溯：FR18.9、FR23.3、ADR-017、R-016/R-017
- 状态：实现完成，增量验证通过；待综合 QA

## 实现增量

- 新增七级离散 `StagePolicy`，不用数值范围推断状态。Stage 0 与 hidden 始终投影原文；Stage 1/2/3/5 投影译文；locked 只在译文非空时投影译文，`locked + 空译文` 返回阻断诊断。AI、TM、PostProcess 和 FOMOD 的候选选择均经同一策略，hidden/locked 不会被旧 overwrite/target-id 分支重新纳入。
- 新增 STRINGS/DLSTRINGS/ILSTRINGS 完整 `SourceSnapshot` adapter：保存每个 ID 的顺序、原始 chunk、编码/BOM 和定位信息，只替换 ChangeSet 指定项；源指纹、重复 ID、跨 namespace/locator 与 locked-empty 全部 fail-closed。
- SSE adapter 解析 loose localized strings 时保留完整快照；没有可写快照即拒绝不安全写回。FOMOD 写回转用 `TranslationIoUseCase`，解析或写出失败会传播而不再伪装为已完成。EET Writer 使用策略结果写出文本和 status，不再通过文本差异猜测翻译状态。

## 验证证据

- 锁定 uv（CPython 3.12.12）运行 I/O contracts、SSE parser/writer、EET writer、Agent、TM 与 FOMOD 成功链：**259 passed**。其中包含三类真实 Strings fixture、SSE loose snapshot/rebuild、locked-empty 阻断及 FOMOD 写回；未声明原子 publish 已完成。
- [EvidenceManifest](../../../test-reports/requirement-code-review-2026-08-18/qa-evidence/io-s05/qa-20260818T083955.212699Z-82001a337954/manifest.json)：`passed`，已由 `verify_evidence.py` 校验 schema、verdict 与 hash。
- 新 I/O 与合同测试 Ruff check/format、历史接线文件 F821 检查和范围 `git diff --check` 通过。完整回归产生 26,262 条 warning，主要来自既有 `PluginWriter.get_by_key`、旧 `TranslationEntry` 直接 mutation 和其它 compatibility facade；它们不是成功完成证据，也不在本 Story 静默重写。

## 剩余门禁

S06 承接同卷 staging、备份、验证、TOCTOU 与原子 publish；BSA 内嵌 localized strings 保持 P1 experimental，缺 loose snapshot 时继续阻断。legacy `PluginWriter` 兼容入口暂不删除，待替代入口、迁移验证及删除门禁完成后再评估。
