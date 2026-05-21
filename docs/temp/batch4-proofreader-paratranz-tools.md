# Proofreader + ParaTranz 工具 — LLM 使用参考（Batch 4）

> 格式参照 `claude-code-tools-reference.md`，纯使用面。

---

## Proofreader（后处理工具）

### 1. run_postprocess

**描述:**
运行完整的后处理流水线（与界面后处理一致）。涵盖术语一致性检查、格式校验、质量关卡、LLM修复、LLM润色、LLM裁决六个阶段，按固定顺序执行。

**六阶段说明:**
- `consistency`(术语一致性检查): 检查译文中的术语是否与术语库一致，标记不一致处
- `format`(格式校验): 检查译文格式（标点、变量占位符、XML标签等）是否正确
- `quality_gate`(质量关卡): 汇总前两阶段结果，判定每条译文是否通过质量门槛
- `refinement`(LLM修复): 调用 LLM 自动修复未通过质量关卡的译文
- `polish`(LLM润色): 调用 LLM 对已通过检查的译文进行文笔润色提升
- `arbitration`(LLM裁决): 调用 LLM 对修复/润色后的译文做最终质量裁决（通过/打回/待审）

与 `start_polish` 的区别：`run_postprocess` 是完整六阶段流水线（含一致性检查、格式校验等非 LLM 阶段）；`start_polish` 仅做 LLM 润色（相当于本工具的 `phases=["polish"]`），但提供 `intensity` 和 `scope` 参数进行精细控制。

**参数:**
- `phases` (可选): 要运行的阶段列表，可选值: `"consistency"`(术语一致性检查)/`"format"`(格式校验)/`"quality_gate"`(质量关卡)/`"refinement"`(LLM修复)/`"polish"`(LLM润色)/`"arbitration"`(LLM裁决)，默认全部运行
- `entry_ids` (可选): 条目 key 列表。不传则从当前翻译作用域解析，作用域未设置时处理全量条目。scope 来源: `entry_ids` 不传时，自动从 `ctx.translation_scope`（由 `set_scope` 设置）解析条目范围
- `max_workers` (可选): LLM并行工作线程数，默认 1，最大 8

**使用规则:**
- 后台运行，需用户确认（产生LLM费用），write 权限
- 通过 `get_task_status` 查询进度，支持 `stop_task action=pause` 暂停和 `stop_task action=resume` 恢复
- 每阶段完成后自动保存断点，再次启动时可续传
- 完成后用 `get_quality_report` 查看本次报告，用 `list_quality_reports` 查看历史报告
- 返回: `{task_id, phases, entry_count}`

---

### 2. get_quality_report

**描述:**
获取最近一次质量检查/修复/润色/裁决的报告摘要。

**参数:** 无

**使用规则:**
- 只读操作，始终返回最近一次 `run_postprocess` 的报告摘要
- `reports` 为聚合后的单条报告（数组格式仅为保持一致性），覆盖最近一次 `run_postprocess` 全部已完成阶段的结果。不是每阶段一条报告。
- 若最近一次运行为 polish-only（通过 `start_polish`），报告的 `phase` 字段为 `"polish"`，字段结构略有不同（含 `entry_count`、`polish_level`、`scope` 等）
- 返回 `{"reports": [{"phase": "polish", "total_checked": 150, "issue_count": 12, "auto_fixed": 8, "needs_review": 4, "issues": [{"entry_key": "...", "description": "..."}], "timestamp": "2026-05-21T10:30:00"}]}`，无报告时返回 `{"reports": []}`
- 如需浏览历史报告文件列表，用 `list_quality_reports`

---

### 3. list_quality_reports

**描述:**
列出历史后处理报告文件。每次 `run_postprocess` 完成后会生成 Excel 报告保存在磁盘上。用此工具浏览归档的报告文件列表。注意：`get_quality_report` 始终返回最近一次运行的摘要，无法加载历史报告详情；历史报告的完整内容需手动打开 Excel 文件查看。

**参数:**
- `limit` (可选): 返回条数上限，默认 50

文件列表按修改时间降序排列（最新在前）。

