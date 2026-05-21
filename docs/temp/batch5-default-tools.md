# Default 工具 — LLM 使用参考（Batch 5）

> 格式参照 `claude-code-tools-reference.md`，纯使用面。

---

## Default 工具概览

Default 工具是 LLM 获取应用状态的**唯一途径**。这 7 个工具覆盖了所有可查询的状态维度。

**状态查询速查表（供其他工具参考）:**

| 需要了解的状态 | 使用工具 | 关键返回字段 |
|--------------|---------|-------------|
| 全局上下文 | `get_app_state` | active_collection, esp_file, eet_file, xt_file, filters |
| 已加载集合列表 | `list_collections` | collections (key/label/entry_count/is_active) |
| 当前筛选条件 | `get_current_filters` | filter_state (stage/category/label/search_query/search_field) |
| 翻译进度统计 | `get_statistics` | total/translated/translation_rate/stage_distribution/category_distribution |
| 本地项目列表 | `list_local_projects` | projects (name[]) |
| 当前项目信息 | `get_current_project` | name/variant/collection |

以下工具均为**只读操作**，随时可安全调用，不产生副作用。

---

## 1. get_app_state

**描述:**
返回当前应用的全局状态概览。一站式概览，可用于判断"现在处于什么阶段、可以做什么"。

注意："判断现在处于什么阶段"中的"阶段"指项目工作阶段（是否已加载文件、是否已解析等），与翻译条目的 `stage` 字段（翻译进度标记）是不同的概念。

与 `get_current_filters` 的区别：get_app_state 返回全局状态（含集合+项目+筛选+API），get_current_filters 只返回筛选状态。

**参数:** 无

**使用规则:**
- 只读操作，随时可调用
- 适合在操作前确认当前上下文
- 返回字段说明:
  - `active_collection`: 当前活跃集合名称
  - `esp_file`: 已解析的 ESP 插件文件路径（无则为 null）
  - `eet_file`: 已解析的 EET XML 文件路径（无则为 null）
  - `xt_file`: 已解析的 XT XML 文件路径（无则为 null）
  - `project`: 当前项目名称
  - `variant`: 当前翻译版本变体的名称（字符串，如 `"v1"`）。对应翻译项目的版本标识，用于区分同一项目的不同翻译批次。无版本变体时为 null
  - `filters`: 当前筛选条件快照
  - `collection_count`: 已加载集合总数
  - `has_active_collection`: 是否有活跃集合
  - `paratranz_configured`: ParaTranz API 是否已配置

文件路径字段（esp_file/eet_file/xt_file）仅返回文件名（不包含目录路径，安全设计）。完整路径通过 UI 操作的上下文获取。

---

## 2. list_collections

**描述:**
列出所有已加载的翻译集合及基本信息（名称、条目数、来源等）。

### 翻译集合 (Collection) 概念

翻译集合是 TransBridge 中管理翻译条目的核心容器。每个集合对应一个已解析的翻译源（ESP插件、EET XML、XT XML、SST文件、JSON导入或Strings导入）。

集合的生命周期：
- **创建**: 通过 parser 工具的 `action="create_slot"` 创建
- **激活**: 创建时自动激活，或通过 `switch_collection` 切换。后续所有操作（编辑/翻译/写回）针对活跃集合
- **追加**: 通过 parser 工具的 `action="append"` 向活跃集合追加条目
- **移除**: 在 UI 中手动移除（当前无工具支持）

集合的关键区分：每个集合独立存储，筛选/作用域/选择状态绑定到活跃集合。切换集合后，之前设置的筛选条件、选择状态均不保留。

**参数:** 无

**返回字段:**
- `collections`: 数组，每项包含以下字段：

字段名 | 含义
---|---
`key` | 集合的唯一标识键
`label` | 集合的显示名称
`esp_name` | 关联的 ESP 插件名称。仅为关联的 ESP 插件文件名。对于从 JSON 导入、Strings 导入或其他非 ESP 来源创建的集合，该字段为 null
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
- `collection_name` (可选): 集合的 `key` 或 `label`（来自 `list_collections` 返回的对应字段）。先调用 `list_collections` 获取可选值
- `slot_index` (可选): `slot_index` 为 `list_collections` 返回数组中的位置序号（从 0 开始）。`list_collections` 返回中无显式的 index/slot_index 字段——LLM 需根据 `collections` 数组的顺序推算位置

