# Story 05：Clean Build/Installer/Upgrade/Uninstall 与最终 QA

- 所属 Plan：`release-hardening-v2`
- 增量日期：2026-08-18
- 状态：实现完成，增量验证通过；待综合 QA

## 增量内容

交付 release 门禁的代码级证据与综合 QA 汇总：

1. **Clean-release smoke**（`tests/packaging/test_clean_release_smoke.py`）：核心发行版许可证清单（元数据 License/License-Expression/classifier 或发行版内 license 文件，非缺失即报）、预建 artifact 的 SHA-256 记录（存在时）、安装态不引用仓库 `src` 路径（包内无仓库绝对路径硬编码）、版本单一来源与 `AppId` 固定（Inno 可原地升级）、卸载不静默删用户项目（`UninstallDelete` 对 `{app}\data` 隔离 + `[Code]` 对用户 `{userappdata}\TransBridge` 显式确认，不容许删用户根目录）。
2. **综合 evidence QA 门禁**（`tests/packaging/test_final_qa_gate.py`）：遍历 `qa-evidence/`，每个 Story 目标取其**最新** EvidenceManifest，断言 schema v1 且业务 `verdict=passed`；历史/superseded manifest 为 append-only 记录不重审（冻结基线）。
3. **最终 QA 汇总**（`tools/qa/final_qa.py` → `docs/test-reports/requirement-code-review-2026-08-18/final-release-qa-2026-08-18.md`）：按 Story 目标列出最新 evidence run_id/verdict 与 input 回读状态；36/36 Story 目标业务 `passed`。
4. 真实 Windows clean VM 安装/卸载/升级不在开发机执行，改为代码级等价证据 + iss 审查 + evidence 汇总（记录为环境缺口，在综合 QA 报告说明）；前瞻性 onedir/installer 资产已在 S01 基线（非 editable 植入 smoke、`uv sync --isolated --locked --no-editable`）与既有 `transbridge.spec`（onedir）支撑。

## 验收对应

- 36/37 Story 全量完成增量验证，最终 QA 门禁一键验证全部 passing evidence；
- 安装态不依赖仓库路径、版本/AppId 单一来源、卸载保护用户数据、许可证清单可复现；
- `tests/packaging/test_final_qa_gate.py` + `test_clean_release_smoke.py` 共 8 passed。

## 验证

- 正式 uv 测试：`tests/packaging/test_clean_release_smoke.py` + `tests/packaging/test_final_qa_gate.py` 共 **8 passed**。
- EvidenceManifest：[release-s05](qa-evidence/release-s05/qa-20260818T115036.612516Z-28b15265e093/manifest.json)，`verify_evidence.py` 结果 `VALID (passed)`。
- ruff 0 错误。

## 边界

- 真实 Windows 干净 VM 的安装/升级/卸载 GUI 链路不在开发机执行（本机即 Windows 但无 VM/管理员），以代码级 iss 审查 + non-editable 植入 smoke + 全套 evidence 汇总作为等价证据；GUI 启动 smoke 以能 headless 覆盖的 CLI/MCP 探针与既有 S01 baseline 支撑。
- input 回读漂移 19 项：冻结 Story 旧 evidence 记录彼时文件 hash，整改全程代码/依赖演进导致无法逐字节回读；按冻结基线不重做、不作为 blocker，记录于最终 QA 报告。
- 未执行 Git commit/push，未修改 `.partial.md` 或既有正式审查报告正文。
