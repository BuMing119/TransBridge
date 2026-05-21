# 工具描述审核报告

**首次审核**: 2026-05-18 | **更新**: 2026-05-21（Story 25+26 变更同步）
**审核范围**: `docs/temp/batch1-5*.md`（当前 47 个工具：editor 7 + translator 9 + writer 1 + parser 6 + proofreader 3 + paratranz 9 + default 7）
**审核规范**: `docs/temp/tool-description-review-spec.md`（15 项检查清单）

---

## 2026-05-21 更新: Story 25+26 变更同步

因 Story 25（后处理统一：5→1）和 Story 26（断点续传与暂停/恢复）导致以下工具描述过时，已于当日修复：

| 批次 | 工具 | 变更内容 |
|------|------|---------|
| 2 | `stop_task` | 新增 `action` 参数（stop/pause/resume），文档补全 |
| 2 | `start_polish` | `entry_ids` 改为可选；新增 `scope` 参数（all/passed/has_issues） |
| 2 | `get_task_status` | 状态值新增 `paused` |
| 4 | `run_postprocess` | 新增 `max_workers` 参数；补全断点续传/暂停恢复说明 |
| 4 | `list_projects` | 参数名修正 `view`→`uid`，值修正 `"mine"/"all"`→`"my"/不传` |
| 4 | `list_quality_reports` | 新工具，已补充完整描述 |

以下旧审计发现因工具已被替换而自然消失：
- `run_consistency_check` / `run_format_validation` / `run_quality_gate` → 被 `run_postprocess` 替代
- `run_llm_refinement` / `run_llm_polish` / `run_llm_arbitration` → 同上
- 报告统计从 45 工具变为 47 工具（-5 proofreader 旧 + 1 run_postprocess + 1 list_quality_reports + 5 parser/writer 合并调整）

---

## 原始 2026-05-18 审核结果（已部分过时，保留供参考）

---

## 致命问题（3 项）

LLM 按文档调用会直接出错。

| 批次 | 工具 | 问题 |
|------|------|------|
| 4 | `run_consistency_check` | 文档说返回"不一致列表及建议修正"，源码实际返回 `{"task_id": task_id}`（异步后台任务）。LLM 期望同步拿到检查结果，实际必须轮询 task_id |
| 4 | `run_format_validation` | 同上。文档说返回"格式错误列表"，源码返回 `{"task_id": task_id}` |
| 5 | `get_statistics` | 返回字段名错误：文档写 `translated_count`，源码实际字段名为 `translated`。LLM 按文档取 `result.data["translated_count"]` 会拿到 None |

---

## 重要问题（5 项）

行为描述与实现不符，LLM 会产生错误预期。

| 批次 | 工具 | 问题 |
|------|------|------|
| 3 | parse_*/import_* (6 工具) | 文档共享规则称"解析结果追加到当前集合"，但区分表称 parse_* "创建新槽位"——内部矛盾。源码中 6 个工具均无 `@require_collection` 装饰器，函数体不操作集合 |
| 4 | `run_consistency_check` | 文档标注"只读操作"，但源码实际启动 `threading.Thread` + `TaskManager` 后台任务，存在副作用 |
| 4 | `run_format_validation` | 同上 |
| 4 | `download_entries` | 首句"下载翻译条目到本地集合"暗示修改集合，但与后文"不会自动修改当前集合中的条目"矛盾。源码不修改 `ctx.collection`，仅创建临时 Collection 返回数据 |
| 3 | parse_sst | 区分表声称所有 parse_* "为 write_back 推断 target 记录路径"，但 `_WRITE_HANDLERS` 中无 sst target |

---

## 遗漏问题（18 项）

缺少信息，LLM 无法正确使用。

### 返回字段缺失（14 项）

