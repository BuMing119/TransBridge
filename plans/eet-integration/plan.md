# EET 二进制正式流程接入

- 日期：2026-11-08
- 状态：本地实现、回归及文档完成；按用户 bm-pilot 授权实施，能力保持实验性
- 证据：[EET 格式与数字缓存实验](../../docs/dev/eet-experiment.md)

## 目标与边界

将已验证的 EET v3 Classique/BDD codec 接入来源识别、工程创建/追加、存储重开、译文迁移、
GUI 与助手原格式写回及统一发布。ESP-ESM Translator XML 与 EET 是独立格式，按内容识别。
处理记录重复身份、状态、注释、图标、短语表、原文哈希和双侧数字缓存。
不运行外部翻译器，不修改用户样本；不猜测旧版本格式，也不从插件凭空生成缺失原生数据的新 EET。
正式 MCP 文件读写入口保持现有边界，不将 Python/CLI 能力虚报为 MCP 工具。

## 契约

- 按用户明确约束，ESP-ESM Translator XML（.xml）、ESP-ESM Translator EET（.eet）、
  xTranslator XML（.xml）、xTranslator SST（.sst）是四种独立格式。
  `xml.eet`/`binary.eet`/`xml.xt`/`sst.ssu8|ssu9` 为各自独立的技术标识；内容识别不合并格式，
  历史兼容入口可共享，但展示必须区分，写回不得在 XML、EET、SST 之间隐式转换。
- binary.eet 使用完整 SourceSnapshot；EntryKey 为来源命名空间加版本化物理序号。
- 保存来源摘要、记录签名和 codec 版本，保留重复 FormID、字段、原文；旧身份不猜测写回。
- 译文改变才重建译文数字缓存；默认按 EET4 4.35 配置，原文侧与原生状态保持。
- 写出需来源一致性、条目身份与原文校验；发布经 staging、全文件回读比较、备份与原子替换。
- 迁移只使用可证明的唯一匹配；二进制词典未知插件来源不直接混入其他来源身份。
- 两边都有 EditorID 时还须大小写无关的一致性；重开后的 EditorID 从保存元数据或插件定位键恢复。
- GUI 长任务沿用既有 worker/coordinator；不在 Qt 主线程运行全文件转换。

## Story 01：来源身份与正式 adapter

新增 converter/eet_entries.py、application/io/eet_adapter.py、publish/eet_fidelity.py；
注册实验性能力，解析/取消/损坏诊断，写回身份与快照检查，缓存更新和全文件验证。
验收：重复记录不丢失、空译文有效、未编辑字节保留、恶意身份/损坏快照/旧身份拒绝。

## Story 02：建项、重开、迁移和用户入口

接入来源识别与持久化、GUI 单个/批量写回、助手 parse/append/write；区分 XML 与 binary。
更新旧硬编码的 xml.eet 分支，使用共享内容探测和格式目录；保持已有 SST/JSON/插件路径。
验收：EET→工程→编辑→存储重开→EET，来源路径不可用时使用可信保存快照；
EET→插件译文迁移保守唯一匹配，用户入口选择 EET 实际路由 binary.eet。

## Story 03：验证与文档补齐

更新需求、ADR、原格式计划、开发/用户流程、能力矩阵及索引；补录 3DNPC 与80份 SST 实测证据。
先聚焦测试，再相关持久化/GUI/助手/发布回归和全库 Ruff；真实样本只读，不纳入版本控制。
以本次增量差异记录 changelog，保留历史日志；不提交、不推送。

## 任务阶段

- 分析与架构：已完成；[ADR-053](../../docs/adr/053-eet-binary-io-integration.md) 保持提议状态。
- 开发：核心、GUI、助手、迁移及持久化路径已接入；四格式命名及维护注释已澄清。
- QA：扩大回归 1218 通过、1 跳过、2 项已复现基线失败排除；最终命名调整后核心 41 项、UI 92 项通过。
  全库 Ruff 检查与格式检查通过（2429 文件）。真实 3DNPC 53847 条走正式发布链成功，原文件未改动。
- 文档与记录：需求、ADR、开发指南及相关计划已更新；[增量记录与验证命令](../../docs/changelogs/eet-integration/story-01-to-03/2026-11-08-001-eet-production-integration.md)。

## 风险与回退

仅 v3 已验证；EET 设置不存文件，数字缓存按已验证默认值生成。完整性校验失败不能发布。
功能保持实验性并明确外部程序人工验收边界；回退为关闭 binary.eet adapter，保存的原始快照不变。
原文编辑与从零构建完整 EET 涉及去标点/哈希规则，不在本次原格式译文工作流中引入。

## 职责复核

- 新格式算法、adapter、identity mapper、完整回读和入口识别均在独立模块；没有塞进大型 converter/UI 类。
- `legacy_adapters.py` 737行，主类最多6方法：仅将已有 XML 解析拆出 `parse_bytes`，修正二次读取导致
  词条和快照不一致的风险；binary adapter 独立。暂保留既有多格式模块以避免扩大无关插件/XML重构，
  后续新增 XML/插件格式责任前应按 adapter 拆分。
- `operation_write.py` 507行，最大类3方法：只把 EET 加入已有预检条件，算法不在编排层。
- `parse_coordinator.py` 515行/13方法、`step1.py` 701行/19方法：既有 EET 工作委托独立
  `ui/eet_source.py`，沿用现有 worker。Step1仅增加快照/格式传递，暂保留旧入口兼容层，
  风险是主/旧入口长期并存；下一次增加解析流程或完成旧入口淘汰时应移除其内嵌解析worker。
