# Story 04：ESP/EET/XT Adapter 与调用链修复

- 所属 Plan：[Translation I/O Kernel V2](../plan.md)
- 状态：实现完成，综合 QA 通过（2026-08-18）
- 追溯：FR18.2/18.6/18.8；ADR-017；R-014/R-015
- 依赖：S01/S02；platform S03/S05

## 目标与验收

ESP/EET/XT 通过统一端口完成 parse→modify→write→reparse；消除 Agent 指向不存在模块、统一无参构造和 Slot 丢 source context；experimental 状态准确。

## 当前与目标数据流及接口

当前 tool_parser 以统一 `cls().parse()` 假设调度，而真实入口分别是 `PluginParser.parse_plugin`、`EET_XmlParser.from_file`、`XT_XmlParser.from_file`；EET/XT Writer 构造需要 parser。目标：ParseRequest → per-format adapter → SourceSnapshot（含 parser/source template/identity）+ entries；WriteRequest 引用 snapshot → adapter apply ChangeSet → staged bytes。

## 实施步骤

1. 分别实现 ESP/EET/XT adapters，不强迫底层类改成同一构造签名。
2. 把 PluginParser 的 plugin/strings lookup/source path 包入 snapshot lease；EET/XT 保存 XML template、encoding/BOM 和定位键。
3. Agent/GUI parser tools 只调用 parse use case，Slot 兼容对象保存 snapshot ref 而非猜测路径。
4. Writer 使用完整 EntryKey/source locator；无法唯一定位返回 conflict diagnostic。
5. 建立 facade 对比旧输出；DSD/SST Reader 仅 experimental，未验证 writer 不注册。

## 边界、迁移与测试

source 文件变化时比较 fingerprint 并拒绝盲写；snapshot 释放后需重解析。修改 parser/writer/tool adapters 与 fixtures，不在此 Story 实现原子发布（S06）。每格式至少一个真实小 fixture 成功链、一个损坏/歧义案例；测试 Agent/GUI 调用相同 use case，EET/XT writer 不再无参构造失败，输出重解析后的身份/文本等价。
