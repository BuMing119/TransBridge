# Story 04：ESP-ESM Translator XML 与 EET 两种独立来源

- 所属方案：[文件解析](../plan.md)。
- 状态：XML 既有实现保持；v3 二进制 codec 与数字缓存已验证，正式接入与相关验证本地完成，能力实验性。
- 追溯：FR1.5、FR4.2、FR18.8；[ADR-053](../../../docs/adr/053-eet-binary-io-integration.md)、[EET 集成计划](../../eet-integration/plan.md)。

## 目标与接口

按内容识别 ESP-ESM Translator XML（`.xml`）与 ESP-ESM Translator EET（`.eet`）两种独立格式。XML 继续使用 `EET_XmlParser.from_file`、
`find(grup, champ)`、`EET_Entry` 与 `TranslationEntry.create_from_eet_entry`。
二进制使用 `EetDocument.from_bytes/to_bytes` 与 `EET_BinaryParser` facade，完整保留 v3
Classique/BDD 记录、头部、状态、注释、图标、缓存和短语表；其他版本/类型明确拒绝。

## 身份与数据流

内容探测 → `binary.eet` adapter → 完整 `SourceSnapshot` → 命名空间内 `eet-v1:{ordinal}` 条目。
重复文本、FormID 与字段保持独立，codec 版本、来源摘要、原文和记录签名共同限制写回。
工程保存重开不能依赖旧安装路径；使用可信保存快照。缺少新版绑定的来源仍可保留编辑数据，
原格式写回要求重新导入/迁移，不用旧启发定位猜测记录。

向插件迁移与二进制自身身份分开处理：要求 FormID、记录/字段及原文唯一；双方已知 EditorID 必须 casefold 相同，只有未知时才沿用前述定位，EditorID 从 metadata 保留至重开。歧义、未知来源归属
与冲突不得静默覆盖；EET ordinal 不能作为插件主键。

## 写回与边界

统一阶段策略产生有效译文变化后，codec 只重建变化译文的数字缓存。显式空译文有效；
原文哈希、原文侧缓存、原生状态及其他记录保持。默认数字选项固定于已验证 EET4 4.35；
不自动读取安装目录设置。经 staging、完整回读比较、备份与原子发布，失败不改变正式目标。
GUI/助手共用 application，长任务不阻塞 Qt；没有新 MCP 读写工具，不启动外部翻译器。
本 Story 不提供插件→EET、旧版本升级、从零新建完整 EET 或原文哈希重算。

## 文件与验证

- `parser/eet_binary_codec.py`、`eet_binary_wire.py`、`eet_number_cache.py`：格式核心。
- `parser/eet_binary_parser.py`：兼容查询；`parser/eet_parser.py`：XML。
- `converter/eet_entries.py`、`application/io/eet_adapter.py`、`publish/eet_fidelity.py`：本轮正式接入落点。
- 独立 wire fixtures、损坏边界、空译文、重复身份、来源篡改、缓存重建及全文件往返；
  建项→编辑→保存重开→单个/批量写回，以及助手路由回归由集成计划记录。

[实验记录](../../../docs/dev/eet-experiment.md)包含本机只读 IL 与真实语料证据；不能将其称为
公开 EET 源码，也不能把格式测试视为上游程序 GUI 人工验收。
