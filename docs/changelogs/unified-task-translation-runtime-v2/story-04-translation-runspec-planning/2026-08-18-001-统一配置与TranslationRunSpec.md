# Unified Task and Translation Runtime V2 / Story 04：统一配置与 TranslationRunSpec

- **日期**：2026-08-18
- **状态**：已实现并增量验证通过
- **追溯**：FR21.1～21.3、FR21.8、FR21.9；ADR-019；R-033～R-035、R-039

## 变更

- 新建 versioned `transbridge.ini` ConfigRepository，使用文件/进程锁、原子 replace 与读回校验；未来 schema 与明文 secret 拒绝加载。
- 旧 ParaTranz INI 只读迁移到 credential reference 并写入脱敏 validated backup；删除 `llm_profiles`，LLM/ParaTranz compatibility facade 共用同一 config revision。
- 新增冻结、可哈希的 TranslationRunSpec、ActionPlanner 与 ContextPlanner；provider/base_url/model 原子变更，StagePolicy 排除 hidden/locked，Quest 顺序、unknown context 与 disabled retrieval 行为均可验证。

## 验证

- 锁定 uv：141 passed、12 条既有 direct-mutation/Swig 弃用 warning。
- [EvidenceManifest](../../../test-reports/requirement-code-review-2026-08-18/qa-evidence/task-s04/qa-20260818T092608.226604Z-74735433e0f6/manifest.json) 已通过 verify；Ruff check/format、定向 diff-check 通过。

## 遗留边界

- GUI/Agent/MCP/FOMOD 的完整 RunSpec 生命周期与 checkpoint 接线由 S07 承接；本 Story 不改变活跃 I/O 或持久化权威路径。
