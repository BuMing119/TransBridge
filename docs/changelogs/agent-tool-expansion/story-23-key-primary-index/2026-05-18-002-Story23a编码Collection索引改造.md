# 002: Story 23a 编码 — Collection key 主索引改造

**日期**: 2026-05-18
**类型**: 改
**关联**: Epic: Agent 工具系统全面扩展 > Story 23a: Collection 索引改造

## 修改文件

### `src/transbridge/converter/translation_entry_collection.py` (全文重写)
- **修改内容**: `_entries` 主索引 key 从 `entry.id` 改为 `entry.key`；`_key_index` 更名为 `_id_index: {id → entry}`（辅助索引）；`get(key)` 按 key 主查找；新增 `get_by_id(id)` 按 id 辅助查找；`get_by_key(key)` 标记 deprecated 转发到 `get()`；`add()` 主索引由 `entry.key` 构建 + 同步更新 `_id_index`；`remove(key)` 同时删除双索引条目；`__contains__(key)` 按 key 检查；所有内部 apply 方法改用 `_entries[entry.key]` + `_id_index[entry.id]` 双索引更新；`update_from_translated_plugin` 按 id 查找改用辅助索引
- **原因**: ADR-002 更新 2026-05-18 — ParaTranz 同步后 `entry.id` 重新生成导致跨同步查询断裂，`entry.key` 为稳定标识符升为主索引

## 测试结果
- 120 passed, 3 pre-existing failures
- 新 API 验证：get by key / get_by_id / deprecated get_by_key / contains / remove 全部通过
