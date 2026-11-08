# Story 09：XT SST 完整解析与物理身份

- 所属方案：[文件解析](../plan.md)。
- 当前集成：[SST 正式读写与插件导出](../../sst-integration/plan.md)，最终验证证据由该计划汇总。
- 架构：[ADR-052](../../../docs/adr/052-sst-io-integration.md)。
- 状态：2026-11-08 格式层重写与本地来源集成完成，能力实验性。

## 目的与边界

用 xTranslator 真实顺序布局完整读取 SSU8/SSU9，恢复曾被错位扫描遗漏的物理记录。保留 `SST_Parser`、`SST_Entry`、JSON/CSV 导出入口，不继续维护扫描猜测或伪嵌套解析。Reader 与 Writer 共用 `parser/xt/sst_codec.py`。

上游依据固定为 commit `9aa38d60860273401f8bb0dd1557c82a295c41b3` 的 [TESVT_SSTFunc.pas](https://github.com/MGuffin/xTranslator/blob/9aa38d60860273401f8bb0dd1557c82a295c41b3/TESVT_SSTFunc.pas#L203-L435) 和 [TESVT_typedef.pas](https://github.com/MGuffin/xTranslator/blob/9aa38d60860273401f8bb0dd1557c82a295c41b3/TESVT_typedef.pas#L68-L97)。

## 文件与记录模型

`SstDocument` 保存 magic、flag、masters、labels 和有序 records。magic 为 `SSU8` 或 `SSU9`；公共 flag 是一字节。SSU9 有 master 表，SSU8 无此表。两者都有协作标签表。数量、标签 ID、字符串字节长度为小端 signed int32；文本为 UTF-16LE。

每条记录固定头为 `<BiI4s4sHHIBB`，共 27 字节，依次为：

1. `list_index`：0/1/2 文本列表。
2. `string_id`：signed int32，保留原始 bit pattern 的签名语义。
3. `form_id`：uint32 真实插件 FormID。
4. `record_type`、`field_type`：各四字节签名，未知签名仍保留。
5. `index`、`index_max`：uint16；前者为零基物理同名字段索引，后者为同名字段最大索引。
6. `edid_hash`：uint32，EditorID 的 xTranslator StringHash。
7. `collab_id`、`params`：各一字节，完整保留状态位。

固定头后是原文长度/原文、译文长度/译文。记录连续到 EOF；没有记录总数、终止符、任意填充或嵌套 subrecord。Hash 对 UTF-16 code unit 的低字节做 FNV-1a，不按 UTF-8 字节计算。

## 公开接口与转换

`SST_Parser.from_file(path)` 读取一次文件；`from_bytes(data, source_name=...)` 使用已捕获快照；`from_document(document)` 可接收完整模型。三者产生相同投影，`parser.document` 保留全文件。

`SST_Entry.record` 指向完整记录，`ordinal` 为文件内零基物理位置，`rec` 为 record/field 拼接，兼容 `index` 为 `record.index + 1`。`group_index` 只兼容 `index_max`，`global_seq` 只兼容 `string_id`，`f2` 只兼容 `edid_hash`。`SST_Subrecord` 保留类型兼容但解析不生成该类型。不要在调用方解释旧 `_raw/_tail` 偏移。

转换通过 `converter/sst_entries.py` 的 `create_sst_entry/document_entries`。使用来源命名空间及 `sst-v2:{ordinal}` key，保存 `sst.codec_version=2`、`sst.record_index`、`sst.record_signature`，正式来源还绑定 SHA-256 和完整快照。重复文本、FormID 或相同字段仍是独立记录，不能沿用旧 key 去重。

旧来源继续允许打开编辑；缺少 v2 身份时禁止原格式猜测写回，提示重新导入来源后迁移译文。新身份不自动回填到已存在旧版本。

## 错误处理

负长度、奇数 UTF-16 长度、越界计数、截断、无效 list index 和数值溢出均抛出带字段/偏移的 ValueError。Parser 补上来源名并保留 cause。解析失败不返回部分 entries。空文件头可表示零记录，但不足完整表头的文件不属于空文件成功情况。

UTF-16 使用 surrogatepass 保留上游原始 code units；四字节签名用 Latin-1 做无损字节映射，不以已知后缀筛掉新类型。空原文、空译文和补充平面字符均应可往返。

## 验证

- 独立 struct 合成夹具覆盖两版本、标签、三列表、空文本、长文本、Unicode、非法计数/长度与每个截断边界。
- 19 个本地真实输入、9,690 条记录全部顺序读到 EOF；`hlioremi` 为 8,604 条，`pets` 为 171 条。
- 两份历史 `_test*.sst` 错误文件头被拒绝；不能作为生成器成功证据。
- `tests/parser/test_sst_lab_codec.py` 验证 wire，`test_sst_parser_serializer.py` 验证 facade 与公开 API。
- 真实样本缺失时相关本地语料测试明确 skip，不能算作完整语料验收；合成 wire 测试始终运行。

来源导入、写回、迁移、GUI/助手的最终验收证据由正式集成计划汇总；完整内部往返不提升为全面发布兼容证明。正式 MCP 当前仅 foundation，文件解析/写回工具未开放，read/write 为 unavailable。