**使用规则:**
- 只读操作
- 返回: `{files: [{name, size, modified_at}], directory}`
- `directory` 为报告文件所在目录的路径
- 此工具仅返回文件列表（文件名/大小/修改时间），不提供文件内容。LLM 无法直接读取历史 Excel 报告详情。查看最新报告摘要请使用 `get_quality_report`。

---

## ParaTranz（平台同步工具）

### 4. list_projects

**描述:**
列出 ParaTranz 平台上的翻译项目。

**参数:**
- `uid` (可选, str): 传 `"my"` 只看自己的项目（**默认行为**），传 `""` 查看全部项目。注意：省略 uid 参数时默认为 `"my"`，只返回自己的项目列表。需要查看全部项目时必须显式传 `uid=""`。

**使用规则:**
- 只读操作
- 返回项目列表: `{"projects": [{id, name, visibility}]}`
- 用 `get_project_info` 查看单个项目详情

---

### 5. get_project_info

**描述:**
获取单个 ParaTranz 项目的详细信息。

**参数:**
- `project_id` (可选): 项目 ID。不传则使用当前选中的项目，可通过`list_projects`查看

**使用规则:**
- 只读操作
- 返回 `{"id": 123, "name": "项目名", "visibility": "public", "member_count": 5}`
- 如需确认当前选中的项目，先用 `get_paratranz_project` 查询

**何时用此工具而非 `get_paratranz_project`:**
- 需要查看**特定项目**的详细信息（传 `project_id`）
- 需要获取 `member_count`（成员数）
- 当前未选中 ParaTranz 项目但想查看某个项目详情

**何时用 `get_paratranz_project`:**
- 仅想确认**当前选中**的是哪个项目（零参数，便捷）
- 无选中项目时不会报错（返回 null）

---

### 6. compare_with_remote

**描述:**
对比本地翻译与 ParaTranz 远程翻译的差异。`本地翻译`指向当前活跃翻译集合 (`ctx.collection`)。对比基于集合中的条目 key 进行匹配。显示前 20 条不同之处。只读。

**参数:**
- `project_id` (可选): 项目 ID。不传则使用当前选中的项目。通过 `get_paratranz_project` 确认当前操作的 PT 项目

**使用规则:**
- 只读操作
- 返回 `{"only_local": N, "only_remote": N, "different": N, "same": N, "details": [{key, status}, ...]}`，其中 `details` 最多 20 条
- `details` 中每条 `status` 可能值: `"only_local"`（仅本地存在）, `"different"`（本地与远程翻译文本不同）, `"same"`（一致，不在 details 中显示）。注意：`only_remote` 条目不在 details 中列出具体 key。
- 用于下载前预览变更

---

### 7. download_entries

**描述:**
从 ParaTranz 获取翻译条目数据。单阶段操作——下载完成后自动附加对比摘要到返回结果中。长运行，需用户确认。write 权限。

**参数:**
- `project_id` (可选): 项目 ID。不传则使用当前选中的项目。通过 `get_paratranz_project` 确认当前操作的 PT 项目

**使用规则:**
- 下载完成后自动生成对比摘要
- 下载的条目数据在返回结果的 `entries` 字段中，每条包含: `{key, original, translation, stage, context}`，需手动处理——不会自动修改当前集合中的条目
- 虽然标记为 write 权限（触发用户确认），但此操作不会自动修改本地集合中的条目。下载的条目数据在返回结果的 `entries` 字段中，需手动处理（如通过 `edit_translation` 逐条更新）。
- 需用户确认
- 返回 `{"downloaded_count": 150, "entries": [{"key": "...", "original": "...", "translation": "...", "stage": 1}, ...], "diff_summary": {"new_from_remote": 120, "updated": 30}}`。若未加载本地集合，`diff_summary` 为 null。

---

### 8. upload_entries

**描述:**
上传本地翻译条目到 ParaTranz。长运行，需用户确认。write 权限。

