# 文件解析

> **状态**：Story 01–03、05–08 保持既有实现；Story 04 的 XML 已实现，二进制 EET 按 [正式集成计划](../eet-integration/plan.md)完成本地实现与相关验证、能力实验性；Story 09–11 于 2026-11-08 按上游结构重写并完成本地正式集成，能力保持实验性。[SST 正式集成](../sst-integration/plan.md)汇总最终验证证据。
> **模块**：`src/transbridge/parser/`、`src/transbridge/converter/`、`src/transbridge/application/io/`、`src/transbridge/ui/`

## 概述

解析 ESP/ESM/ESL 插件、ESP-ESM Translator XML/二进制 v3 Classique/BDD、xTranslator XML、XT SST 二进制、DSD JSON 与 Strings 文件，统一转换为 TranslationEntry。SST 的格式、身份和发布边界遵循 [ADR-052](../../docs/adr/052-sst-io-integration.md)，不再沿用历史错位扫描布局。

## Story 清单

- Story-01：ESP/ESM/ESL 插件解析（PluginParser + SSEPluginWithContext）——已实现。
- Story-02：本地化 Strings 文件读取（松散文件 + BSA）——已实现。
- Story-03：上下文提取（NPC 性别/种族/职业、INFO 说话者/情绪、DIAL 任务关联）——已实现。
- [Story-04：ESP-ESM Translator XML 与二进制来源](stories/story-04-eet-parser.md)——XML 已实现；二进制完整 codec 已验证，正式接入与相关验证本地完成、实验性。
- Story-05：xTranslator XML 解析——已实现。
- Story-06：DSD JSON 导入——已实现。
- Story-07：Strings 文件导入——已实现。
- Story-08：FormID 转换工具——已实现。
- [Story-09：SST 完整解析与物理身份](stories/story-09-sst-parser.md)——本地集成完成，实验性。
- [Story-10：SST 译文迁移](stories/story-10-sst-migration-source.md)——唯一真实字段与原文匹配已接入，实验性。
- [Story-11：SST 完整写回与新建](stories/story-11-sst-serializer.md)——本地发布与入口集成完成，实验性。

## 关键文件

- `src/transbridge/parser/plugin_parser.py`：ESP/ESM 解析入口。
- `src/transbridge/parser/plugin/plugin_with_context.py`、`plugin_string_with_context.py`、`item.py`：插件上下文及中间结构。
- `src/transbridge/parser/strings_file.py`：Strings 读取与查找。
- `src/transbridge/parser/eet_parser.py`、`xt/xt_parser.py`：EET/xTranslator XML。
- `src/transbridge/parser/eet_binary_codec.py`、`eet_number_cache.py`：EET v3 完整模型与数字缓存；application adapter 与记录身份由 [ADR-053](../../docs/adr/053-eet-binary-io-integration.md)规定。
- `src/transbridge/parser/xt/sst_codec.py`：唯一的 SSU8/SSU9 完整二进制模型和读写实现。
- `src/transbridge/parser/xt/sst_parser.py`、`sst_serializer.py`：兼容公开入口及精确更新/保存。
- `src/transbridge/converter/sst_entries.py`：版本化物理身份与原始记录签名。
- `src/transbridge/application/io/sst_adapter.py`、`sst_export.py`、`migration_import.py`：来源快照、原格式发布、插件新建及迁移。
- `src/transbridge/parser/utils/fromid_trans.py`：FormID 转换。

## Story-09：XT SST 完整解析

对应 FR1.9；技术依据为固定 xTranslator commit `9aa38d60860273401f8bb0dd1557c82a295c41b3` 的 `SaveSSTFile/loadSstEdit` 与 `rEspPointerLite`。详细布局见[开发文档](../../docs/dev/parser.md)。

SSU8/SSU9 都是顺序文件：magic 与 flag、可选 master 表、协作标签表，随后为连续记录。SSU8 无 master 表；其他记录布局相同。每条固定部分 27 字节，后接小端 int32 字节长度与 UTF-16LE 原文/译文。没有嵌套记录、未知尾部或大端长度。

验收要求：

