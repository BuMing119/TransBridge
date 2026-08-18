# Translation I/O Kernel V2

- **状态**：实现完成，综合 QA 通过（2026-08-18）
- **日期**：2026-08-18
- **需求**：FR18、FR17.1～17.4、FR19.1～19.3、NFR2.1、NFR3.1、NFR5.1
- **架构**：ADR-017、ADR-001/002/009/015 的 2026-08-18 增量
- **问题**：R-013～R-020
- **依赖**：`platform-contract-foundation-v2` S01～S03

## 目标与边界

建立 FormatAdapter、ParseRequest/Result、WriteRequest/Result、SourceSnapshot、EntryKey/ExternalEntryRef、StagePolicy 和原子发布合同。P0 发布范围为 ParaTranz JSON 双 ID、ESP/EET/XT 公共成功链与 Localized Strings 完整性；DSD/SST Reader 标为实验性，SST Writer 保持关闭，BSA/Strings 补全和完整上下文为 P1。

本 Plan 不拥有活动 Project/Variant、Task 状态或远端 ParaTranz 同步；解析返回值无共享状态副作用，写入通过显式请求和 mutation/发布端口完成。

## Story 清单

### Story 01：I/O 公共类型、格式目录与 SourceSnapshot

[详细设计](stories/story-01-io-contracts-format-catalog.md)

- **目标**：统一不一致的 parser/writer 构造和隐式格式判断。
- **文件落点**：新增 `src/transbridge/application/io/` 或等价稳定包；兼容 `src/transbridge/parser/`、`writer/`；`tests/contracts/io/`。
- **实施**：定义 FormatId、FormatCapability、Parse/Write Request/Result、SourceDescriptor、SourceSnapshot、Diagnostic 和统计；建立读/写/往返/入口/发布支持矩阵；合法空、partial、failed、cancelled 分离。
- **验收**：格式歧义需确认；DSD JSON/ParaTranz JSON/内部 JSON 不因扩展名混淆；parser 不写 AppContext；SST Writer capability 始终 unavailable。
- **测试**：格式探测 corpus、空文件、损坏/部分损坏/取消、能力矩阵 snapshot。

### Story 02：EntryKey、ExternalEntryRef 与受控 ChangeSet

[详细设计](stories/story-02-entrykey-external-ref-changeset.md)

- **目标**：消除 id/key/external id 混用和无修订直接 mutation。
- **文件落点**：`src/transbridge/models.py` 或新 domain model 包、Collection、migrator/TM adapters、序列化 schema。
- **实施**：EntryKey 加 source namespace；ExternalEntryRef 保存 system/id/metadata；增加 revision/provenance；Collection mutation 通过 expected revision + run_id 的 ChangeSet；提供 V1 key 兼容读取和映射报告。
- **验收**：外部 ID 变化不改变内部身份；不同来源相同 key 不覆盖；冲突 revision 被拒绝；兼容 facade 不形成第二套索引。
- **测试**：身份属性测试、多来源冲突、V1 migration fixture、并发修订拒绝、序列化往返。

### Story 03：ParaTranz JSON 双 ID Adapter

[详细设计](stories/story-03-paratranz-json-adapter.md)

- **目标**：实现明确的离线 ParaTranz JSON 映射并保持可逆字段。
- **文件落点**：新增格式 adapter；迁移 `smart_assistant/file_parser/paratranz_parser.py` 的相关逻辑；`tests/fixtures/paratranz/`。
- **实施**：`key` 映射业务匹配键，`id` 映射可选不透明 ExternalEntryRef；保留 original/translation/context/stage/扩展字段；检测重复 key、冲突 id、字段缺失、类型和非法 stage；导出不合成 id。
- **验收**：有/无 id 均可导入；已有 id 原样导出；重复冲突可定位且不静默覆盖；离线转换不读取网络凭据。
- **测试**：golden round-trip、字段类型/扩展字段、重复与缺失 fixture、数组重排不改变 id。