**参数:**
- `project_id` (可选): 项目 ID。不传则使用当前选中的项目。通过 `get_paratranz_project` 确认当前操作的 PT 项目
- `entry_ids` (可选): 要上传的条目 **key** 列表（来自 `get_visible_entries` 返回的 `key` 字段，不是 `id`）。不传则上传全部。key 构成规则: `{record_type}:{form_id}`。例如 `NPC_:00012345` 中，`NPC_` 为记录类型（对应 ESP/ESM 中的 record signature），`00012345` 为 FormID（十六进制，8位补齐）。
- `force_overwrite` (可选): 是否强制覆盖已存在的条目。默认 `false`

**使用规则:**
- 需用户确认
- 逐条上传，大批量（100+）可能触发限流
- 典型流程: `set_filters stages=[1]` → `get_visible_entries` → 确认条目 → `upload_entries entry_ids=["key1","key2"] force_overwrite=true`
- 返回 `{"uploaded": 80, "total": 82, "failed_items": [{"key": "NPC_:00012345", "error": "网络超时"}]}`

---

### 9. export_artifact

**描述:**
从 ParaTranz 触发翻译工件导出。artifact（工件）: ParaTranz 服务端导出的翻译工件包（通常为 .zip 压缩包），包含指定项目的全部翻译文件。导出在服务器端异步执行，完成后返回下载链接。

**参数:**
- `project_id` (可选): 项目 ID。不传则使用当前选中的项目。通过 `get_paratranz_project` 确认当前操作的 PT 项目

**副作用:**
- 触发 ParaTranz 服务器端导出任务

**使用规则:**
- 需用户确认（触发服务器端操作），write 权限
- 异步模型: 调用 `trigger_export` 触发导出 → 以 2 秒间隔轮询 `get_artifacts`（最长等待 30 秒）→ 成功时返回最新 artifact 数据，超时时返回 `{job, status: "pending"}`
- 返回（成功）: API 返回的 artifact 对象，具体字段取决于服务端响应
- 返回（超时）: `{"job": {...}, "status": "pending"}` — 导出仍在服务端处理中

---

### 10. get_upload_history

**描述:**
获取 ParaTranz 项目的上传历史记录。只读。

**参数:**
- `project_id` (可选): 项目 ID。不传则使用当前选中的项目。通过 `get_paratranz_project` 确认当前操作的 PT 项目
- `limit` (可选): 返回条数上限，默认 20

**使用规则:**
- 只读操作
- **何时调用**: 上传前检查上次同步时间（避免重复上传）；上传后验证是否同步成功；排查同步问题时查看失败记录。
- `status` 可能值: `"success"`（成功）, `"failed"`（失败）, `"processing"`（处理中）。示例仅展示了 success 情况。
- 返回 `{"history": [{"id": 1, "timestamp": "2026-05-21T10:00:00", "filename": "NPC_.json", "status": "success", "entries_count": 150}]}`

---

### 11. get_paratranz_project

**描述:**
获取当前选中的 ParaTranz 项目信息。只读。

**参数:** 无

**使用规则:**
- 只读，返回当前会话中选中的项目
- 返回 `{"id", "name", "visibility"}`，无选中项目时返回 `{"selected_project": null}`
- 与 `get_project_info` 的区别：此工具零参数、仅返回当前选中项目、无选中项目时不报错（返回 null）。需要查看特定项目详情或获取 member_count 时用 `get_project_info`。

---

### 12. switch_paratranz_project

**描述:**
切换到指定的 ParaTranz 项目。切换后，其他 PT 工具的 `project_id` 参数可省略（自动使用当前选中项目）。write 权限。

⚠ **前置条件**: ParaTranz API 需已配置（token 和 API URL）。通过 `get_app_state` 查看 `paratranz_configured` 字段确认。

**参数:**
- `project_id` (必填): 目标项目 ID（整数）。先调用 `list_projects` 获取项目列表，从中取目标项目的 `id` 字段

**使用规则:**
- 切换前必须先调用 `list_projects` 获取可选的项目 ID
- 切换后，其他 PT 工具的默认项目 ID 自动更新
- 切换项目不会丢失数据——本地翻译集合、筛选条件等保持不变。仅改变后续 PT 工具（upload/download/compare）的默认 `project_id`。
- 典型流程: `list_projects` → 从中选项目 `id` → `switch_paratranz_project project_id=123` → `compare_with_remote`
- 返回 `{"id", "name", "visibility"}`
