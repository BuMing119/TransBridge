# Story 01：I/O 公共类型、格式目录与 SourceSnapshot

- 所属 Plan：[Translation I/O Kernel V2](../plan.md)
- 状态：实现完成，综合 QA 通过（2026-08-18）
- 追溯：FR18.1/18.2/18.8；ADR-017；R-014/R-019
- 依赖：platform-contract-foundation-v2 S01～S03

## 目标与验收

统一 parser/writer 调用合同和格式目录；合法空、partial、failed、cancelled 分离；ParaTranz/DSD/内部 JSON 不因扩展名混淆；parser 无共享状态副作用；SST Writer 始终 unavailable。

## 数据流与接口

`ParseRequest(source, format_hint, options, context)` → FormatCatalog resolve/probe → `FormatAdapter.parse` → `ParseResult(format, source, snapshot, entries, diagnostics, stats, outcome)`。写路径接受独立 WriteRequest。计划类型：`FormatId`、`FormatCapability`、`SourceDescriptor`、`SourceSnapshot(bytes/hash/encoding/format metadata)`、`ParseStats`。探测结果可为 exact/ambiguous/unsupported。

## 实施步骤与文件

1. 新增 `application/io/contracts.py`、`catalog.py` 和 Protocol；合同不导入现有 parser 或 PyQt。
2. 注册明确 format IDs：ESP/ESM、EET XML/Binary、XT XML、SST Reader、ParaTranz JSON、DSD JSON、internal JSON、STRINGS variants。
3. 探测使用 magic/schema/root element，扩展名只作提示；歧义返回 diagnostic 等待调用方选择。
4. 对现有 parser 加 adapter，捕获错误为 typed diagnostic；零条合法输入为 completed，不把异常变空集合。
5. 由 registry 生成支持矩阵，experimental/unavailable 不可被入口提升。

## 边界、迁移与测试

SourceSnapshot 大文件可持有路径+hash/必要元数据，生命周期由调用方 lease 管理；取消不返回可发布 snapshot。旧 parser 方法先由 adapter 调用，旧入口转 facade。测试使用格式 corpus 覆盖空、magic/schema 冲突、损坏、部分损坏、取消和 capability snapshot；执行 `pytest tests/contracts/io tests/parser -q`，SST serializer 存在也必须验证 writer capability 为 unavailable。