### Story 04：ESP/EET/XT Adapter 与调用链修复

[详细设计](stories/story-04-esp-eet-xt-adapters.md)

- **目标**：让公开解析/写出入口与真实模块、构造参数和 source context 一致。
- **文件落点**：`src/transbridge/parser/`、`writer/`、Agent parser/writer tools、application adapters、格式 fixtures。
- **实施**：逐格式封装现有实现；修复 EET/XT dispatch；ParseResult 携带 writer 所需完整 source context；Writer 使用完整字符串身份；DSD/SST Reader 保持实验标记。
- **验收**：ESP/EET/XT 可由统一端口 parse→modify→write→reparse；没有无参构造错配；unsupported/experimental 状态准确。
- **测试**：每格式至少一条真实成功链和一条损坏输入；GUI/Agent 调用同一 adapter 的 parity 测试。

### Story 05：StagePolicy 与 Localized Strings 数据完整性

[详细设计](stories/story-05-stage-policy-localized-strings.md)

- **目标**：统一七级 Stage、hidden/locked/空译文及 Localized Strings 原始映射。
- **文件落点**：新增 StagePolicy；修改 writer、AI/TM adapter 接口；`tests/fixtures/strings/`。
- **实施**：使用离散集合而非范围判断；hidden 写原文且不进 AI；locked 不进 AI；locked+空译文阻断正式发布、preview 可显示原文和 blocking diagnostic；Localized Strings 基于完整 SourceSnapshot 更新，不从“有译文条目”重建。
- **验收**：所有 Stage 的 AI/preview/publish 行为矩阵固定；未翻译 string_id、编码、顺序和未修改值不丢失。
- **测试**：Stage 参数化合同、locked-empty 发布阻断、真实 STRINGS/DLSTRINGS/ILSTRINGS golden round-trip。

### Story 06：Staging、验证、备份与原子发布

[详细设计](stories/story-06-atomic-publish.md)

- **目标**：任何 Writer 失败或取消都不破坏既有正式产物。
- **文件落点**：application publish port、filesystem adapter、各 writer facade、`tests/integration/publish/`。
- **实施**：在同卷隔离 staging 生成；格式校验/重解析/摘要比对；冲突与备份策略；fsync/replace 能力按平台处理；取消和异常统一清理；生成 manifest。
- **验收**：生成失败、验证失败、取消和目标冲突均保留旧文件；成功产物可重解析且 manifest 可追溯；临时资源按策略清理。
- **测试**：fault injection、目标已存在、权限失败、取消 race、Windows 非 ASCII/长路径、parse-write-reparse 成功链。

## 追溯与历史状态纠偏提议

| 需求/问题 | Story | 历史 Plan 提议状态 |
|---|---|---|
| FR18.1/2/8；R-014/19 | S01、S04 | `file-parsing`: `partially-verified`, `blocked_by: translation-io-kernel-v2` |
| FR18.6/7；R-015/18 | S04、S06 | `file-writing`: `partially-verified`, publish contract `superseded_by` 本 Plan |
| FR18.3/4/5；R-013/20 | S02、S03 | `core-data-model`、`agent-tool-expansion`: `partially-verified` |
| FR18.9；R-016/17 | S05 | `stage-unification`: `partially-verified`, `blocked_by: translation-io-kernel-v2/S05` |

## 风险、回退与完成门禁

- 风险：V1 key 迁移错误会造成错配。控制：先生成只读映射报告，冲突不自动合并。
- 风险：原子替换在跨卷/网络盘语义不同。控制：staging 固定同卷；能力不足时拒绝正式发布。
- 回退：旧 parser/writer 只能作为 adapter 内部实现切回；公共合同和双 ID 数据不可回退为 `id == key`。
- 完成门禁：六个 Story 各有成功链与 changelog；支持矩阵由可重放 fixture 证明；SST Writer 未被意外开放。