| 批次 | 工具 | 缺失内容 |
|------|------|---------|
| 1 | `edit_translation` | 源码返回 `{entry_id, old_translation, new_translation, stage, stage_changed}` 五字段，文档写"无结构化 data" |
| 3 | write_back | 未记录 `{written_count, path[, strings_files]}` |
| 3 | parse_* (4 工具) | 未记录 `{entry_count}` |
| 3 | import_* (2 工具) | 未记录 `{entry_count}` |
| 4 | `get_quality_report` | 未记录 `{reports: [{phase, total_checked, issue_count, ...}]}` |
| 4 | `download_entries` | 未记录 `{downloaded_count, diff_summary}` |
| 4 | `upload_entries` | 未记录 `{uploaded, total, failed_items}` |
| 4 | `export_artifact` | 未记录返回结构 |
| 4 | `get_upload_history` | 未记录 `{history: [...]}` |
| 4 | `get_paratranz_project` | 未记录 `{id, name, visibility}` |
| 4 | `switch_paratranz_project` | 未记录 `{id, name, visibility}` |
| 5 | `get_current_filters` | 遗漏顶层 `active_filter_count` 字段 |
| 5 | `get_statistics` | 遗漏 `untranslated` 字段 |
| 5 | `list_local_projects` | 未记录 `{projects: [{name}]}` |
| 5 | `get_current_project` | 未说明无活跃项目时的返回（`{active_project: null}`） |

### 上下文缺失（3 项）

| 批次 | 工具 | 问题 |
|------|------|------|
| 1 | `set_filters` | `labels` 参数未告知 LLM 通过 `list_labels` 获取可用标签名 |
| 1 | `manage_entry_labels` | `name` 参数（assign/unassign/batch_assign）未告知 LLM 先调用 `list_labels` |
| 2 | `set_scope` | 缺少返回字段描述（实际返回 `{stages, labels, categories, action}` 快照） |

---

## 不精确问题（10 项）

信息有偏差但不致命。

| 批次 | 工具 | 问题 |
|------|------|------|
| 1 | `set_filters` | `search_field` 文档标注默认值 `"original"`，源码 `args.get("search_field")` 无默认值——实际行为是"保持当前值不变"非"默认 original"。用户切到 translation 后不带 search_field 的新搜索仍在 translation 中搜 |
| 1 | `set_filters` | 当所有参数 None 且 clear=false 时，源码返回 `{unchanged: true}` 而非筛选快照。LLM 取 `.stage` 会拿到 undefined |
| 1 | `set_stage` | 文档写"返回 `{updated_count, not_found}`"，但全部成功时不返回 `not_found`，仅 `partial_ok` 时返回 |
| 2 | `stop_task` | 文档将停止全部和停止单个的返回字段混写。实际停止全部返回 `{stopped_task_ids}`，停止单个返回 `{task_id, stopped}` |
| 2 | `get_task_status` | 所有任务返回描述为数组 `[{task_id, ...}]`，实际顶层是 `{active_count, total_count, tasks: [...]}` |
| 4 | `run_llm_polish` | "使用规则"标题重复出现两次（E1 格式） |
| 4 | `run_llm_arbitration` | 同上 |
| 4 | `get_project_info` | 文档说返回"名称、语言对、条目数、成员等"，源码实际只返回 `{id, name, visibility, member_count}`——无"语言对"和"条目数" |
| 3 | parse_*/import_* | 缺少独立的"使用规则"段，三段结构中仅"描述→参数"（E1 格式） |
| 3 | parse_sst | 区分表声称 parse_* 为 write_back 推断 target，但 SST 在 _WRITE_HANDLERS 中无对应 target |

---

## 语言/格式问题（6 项）

| 批次 | 工具 | 问题 |
|------|------|------|
| 2 | start_polish | `**使用规则:**` 标题在副作用前后各出现一次（E1） |
| 2 | stop_task | 同上 |
| 2 | set_term_config | 同上 |
| 2 | set_translation_config | 同上 |
| 2 | set_scope | 同上 |
| 5 | switch_collection | 同上 |

---

## 通过 15 项全检的工具（11 个）

`start_translation`、`get_translation_config`、`get_scope_preview`、`get_visible_entries`、`select_entries`、`list_labels`、`get_app_state`、`list_collections`、`run_llm_refinement`、`list_projects`、`compare_with_remote`

---

## 统计

| 级别 | 数量 |
|------|------|
| 致命 | 3 |
| 重要 | 5 |
| 遗漏 | 17 |
| 不精确 | 10 |
| 语言/格式 | 6 |
| **总计** | **41** |
| 通过工具 | 11 / 45 |
