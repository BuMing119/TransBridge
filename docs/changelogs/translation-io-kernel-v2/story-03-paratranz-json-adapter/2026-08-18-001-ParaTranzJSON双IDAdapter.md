# Story 03：ParaTranz JSON 双 ID Adapter

- 日期：2026-08-18
- Epic/Story：`translation-io-kernel-v2/S03`
- 追溯：FR18.4/18.5、FR22.1、NFR2.1、ADR-017、R-013/R-020
- 状态：实现完成，增量验证通过；待综合 QA

## 实现增量

- 新增纯离线 `ParatranzJsonAdapter` 与共享映射模块：ParaTranz `key` 始终形成 EntryKey 的 local key；可选 `id` 仅形成 `ExternalEntryRef("paratranz", id)`，缺失值绝不从 key 或数组位置合成。
- `id` 接受并保持严格 JSON scalar（string/int/finite float/bool/null）的类型身份；拒绝 NaN、Infinity 和非标量值，且可区分显式 null 与字段缺失。
- 解析/写入保留 original、translation、context、stage 和受策略控制的扩展字段；默认 namespace 由排序后的业务 key 集合导出，数组重排不改变身份。
- 重复 key、跨 key 的重复 id、核心/扩展字段冲突及非法 stage 返回包含 record index/key/id 的诊断；合法空、partial 和 failed 保持互斥；真实 TaskRuntime cancellation 在 parse/write 均可中止。
- 默认格式目录仅注册已经实现的 ParaTranz V2 adapter；空 JSON 继续返回 DSD/ParaTranz/internal 格式歧义，publish 在 S06 原子发布门禁前明确 unavailable。
- 旧 `smart_assistant.file_parser.paratranz_parser` 的 JSON 解析和分类导出改为 V2 facade，未引入网络 client、配置或凭据依赖。

## 验证证据

- 锁定 uv 成功链：`tests/contracts/io`、`tests/converter/test_translation_entry.py`、`tests/converter/tests_translation_entry_collection.py` 与 `tests/paratranz` 共 **118 passed**；12 个 warning 均为既有 compatibility facade 弃用提示。
- 变更范围 Ruff check 通过；新增 I/O/合同测试 Ruff format check 通过。
- [EvidenceManifest](../../../test-reports/requirement-code-review-2026-08-18/qa-evidence/io-s03/qa-20260818T074317.837175Z-46043dfd68ad/manifest.json)：`passed`，已用 verify 复验 schema、verdict 与 hash。
- 未执行 Git commit/push。

## 剩余门禁

S03 不提供网络同步或远端 artifact 事务；这些由 ParaTranz sync V2 承接。文件发布仍未采用 staging/backup/atomic replace，故 publish capability 保持 unavailable，待 I/O S06 通过验证后才能开放。ESP/EET/XT adapter、Stage/Localized Strings 完整性也仍由 S04/S05 承接。
