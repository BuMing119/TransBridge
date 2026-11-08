# ADR-053：二进制 EET 的完整来源身份与原格式发布

- 状态：提议；按用户 bm-pilot 授权完成本地实现与相关验证，能力保持实验性。
- 日期：2026-11-08。
- 需求：FR1.5、FR4.2、FR18.8；[实施计划](../../plans/eet-integration/plan.md)。
- 关系：扩展 [ADR-017](017-translation-io-kernel-v2.md) 的格式合同，取代二进制 EET 仅实验 CLI、产品拒绝的阶段性策略；不改变 ESP-ESM Translator XML 的既有合同。

## 背景与约束

格式名称遵循四种独立格式：ESP-ESM Translator XML（`.xml`）、ESP-ESM Translator EET（`.eet`）、xTranslator XML（`.xml`）、xTranslator SST（`.sst`）。历史技术 ID 分别为 `xml.eet`、`binary.eet`、`xml.xt`、`sst.ssu8` / `sst.ssu9`，仅用于兼容；SSU8/SSU9 是 SST 的版本。这不是同一种 EET 的 XML/二进制变体。两个 XML 格式由内容方言区分，误命名文件也按内容识别。历史 `parse_eet` 工具兼容接收 ESP-ESM Translator 的两种独立格式，写回严格按快照 FormatId 选择 adapter，不依据该简写互转格式。

已验证 EET v3 Classique(1)/BDD(2) 的顺序结构、完整往返与数字缓存。依据是本机 EET4 4.35.0.0 的只读 .NET IL 和真实文件，不是已找到公开 EET 源码。旧启发读取器的头部、字段宽度和记录边界错误已由严格 codec 替代。原文哈希依赖 .NET 运行时，现有数值必须保留；v3 加载不会自动重建译文数字缓存。具体证据见 [EET 实验](../dev/eet-experiment.md)。

同一 FormID、字段和原文可能重复，文本或业务键不能唯一表示物理记录。工程重开后原文件可能不在原位置；恢复和发布必须使用已保存的完整来源内容。

## 决策

将 `binary.eet` 作为实验性正式来源接入共享 I/O，而不是在 GUI/助手各写一套解析器。唯一格式实现为 `parser/eet_binary_codec.py`，数字缓存算法为 `eet_number_cache.py`；`eet_binary_parser.py` 仅提供兼容查询 facade。

格式核心与 Qt、工程状态和发布流程分离。`converter/eet_entries.py` 负责统一身份投影，`application/io/eet_source.py` 兼容识别 ESP-ESM Translator XML 与 ESP-ESM Translator EET 两种独立输入格式，`application/io/eet_adapter.py` 只负责 EET 快照与变更验证，`application/io/publish/eet_fidelity.py` 负责 EET 完整产物核验；这些接入点已完成本地实现。文件扩展名不替代内容探测，写回以已确定的快照格式为准。

## 关键契约

- 来源保存完整 `SourceSnapshot`、来源摘要和 codec 版本；`EntryKey` 包含来源命名空间与 `eet-v1:{ordinal}`。ordinal 为零基物理序号；记录签名绑定原始元数据与原文，不以 FormID 去重。
- 读入保存所有记录，包括重复、空文本、原生状态、注释、图标、双侧缓存与短语表。损坏长度、计数、编码或未知版本明确拒绝，不返回部分成功。
- 写回验证来源摘要、版本、ordinal、签名和原文；旧工程缺少新身份时拒绝猜测写回，要求重新导入/迁移。已保存快照可支撑原路径不可用时的重开和另存。
- 仅按阶段策略产生有效译文变更；显式空译文有效。只重建变化译文的数字缓存，其余原文字段、哈希、原生状态和文件结构保留。默认按已验证的 EET4 4.35 设置生成，选项不由安装目录隐式读取。
- 经 staging → 全文件回读与保真比较 → 备份/原子发布。任何身份或完整性失败阻止该文件发布；批量沿用既有逐文件结果与取消合同。
- GUI 和助手共用创建/追加、迁移、单个/批量原格式写回流程；长任务走 worker/coordinator。GUI 不启动外部翻译器。正式 MCP 仍仅 foundation，未新增文件读写工具。
- 迁移到插件要求 FormID、记录/字段、原文唯一匹配；双方已知 EditorID 时还须 casefold 相同，只有 EditorID 未知时才沿用前述定位。EditorID 从 metadata 保留至工程重开。不把 EET ordinal 当插件 key，也不把未知插件归属的词典记录混入其他来源。

## 备选方案

继续只支持 XML 导出会保留额外操作且丢失二进制原生数据；保留 CLI 而让产品直接拒绝则无法满足工程保存、重开与写回。各入口自行补解析会造成身份与发布策略分叉。因此选择复用唯一 codec 和共享 application 合同。

## 影响与风险

能力矩阵的读取、写出、往返、GUI、助手与发布保持 experimental；正式 MCP 读写 unavailable。当前仅覆盖 v3 类型1/2，不承诺所有游戏、所有 EET 版本兼容。数字缓存设置不保存在文件，生成默认值必须显式记录；无修改往返不重算已有缓存。内部字节/缓存测试不是上游 GUI 打开、另存或应用插件的人工验收。

## 迁移与回退

不自动改写旧工程身份，不新增插件→EET 或从零构造完整 EET，不编辑原文或重算原文哈希。需要回退时停用 `binary.eet` adapter，保留项目原始来源快照与用户译文。旧 XML 流程保持原入口，旧二进制来源按明确诊断重新导入。

## 验证状态

格式实验及正式 application、持久化、GUI、助手与发布相关回归已完成本地验证；最终联合计数、跳过及已复现的基线失败由[实施计划](../../plans/eet-integration/plan.md)统一记录。上游 EET 程序 GUI 仍未人工验收。
