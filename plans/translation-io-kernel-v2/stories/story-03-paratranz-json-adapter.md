# Story 03：ParaTranz JSON 双 ID Adapter

- 所属 Plan：[Translation I/O Kernel V2](../plan.md)
- 状态：实现完成，综合 QA 通过（2026-08-18）
- 追溯：FR18.4/18.5、FR22.1；ADR-017；R-013/R-020
- 依赖：S01/S02

## 目标与验收

离线 ParaTranz JSON 将 `key` 作为业务匹配键、可选 `id` 作为不透明 ExternalEntryRef；已有 id 原样导出，不合成；重复/缺失/非法 stage 有可定位诊断；无需网络凭据。

## 数据流与映射

JSON array/object → schema validation → 每条 record 保留 source index → EntryKey(namespace, key) + ExternalEntryRef(`paratranz`, id) + original/translation/context/stage/extensions → ParseResult。导出反向映射，扩展字段按允许策略保留，记录顺序只影响展示，不影响身份。

## 实施步骤与接口

1. 新增 `ParatranzJsonAdapter` 实现 FormatAdapter；定义明确 format id 与 schema version。
2. 验证 key/original 等必需字段、字段类型和离散 stage；id 接受合法 JSON scalar 但内部按不透明规范值保存，不做算术。
3. 检测重复 key、相同 id 指向不同 key、扩展字段冲突；诊断含 record index/key/id。
4. WriteRequest 控制是否保留扩展字段和输出排序；缺 id 时省略，不用 key/数组下标生成。
5. 旧 `smart_assistant.file_parser.paratranz_parser` 改为 facade，并与网络 sync mapper 共享纯映射模块。

## 边界、迁移与测试

空数组是 completed；单条非法时按策略 failed 或 partial，但绝不静默覆盖。离线 adapter 禁止引用 config_manager/client。golden fixtures 覆盖有/无 id、数字/字符串 id、扩展字段、数组重排、重复/冲突/非法 stage、Unicode；执行 parse→write→parse 等价断言和“无 secret/client import”测试。
