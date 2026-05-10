# ADR-002: TranslationEntryCollection 作为数据中枢与双索引设计

- **状态**: 已接受
- **日期**: 2026-01 (回顾性记录于 2026-05-06)
- **决策者**: BuMing

## Context

系统需要一个中心化的数据容器来管理翻译条目集合。该容器需要支持高效的按 ID 查找、批量操作、导入导出，并作为 UI 和业务逻辑之间的数据桥梁。同时需要兼容历史序列化数据中的 `key` 字段。

## Decision

采用 **TranslationEntryCollection 作为唯一数据容器**，内部维护**双索引**结构：

```python
class TranslationEntryCollection:
    _entries: dict[str, TranslationEntry]    # id → entry 主索引
    _key_index: dict[str, TranslationEntry]  # key → entry 辅助索引
```

- `add(entry, overwrite=True)` 同时更新两个索引（无独立 `update()` 方法）
- `get(id)` 通过主索引精确查找
- `get_by_key(key)` 通过辅助索引查找（兼容历史数据）
- `filter(predicate)` 返回筛选后的新 Collection
- AppContext 通过 Qt 信号 `collection_changed` 广播所有变更

## Consequences

- **正**: 单一数据源，避免多处维护导致的状态不一致
- **正**: 统一的事件通知机制（信号广播），所有 UI 组件自动刷新
- **正**: 支持多集合管理（AppContext._slots），可同时处理多个插件
- **正**: `key` 与 `id` 相同，避免破坏现有序列化数据
- **负**: 双索引增加内存开销（约 2x 引用）
- **负**: `get_by_key` 是冗余 API，增加接口面积

## Alternatives Considered

- **单一 dict + 双向查找**: 只维护一个 dict，需要 key 查找时遍历 → 拒绝：O(n) 性能不可接受
- **pandas DataFrame**: 用 DataFrame 管理条目 → 拒绝：引入重依赖，对字符串操作无优势
