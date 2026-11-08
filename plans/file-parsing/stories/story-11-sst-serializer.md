# Story 11：SST 完整写回与新建

- 所属方案：[文件解析](../plan.md)。
- 当前集成：[SST 正式读写与插件导出](../../sst-integration/plan.md)，最终验证证据由该计划汇总。
- 架构：[ADR-052](../../../docs/adr/052-sst-io-integration.md)。
- 状态：2026-11-08 格式层、产品本地发布与 GUI/助手入口集成完成，能力实验性。

## 目标与约束

通过唯一生产 codec 完整写出 SSU8/SSU9，支持原格式译文更新和明确的新建。现有 parser 产生完整 `SstDocument`；serializer 不再模板拼接错误的 26 字节头，不使用假 extra/subrecords，也不猜测缺失的 Header 信息。

内部 `SST_Serializer` 是格式 API；用户可见原格式发布仍经 application 快照身份校验、staging、回读验证、覆盖确认和备份。没有全面上游验收的能力保持实验性，不能仅因内部往返通过而宣称发布级兼容。

## 公开接口

```python
SST_Serializer.from_document(document)
SST_Serializer.from_parser(parser)
SST_Serializer.create_new(
    plugin_name, entries, *, magic=b"SSU9", masters=None, labels=(), flag=0
)
serializer.to_bytes()
serializer.update_record(ordinal, *, text=None, translated_text=None)
serializer.save(path, overwrite=False)
serializer.update_and_save(
    form_id, translated_text, path, overwrite=False, *, ordinal=None, rec=None, index=None
)
serializer.update_entries(updates, path, overwrite=False)
```

`document` 是完整当前模型。`from_parser()` 保留所有原始 metadata，允许 facade 文本编辑；不允许悄悄新增、删除或重排模板 entries，新文件显式用 `create_new()`。新建可直接提供 `SstRecord` 以声明所有字段；兼容 `SST_Entry` 保留其 record 元数据。SSU8 不允许非空 master 表。SSU9 显式 masters 按给定顺序保存，否则以非空 plugin_name 为唯一 master。

## 更新身份与原子性

- `ordinal` 为零基物理记录位置，可精确区分相同 FormID、字段、索引甚至同文本的记录。
- 不使用 ordinal 时，`rec + form_id + index` 必须完整且唯一；此处兼容 index 是 1-based。
- 旧仅 `form_id` 调用只有唯一候选时有效。多义抛出 ValueError，不更新第一条或全部同 FormID 记录。
- 批量请求的重复定位、未知字段、非法值和多义匹配在保存前拒绝。`translated_text=""` 明确清空；`update_record(..., translated_text=None)` 表示不改该字段。
- 序列化和保存成功后才提交批量内存状态；失败保留原 document。默认不覆盖目标，唯一同目录临时文件避免触碰用户已有 `.tmp`。
- 无修改时全文件字节一致；修改指定译文时保留其他记录、原文、masters、labels、flag、StringID、索引、Hash、collab 和 params。底层不自动清除 validated 等状态位。

## 产品原格式发布

`SstAdapter` 从保存快照构造完整 document。输入条目使用命名空间内 `sst-v2:{ordinal}` key，必须有 codec 版本 2、record index/signature、原文和来源绑定。更新先验证身份与快照一致，再用现有阶段策略决定发布文本，最后更新被选 ordinal。

完整候选文件进入 staging，回读检查全部 header 与记录，再交给统一发布。旧来源缺少 v2 身份时返回明确诊断及重新导入/迁移路径，不使用旧错误 key 兜底。选择原格式输出时 SSU8 保持 SSU8、SSU9 保持 SSU9。

## 从插件新建 SSU9

`application/io/sst_export.py` 只接受已保存的真实 Skyrim 插件快照与可验证词条，不从显示文本或旧 SST key 伪造插件身份。

- 从物理字段顺序计算同名字段 index/index_max；根据经过验证的 record/field 映射选择列表 0/1/2。
- 保留真实 FormID、本地化 StringID 和原文；读取 TES4 master 顺序并加入插件名。
- EditorID Hash 使用上游算法；无 EDID 时使用经 load-order 规范化的 FormID 文本 fallback。
- 阶段策略决定译文与发布状态；默认不把未知记录解释成通用列表字段。
- VMAD 属性、非 ASCII master/插件文件名、未知字段映射及不完整或冲突来源暂拒绝，并给出稳定诊断。
- 插件→SSU9 是单独输出格式，不能覆盖输入插件或在导入迁移时隐式触发。

GUI 与助手共用发布用例，并明确显示来源与目标格式。正式 MCP 当前仍为 foundation，未开放文件解析/写回工具，其 read/write 能力为 unavailable。新建 SSU9 仍为实验性，需持续补充不同插件、重复字段与上游重新加载的验收证据。

## 验证

- `test_sst_lab_codec.py` 用独立 wire 夹具验证完整布局、两个格式及损坏拒绝。
- `test_sst_parser_serializer.py` 覆盖 facade 保真、从零新建、空译文、唯一 composite/ordinal 更新、FID 多义拒绝、失败原子性与默认不覆盖。
- 真实本地 19 个输入、9,690 条记录零修改字节一致；所有记录须完整回读，不能只检查已选文本。
- `test_sst_lab_cli.py` 验证诊断脚本调用相同生产 codec。
- application 继续验证源绑定、取消、旧身份拒绝、失败不污染、发布回读和插件新建；入口与持久化回归由正式集成计划记录。
- 历史 changelog/旧 QA 报告作为当时过程保留，不再承担当前格式规范或兼容性结论。
