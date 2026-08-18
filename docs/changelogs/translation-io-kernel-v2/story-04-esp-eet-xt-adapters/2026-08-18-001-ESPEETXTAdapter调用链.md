# Story 04：ESP/EET/XT Adapter 与调用链修复

- 日期：2026-08-18
- Epic/Story：`translation-io-kernel-v2/S04`
- 追溯：FR18.2/18.6/18.8、ADR-017、R-014/R-015
- 状态：实现完成，增量验证通过；待综合 QA

## 实现增量

- 新增 ESP/EET/XT 的 V2 FormatAdapter 和共享 TranslationIoUseCase，不强制底层 parser/writer 采用虚假的无参构造或统一 `parse()` 签名。
- SourceSnapshot 为三种格式保存 source identity、fingerprint、parser/template、EntryKey/source locator、encoding 与 BOM；写入使用完整 locator，重复/缺失/跨 namespace 定位返回结构化冲突诊断，写后进行 reparse 验证。
- 默认 FormatCatalog 注册已经可用的 ESP/EET/XT adapter；ESP localized 保持 experimental，publish 在 S06 前保持 unavailable。
- Agent parser tool 按 format id 调用入口 adapter，CollectionSlot 保存 snapshot 与 format id；GUI/Agent adapter 复用同一 use case。公开 concrete adapter 采用惰性导出，保留兼容 import 并消除 converter/I-O 循环导入。
- 保持工具描述文字不变，仅为本 Story 改动的长 schema 描述添加逐行 E501 注释；未批量格式化历史 `tool_parser.py`。

## 验证证据

- 锁定 uv 成功链：I/O 合同、真实 EET/XT fixture、仓库 ESP、旧 parser/writer 与 Agent 集成共 **231 passed**。
- 8,771 个 warning 中 8,757 个来自旧 `PluginWriter.get_by_key` compatibility facade；其未作为测试完成结论，后续迁移再消除。
- I/O 新/改文件 Ruff check、format check 与定向 `git diff --check` 通过；`tool_parser.py` 对未修改的历史 UP037/E302 债务使用显式 file-scope ignore 复核，S04 新行无 lint error。
- [EvidenceManifest](../../../test-reports/requirement-code-review-2026-08-18/qa-evidence/io-s04/qa-20260818T080656.455807Z-2da9174ec8f8/manifest.json)：`passed`，已用 verify 复验 schema、verdict 与 hash。
- 未执行 Git commit/push。

## 剩余门禁

S04 不实现 staging/backup/atomic publish，fingerprint 检查后的 TOCTOU 封闭由 S06 承接。DSD/SST 仍遵守既有 experimental/unavailable capability，Stage/Localized Strings 完整性由 S05 承接。旧 GUI 屏幕入口仍保留，删除须等跨入口迁移和综合 QA。
