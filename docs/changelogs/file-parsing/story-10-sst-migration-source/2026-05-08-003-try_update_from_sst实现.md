# 003: try_update_from_sst() 实现

**日期**: 2026-05-08
**类型**: 增
**关联**: Epic: 文件解析 > Story 10: SST 迁移源集成

## 修改文件

### `src/transbridge/converter/translation_entry.py` (改)
- **修改内容**: `try_update_from_xt()` 与 `to_dict()` 之间新增 `try_update_from_sst()` 类方法（约 60 行）。匹配策略：从 `entry.id` 解析 form_id 和 index（ID 格式 `edid:form_id|index~TYPE`，用 `partition("|")` 分割），与 `sst.form_id`（int → `f"{:08X}"` hex 字符串比较）和 `sst.index` 比较。更新条件：stage==STAGE_UNTRANSLATED 且 translation 为空 且 sst.translated_text 非空。更新时保留原有 id/key/original/context/form_id_with_plugin/string_id/dsd_type/dsd_index/editor_id
- **原因**: 实现 FR1.9.2 迁移源集成的核心匹配逻辑。SST 有 form_id（XT 没有），用 form_id+index 双键匹配比 XT 的 edid+index 更精确。ID 中不含 plugin 后缀（已在 create_from_plugin_entry 中剥离），直接用 partition 分割即可