**使用规则:**
- `collection_name` 和 `slot_index` 至少传一个。同时传入时 `collection_name` 优先，`slot_index` 被忽略
- 建议使用 `collection_name` 参数（通过 `key` 或 `label` 指定），避免依赖数组位置
- 传入无效的 name 或 index 会返回错误
- write 权限（修改全局状态）
- 返回 `{active_collection}`，其值为切换后的集合 `label`

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

**stage 值语义映射:**

| 值 | 含义 | 值 | 含义 |
|----|------|----|------|
| 0 | 未翻译 | 5 | 已审核 |
| 1 | 已翻译 | 9 | 已锁定 |
| 2 | 有疑问 | -1 | 已隐藏 |
| 3 | 已检查 | | |

注意：`filter_state.stage` 返回的是数字列表（如 `[0, 1]`），不是中文标签。此映射表供 LLM 理解含义。

合法范围外的值（4/6/7/8）为 ParaTranz 平台预留，正常操作中不会出现。

与 `get_app_state` 的区别：get_current_filters 只返回筛选维度，get_app_state 返回全局状态。

**参数:** 无

**使用规则:**
- 只读操作
- 修改筛选用 `set_filters`
- `active_filter_count`: 已激活的筛选维度数量（仅统计非空/非null的维度：stage/category/label/search_query；search_field 不计入）
- `filter_state.stage`: 已筛选的 stage 值列表（合法值: 0/1/2/3/5/9/-1），空数组表示未按 stage 筛选
- `filter_state.search_field`: 搜索目标字段（可选值: `"id"`, `"key"`, `"original"`, `"translation"`, `"context"`, `"all"`）
- 返回 `{active_filter_count, filter_state: {stage, category, label, search_query, search_field}}`
- `category`/`label` 的有效值发现途径：`category` 可通过 `get_statistics` 返回的 `category_distribution` 查看当前集合中实际存在的分类；`label` 可通过 `list_labels` 查询所有已定义标签

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
- 返回 `{total, translated, untranslated, translation_rate, stage_distribution, category_distribution}`。集合为空时所有数值字段为 0、分布字段为空对象 `{}`

**返回字段内部结构:**
- `stage_distribution`: `{"未翻译": 120, "已翻译": 80, "有疑问": 5, ...}` — key 为中文 stage 标签，value 为该阶段的条目数
- `category_distribution`: `{"NPC_": 150, "INFO": 45, "BOOK": 30, ...}` — key 为分类名前缀，value 为该分类的条目数。仅返回前 20 个最多的分类

统计基于当前活跃集合的**全量数据**，**不受当前筛选条件影响**。若需了解筛选后的条目，使用 `get_visible_entries`。

---

## 6. list_local_projects

**描述:**
列出本地工作空间中的所有项目。

### 工作空间 (Workspace) 概念

workspace 是 TransBridge 的本地项目工作空间，对应磁盘上的一个目录，包含多个翻译项目（每个项目为子目录）。项目通过 UI 中的项目管理功能创建、打开和关闭。

当前无工具支持创建/删除项目或切换工作空间——这些操作需用户在 UI 中完成。此工具仅提供只读列表供 LLM 了解可用项目。

**参数:** 无

**使用规则:**
- 只读操作
- 查看当前项目用 `get_current_project`
- 返回 `{"projects": [{"name": "项目目录名"}]}`（name 为项目所在目录的名称，不包含完整路径，安全设计）

---

## 7. get_current_project

**描述:**
获取当前活跃项目的基本信息（名称、版本变体、活跃集合名称）。不返回文件路径（安全设计）。

**与 `get_app_state` 的区分:**

| 场景 | 推荐工具 |
|------|---------|
| 需要完整上下文（集合+文件+筛选+API状态） | `get_app_state` — 一站式概览 |
| 仅需项目基本信息（名称/版本/集合） | `get_current_project` — 轻量查询 |
| 需要文件路径信息 | `get_app_state` — `get_current_project` 不返回文件路径（安全设计） |

两者均返回 `variant` 字段。`variant` 是当前翻译版本变体名称，定义见 `get_app_state` 的描述。

**参数:** 无

**使用规则:**
- 只读操作
- 有活跃项目时返回 `{"name": "项目名", "variant": "v1", "collection": "集合名"}`
- 无活跃项目时返回 `{"name": null, "variant": null, "collection": null}`
