# 003: SSU8 index 修正为 per-EDID 子索引

**日期**: 2026-05-08
**类型**: 改
**关联**: Epic: 文件解析 > Story 09: XT SST 二进制解析器

## 修改文件

### `src/transbridge/parser/sst_parser.py` (改)

- **修改内容**:
  - `SST_Entry.index` 语义变更：原为全局 sID 序号（SSU8 二进制尾部 `seq` 字段），改为 per-EDID 子索引 `(field_a & 0xFF) + 1`，与 XT XML 的 REC `id` 属性 + 1 逻辑一致
  - 新增 `SST_Entry.global_seq` 字段：保留原全局序号，用于排序和调试参考
  - `_parse_ssu8()`: `field_a` 从忽略变量改为提取 per-EDID index 的来源；原 `seq` 变量重命名为 `global_seq` 并映射到新字段
  - `to_csv_file()`: 新增 `global_seq` 列
- **原因**: 修复前所有 116 条匹配条目 SST index 与 XT index 均不一致。`field_a` 的低字节编码了 xTranslator 的 REC sub-index（0-based），高位字节存储 idMax。忽略此字段导致生成的 `TranslationEntry.id` 与插件解析结果无法匹配，迁移源合并将全部失败

### `scripts/sst2json.py` (改)

- **修改内容**:
  - `--pretty` 模式：SSU8 条目输出新增 `global_seq` 字段
  - `--stats` 模式：条目行新增 `idx` 和 `glob` 显示
- **原因**: 适配 `SST_Entry` 新增的 `global_seq` 字段，保持工具输出完整
