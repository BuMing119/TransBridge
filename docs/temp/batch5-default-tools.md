# Default 工具 — LLM 使用参考（Batch 5）

> 格式参照 `claude-code-tools-reference.md`，纯使用面。

---

## 1. get_app_state

**描述:**
返回当前应用的全局状态概览。一站式概览，可用于判断"现在处于什么阶段、可以做什么"。

与 `get_current_filters` 的区别：get_app_state 返回全局状态（含集合+项目+筛选+API），get_current_filters 只返回筛选状态。

**参数:** 无

**使用规则:**
- 只读操作，随时可调用
- 适合在操作前确认当前上下文
- 返回: `{active_collection, esp_file, eet_file, xt_file, project, variant, filters, collection_count, has_active_collection, paratranz_configured}`

---

## 2. list_collections

**描述:**
列出所有已加载的翻译集合及基本信息（名称、条目数、来源等）。

**参数:** 无

**返回字段:**
- `collections`: 数组，每项包含以下字段：

字段名 | 含义
---|---
`key` | 集合的唯一标识键
`label` | 集合的显示名称
`esp_name` | 关联的 ESP 插件名称
`entry_count` | 条目数量
`is_active` | 是否为当前活跃集合

**使用规则:**
- 只读操作
- 返回 `{collections: [{key, label, esp_name, entry_count, is_active}]}`
- `key` 和 `label` 均可用于 `switch_collection`

---

## 3. switch_collection

**描述:**
切换当前活跃的翻译集合。操作后，所有 editor/translator/writer 工具将针对新集合。

**参数:**
- `collection_name` (可选): 集合名称或标识符。先调用 `list_collections` 获取可选值
- `slot_index` (可选): 位置序号（从 0 开始）。先调用 `list_collections` 查看各集合对应的序号

**使用规则:**
- `collection_name` 和 `slot_index` 至少传一个——先调 `list_collections` 获取可选值
- write 权限（修改全局状态）
- 返回 `{active_collection}`，其值为切换后的集合名称

**副作用:**
- 切换后，所有操作（筛选、编辑、翻译、写回等）都针对新集合进行

---

## 4. get_current_filters

**描述:**
返回当前筛选条件的完整快照。

字段名 | 含义
---|---
`active_filter_count` | 已激活的筛选条件数量
`filter_state` | 筛选状态对象

`filter_state` 内部字段：

字段名 | 含义
---|---
`stage` | 当前阶段筛选（列表）
`category` | 当前分类筛选（列表）
`label` | 当前标签筛选（列表）
`search_query` | 搜索关键词
`search_field` | 搜索字段

与 `get_app_state` 的区别：get_current_filters 只返回筛选维度，get_app_state 返回全局状态。

**参数:** 无

**使用规则:**
- 只读操作
- 修改筛选用 `set_filters`
- 返回 `{active_filter_count, filter_state: {stage, category, label, search_query, search_field}}`

---

## 5. get_statistics

**描述:**
返回当前集合的详细统计——条目总数、翻译率（百分比）、按 stage 分布、按分类分布。

与 `get_visible_entries` 的区别：get_statistics 返回统计摘要（数字和分布），get_visible_entries 返回具体条目列表。

**参数:** 无

**使用规则:**
- 只读操作
- 不需要获取具体条目时优先用此工具（比遍历所有页高效）
- 翻译率为百分比（如 45.2 表示 45.2% 已翻译）
- 返回 `{total, translated, untranslated, translation_rate, stage_distribution, category_distribution}`
- **注意**: 当集合为空时，仅返回 `{total: 0, translated: 0}`，不包含 `untranslated`、`translation_rate`、`stage_distribution`、`category_distribution` 等字段

---

## 6. list_local_projects

**描述:**
列出本地工作空间中的所有项目。

**参数:** 无

**使用规则:**
- 只读操作
- 查看当前项目用 `get_current_project`
- 返回 `{projects: [{name}]}`

---

## 7. get_current_project

**描述:**
获取当前活跃项目的基本信息（名称、版本变体、活跃集合名称）。不返回文件路径（安全设计）。

**参数:** 无

**使用规则:**
- 只读操作
- 有活跃项目时返回 `{name, variant, collection}`
- 无活跃项目时返回 `{active_project: null}`