- `SstDocument` 保留全部文件头和记录字段，空字符串、未知签名和原始 UTF-16 code units 均可往返。
- 严格验证计数、长度、list index、数值范围与 EOF；损坏文件不返回部分成功。
- `SST_Parser.from_file/from_bytes`、`SST_Entry` 及 JSON/CSV 入口保留。`document` 提供完整数据；`record`/`ordinal` 提供物理定位；兼容 `index` 为 1-based。
- 规范 `SstRecord.index` 为零基字段索引；`index_max` 为同名字段最大索引；`edid_hash` 为 EditorID Hash。兼容 `group_index/global_seq/f2` 不得再解释为历史猜测字段。
- 来源 key 为命名空间内 `sst-v2:{ordinal}`；保存 codec 版本、记录签名、原文与来源 SHA-256，不以文本或 FormID 去重。
- 本地 19 个真实输入完整解析 9,690 条，零修改字节一致；`hlioremi` 8,604 条、`pets` 171 条。历史实验输出可被明确拒绝，不作为正例。

## Story-10：SST 译文迁移

对应 FR1.9.2。SST 自身建项保留物理来源身份；将 SST 译文迁移到插件则通过独立候选草稿，不把 SST 的 ordinal 当作插件身份。

迁移必须同时满足真实 FormID、record/field 签名、原文完全一致，且源和目标定位唯一。SST 字段索引与插件业务索引的关系未逐类证明前，不按旧 `form_id + index` 规则猜测覆盖。跨命名空间、重复候选和无法证明的映射给出稳定冲突诊断。沿用既有状态/覆盖策略，解析失败、取消或活动版本变化不修改当前集合。

验收要求：

- SSU8、SSU9 均有原文与译文，不将 SSU8 当作单语格式。
- 相同 FormID 的不同字段或原文保持独立；歧义不自动覆盖。
- GUI 与助手共享权威准备/提交流程；长任务使用既有 worker/TaskRuntime。正式 MCP 仍为 foundation，不提供文件解析/写回工具，read/write 仍为 unavailable。
- 空源、无译文、损坏内容及格式提示冲突均有可操作诊断。
- 旧 SST 工程仍可编辑；缺少 v2 物理身份时原格式写回拒绝，重新导入来源后可迁移已有译文。

## Story-11：完整 SST 写回与新建

对应 FR1.9.5；完整产品范围由 [SST 正式集成](../sst-integration/plan.md)继续跟踪。`SST_Serializer.from_document/from_parser/create_new` 共用生产 codec，支持 SSU8/SSU9；不再保留另一套实验实现。

验收要求：

- 未修改时文件字节完全一致；修改仅作用于指定物理记录的文本，其他记录、masters、协作标签、状态和身份字段保持。
- 更新以 `ordinal` 或唯一 `rec + form_id + index` 定位。旧单独 FormID 调用在多义时抛错；批量重复定位或非法字段在写入前拒绝。
- 明确空译文可写回；失败不改变原有文件或序列化器内存状态。默认输出到新文件，已有目标拒绝；显式覆盖时使用同目录唯一临时文件。
- 产品原格式写回验证快照、来源命名空间、codec 版本、ordinal、记录签名与原文；使用 staging、完整回读核验、覆盖确认与备份的统一发布流程。
- 从真实已保存 Skyrim 插件新建 SSU9，映射物理字段索引、StringID、masters 和 EditorID Hash。无 EDID 使用经过规范化 FormID 的上游 fallback。VMAD、非 ASCII master/插件名和未知字段映射暂拒绝。
- GUI 与助手明确区分来源格式和输出格式；SST 原格式写回与插件→SSU9 均为实验性。正式 MCP 不因共享 application 实现而获得文件读写能力。DSD 的既有实验性级别不受本次修改影响。

## 验证与证据边界

格式回归位于 `tests/parser/test_sst_lab_codec.py`、`test_sst_parser_serializer.py`、`test_sst_lab_cli.py`；application、持久化和入口验收由新集成计划记录。历史 changelog 与测试报告保持历史事实，早期的记录计数、截断结论和布局猜测不能继续作为当前规范或发布门禁依据。
