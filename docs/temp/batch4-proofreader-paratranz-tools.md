# Proofreader + ParaTranz 工具 — LLM 使用参考（Batch 4）

> 格式参照 `claude-code-tools-reference.md`，纯使用面。

---

## Proofreader（后处理工具）

### 1. run_postprocess

**描述:**
运行完整的后处理流水线（与界面后处理一致）。

**参数:**
- `phases` (可选): 要运行的阶段列表，可选值: consistency(术语一致性检查)/format(格式校验)/quality_gate(质量关卡)/refinement(LLM修复)/polish(LLM润色)/arbitration(LLM裁决)，默认全部运行
- `entry_ids` (可选): 条目 key 列表，不传则从当前翻译作用域解析

**使用规则:**
- 后台运行，需用户确认（产生LLM费用），write 权限
- 通过 `get_task_status` 查询进度
- 完成后用 `get_quality_report` 查看报告

**返回:** `{task_id, phases, entry_count}`

---

### 2. get_quality_report

**描述:**
获取最近一次质量检查/修复/润色/裁决的报告摘要。

**参数:** 无

**使用规则:**
- 只读操作，返回最近操作的统计结果
- 返回 `{"reports": [{"phase", "total_checked", "issue_count", "auto_fixed", "needs_review", "issues", "timestamp"}]}`，无报告时返回 `{"reports": []}`

---

## ParaTranz（平台同步工具）

### 3. list_projects

**描述:**
列出 ParaTranz 平台上的翻译项目。

**参数:**
- `view` (可选): `"mine"` 只看自己的项目, `"all"` 看全部项目。默认 `"mine"`

**使用规则:**
- 只读操作
- 返回项目列表: `{"projects": [{id, name, visibility}]}`
- 用 `get_project_info` 查看单个项目详情

---

### 4. get_project_info

**描述:**
获取单个 ParaTranz 项目的详细信息。

**参数:**
- `project_id` (可选): 项目 ID。不传则使用当前选中的项目，可通过`list_projects`查看

**使用规则:**
- 只读操作
- 返回 `{"id", "name", "visibility", "member_count"}`

---

### 5. compare_with_remote

**描述:**
对比本地翻译与 ParaTranz 远程翻译的差异。显示前 20 条不同之处。只读。

**参数:**
- `project_id` (可选): 项目 ID。不传则使用当前选中的项目

**使用规则:**
- 只读操作
- 返回 `{"only_local": N, "only_remote": N, "different": N, "same": N, "details": [{key, status}, ...]}`，其中 `details` 最多 20 条
- 用于下载前预览变更

---

### 6. download_entries

**描述:**
从 ParaTranz 获取翻译条目数据。单阶段操作——下载完成后自动附加对比摘要到返回结果中。长运行，需用户确认。write 权限。

**参数:**
- `project_id` (可选): 项目 ID。不传则使用当前选中的项目

**使用规则:**
- 下载完成后自动生成对比摘要
- 下载的数据在返回结果中，需手动处理——不会自动修改当前集合中的条目
- 需用户确认
- 返回 `{"downloaded_count": N, "diff_summary": {"new_from_remote": N, "updated": N}}`（diff_summary 仅在已加载集合时存在；未加载集合时为 null）

---

### 7. upload_entries

**描述:**
上传本地翻译条目到 ParaTranz。长运行，需用户确认。write 权限。

**参数:**
- `project_id` (可选): 项目 ID。不传则使用当前选中的项目
- `entry_ids` (可选): 要上传的条目 `key` 列表（来自 `get_visible_entries`）。不传则上传全部
- `force_overwrite` (可选): 是否强制覆盖已存在的条目。默认 `false`

**使用规则:**
- 需用户确认
- 长运行操作
- 返回 `{"uploaded": N, "total": N, "failed_items": [{"key", "error"}]}`

---

### 8. export_artifact

**描述:**
从 ParaTranz 导出翻译工件（如最终的翻译包）。长运行。write 权限。

**参数:**
- `project_id` (可选): 项目 ID。不传则使用当前选中的项目

**使用规则:**
- 长运行操作
- 返回 `{"result": ...}`（具体结构取决于 API 响应）

---

### 9. get_upload_history

**描述:**
获取 ParaTranz 项目的上传历史记录。只读。

**参数:**
- `project_id` (可选): 项目 ID。不传则使用当前选中的项目
- `limit` (可选): 返回条数上限，默认 20

**使用规则:**
- 只读操作
- 返回 `{"history": [...]}`

---

### 10. get_paratranz_project

**描述:**
获取当前选中的 ParaTranz 项目信息。只读。

**参数:** 无

**使用规则:**
- 只读，返回当前会话中选中的项目
- 返回 `{"id", "name", "visibility"}`，无选中项目时返回 `{"selected_project": null}`

---

### 11. switch_paratranz_project

**描述:**
切换到指定的 ParaTranz 项目。切换后，其他 PT 工具的 `project_id` 参数可省略（自动使用当前选中项目）。write 权限。

**参数:**
- `project_id` (必填): 目标项目 ID（整数）。先调用 `list_projects` 获取项目列表，从中取目标项目的 `id` 字段

**使用规则:**
- 切换前必须先调用 `list_projects` 获取可选的项目 ID
- 切换后，其他 PT 工具的默认项目 ID 自动更新
- 典型流程: `list_projects` → 从中选项目 `id` → `switch_paratranz_project project_id=123` → `compare_with_remote`
- 返回 `{"id", "name", "visibility"}`
