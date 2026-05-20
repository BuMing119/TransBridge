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

### 更新: 2026-05-18 — 主索引从 id 切换为 key

**决策**: TranslationEntryCollection 主索引从 `id` 切换为 `key`。

**原因**: ParaTranz 上传后平台重新生成 `entry.id`，下载回来的 `id` 不再与本地数据匹配。`entry.key` 是跨同步的稳定标识符，应作为条目查找的主键。

**具体变更**:

```python
# 改前
_entries:   dict[str, TranslationEntry]  # id → entry（主索引）
_key_index: dict[str, TranslationEntry]  # key → entry（辅助索引）

# 改后
_entries:  dict[str, TranslationEntry]  # key → entry（主索引）
_id_index: dict[str, TranslationEntry]  # id → entry（辅助索引，保留供内部合并逻辑）
```

- `get(key)` — 主查找（原签名 `get(entry_id)` 改为接受 key）
- `get_by_id(id)` — id 辅助查找（新增，供 EET/XT/SST apply 等内部合并逻辑使用）
- `get_by_key(key)` — 标记 deprecated，转发到 `get(key)`
- `add(entry)` — 主索引 key 由 `entry.key` 构建（原为 `entry.id`）
- `remove(key)` — 按 key 移除（原按 id）
- `__contains__(key)` — 检查 key 是否存在（原检查 id）

**工具层影响**:
- 所有工具的 `entry_id` / `entry_ids` 参数语义从 "entry.id 值" 改为 "entry.key 值"
- `get_visible_entries` 返回的条目中 `key` 为 LLM 应使用的标识符字段
- 工具描述中需显式映射: `entry_ids` 使用 `get_visible_entries` 返回的 `key` 字段

**内部逻辑兼容**:
- EET/XT/SST apply 方法中提取 form_id/edid 的逻辑不变（这些操作在 `entry.id` 格式上运行）
- `_id_index` 保留供这些内部操作通过 id 反查 entry

**影响**:
- 目录变更: 无
- 接口变更: Collection 类 6 个方法签名语义变更；7 个工具模块参数语义更新；`get_visible_entries` 返回格式调整
- 依赖变更: 无新依赖
- 数据兼容: `key` 值与旧 `id` 值相同（当前数据），序列化 JSON 无需迁移
