# Story 02：真实成功链与跨入口合同测试资产

- 所属 Plan：[Quality Foundation and Release Hardening V2](../plan.md)
- 状态：实现完成，综合 QA 通过（2026-08-18）
- 追溯：全部 FR17～FR23 共同验收；R-047
- 依赖：S01；随六个业务 V2 Plan 增量完成

## 验收记录（2026-08-18）

- 已建立 `tests/quality/success_chains.py`（SuccessChain 值对象、7 份真实 fixture sha256 注册表、确定性/parity/归一化 summarize harness）与 `tests/quality/test_success_chains.py`（12 项测试）。
- 覆盖 EET/XT/Strings(SSE)/ESP/ParaTranz 离线真实 parse→write→reparse 成功链、受控 HTTP 后处理链（refine→polish→arbitrate）、FOMOD typed 九阶段链；跨 GUI/Agent 入口 parity；每条链重复 3 次确定性；fixture checksum 防漂移（改坏一字节即检测）。
- `uv run --locked python -m pytest tests/quality -q -p no:cacheprovider`：20 passed；ruff 0 错误。
- Evidence：`qa-20260818T113458.115867Z-5a5e5494471d` → verify `VALID ... (passed)`。
- 已完成：实现、确定性、parity、checksum 门禁；未做：S05 最终 release smoke（另承接）。

## 目标与验收

每项 P0 能力至少一条真实 fixture/受控集成成功链；parse→write→reparse 与 GUI/Agent/MCP/FOMOD parity 可独立运行；mock-only/负路径不能替代完成证据。

## 资产、数据流与接口

版本化 fixture+checksum → canonical use-case request → direct baseline result → 各 entrypoint adapter 执行 → semantic normalizer（忽略 UI 文案/时间）→ compare outcome/diagnostic/ChangeSet/artifact/report → EvidenceManifest。外部服务使用本地受控 HTTP/LLM server，仍执行真实序列化/网络栈。

## 实施步骤

1. 建立最小可再分发 ESP/EET/XT/STRINGS/ParaTranz/FOMOD/project/session corpus 与来源说明。
2. 为每个业务 Story登记 success-chain ID、fixture、expected semantic assertions。
3. parity harness 复用真实 Composition Root，入口不各自 monkeypatch业务实现。
4. golden 更新需显式 review metadata，防止实现错误自动覆盖 expected。
5. 将旧 122 tests 作为回归层，不再作为组合根成功证明。

## 测试、边界与迁移

重复三次验证确定性；fixture checksum/许可/体积检查；至少覆盖合法空和真实非空成功链。无法自动 GUI 驱动时可调用 GUI adapter/use case boundary，但最终 release S05 仍做 GUI 启动 smoke。测试失败进入对应 Story，不在质量层吞掉。
