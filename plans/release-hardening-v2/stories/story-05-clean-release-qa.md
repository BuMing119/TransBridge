# Story 05：Clean Build/Installer/Upgrade/Uninstall 与最终 QA

- 所属 Plan：[Quality Foundation and Release Hardening V2](../plan.md)
- 状态：实现完成，综合 QA 通过（2026-08-18）
- 追溯：NFR6.1、全部共同验收；ADR-016；R-001/R-002/R-048/R-050
- 依赖：S01～S04；其余六个 V2 Plan 全部完成

## 验收记录（2026-08-18）

- 综合 QA：显式证据 target 精确为 37；release manifest 1370 passed/5 skipped，加 4 项自引用门禁后合计 1374 passed/5 skipped、0 failed。最新 release-s05 EvidenceManifest `qa-20260818T131538.395439Z-e907afb40368`，37/37 target passed。
- 8 passed（`tests/packaging/test_clean_release_smoke.py` + `test_final_qa_gate.py`）；EvidenceManifest [release-s05](../../../docs/test-reports/requirement-code-review-2026-08-18/qa-evidence/release-s05/qa-20260818T115036.612516Z-28b15265e093/manifest.json) 通过 verify。
- 早期增量报告曾记录 36/36；已由上方综合 QA 的显式 37-target 基线 supersede。最终报告 [final-release-qa-2026-08-18](../../../docs/test-reports/requirement-code-review-2026-08-18/final-release-qa-2026-08-18.md) 为 37/37 Story 目标业务 verdict passed。

## 目标与验收

从干净 checkout/lock 生成 installer+onedir；安装态核心 import、GUI、CLI help、MCP stdio、capability、版本、许可证通过；升级/卸载不破坏用户数据；Blocker/Critical 清零或由用户明确接受风险。

## 发布流与接口

source+lock → clean build env → wheel/onedir → installer → artifact hashes/SBOM/licenses → clean VM install → smoke/contract/performance subset → upgrade from supported previous → uninstall → user-data check → final EvidenceManifest/QA report。便携包若提供走同样 smoke。

## 实施步骤

1. 固化 PyInstaller spec/installer 配置、版本和依赖收集，禁止从系统环境偶然拾取包。
2. 构建后扫描 import graph、licenses、secret、绝对仓库路径和 artifact checksum。
3. 在 Windows 10/11 干净环境安装，执行 GUI 启动/关闭、`transbridge --help`、`transbridge-mcp` initialize/shutdown。
4. 验证 upgrade schema migration/备份和 uninstall 用户 Project/Session/credential policy。
5. 汇总 JUnit/coverage/performance/capability/成功链，按台账 R-001～R-050 给最终证据状态；Blocker/Critical 触发用户门禁。

## 测试、回退与边界

构建失败不发布旧 artifact 冒充新版本；升级失败保持备份/可恢复旧安装。若缺干净 Windows 环境，QA 状态为 Blocker 并暂停，不用开发机替代。此 Story完成后才允许 Phase 6 报告回填。
