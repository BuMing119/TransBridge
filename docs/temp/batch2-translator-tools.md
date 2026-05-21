# Translator 工具 — LLM 使用参考（Batch 2）

> 格式参照 `claude-code-tools-reference.md`，纯使用面。
> 条目标识符：所有 `entry_ids` 参数使用 `get_visible_entries` 返回的 **`key`** 字段值。

---

## 1. start_translation

**描述:**
启动 AI 翻译任务（后台运行）。返回 `task_id` 用于之后查询进度或停止任务。

支持三种模式：`translate`（翻译未翻译条目）、`polish`（润色已有译文）、`mixed`（混合）。注意: `mixed` 模式当前与 `translate` 行为相同（源码中无独立分支逻辑），作为预留模式存在。不传 `entry_ids` 时自动使用 `set_scope` 设置的范围（默认=全部未翻译条目）。

与 `start_polish` 的区别：start_translation 默认模式为 translate，可不传 entry_ids（使用作用域）；start_polish 默认模式为 polish，支持 `scope` 或 `entry_ids` 两种指定方式，并提供 `intensity` 参数控制润色强度。

若已有翻译任务运行中，再次调用会启动新的并行任务。两个任务独立运行，互不影响。

**参数:**
- `mode` (可选): 翻译模式。`"translate"` 翻译, `"polish"` 润色, `"mixed"` 混合。默认 `"translate"`
- `entry_ids` (可选): 要翻译的条目 `key` 列表。不传则使用 `set_scope` 设置的当前作用域

**副作用:**
- 启动后台翻译任务，任务 ID 可通过 `get_task_status` 查询进度、`stop_task` 停止

**使用规则:**
- ⚠ **重要前置**: 调用前通过 `get_translation_config` 确认配置，通过 `get_scope_preview` 确认范围
- 若需检查是否已有运行中的任务，使用 `get_task_status` 查询
- 需要 API Key 已配置
- 翻译质量依赖术语库匹配——需要调整术语配置时用 `set_term_config`
- 返回: `{task_id, mode}`，用 `get_task_status` 查询进度，用 `stop_task` 停止或暂停
- 典型流程: `get_translation_config` → `set_term_config term_sources=["paratranz","json"]` → `set_scope stages=[0] action=include` → `start_translation mode=translate` → `get_task_status`

---

## 2. start_polish

**描述:**
启动 AI 润色任务（后台运行）。通过 `scope` 参数自动筛选润色范围，也可通过 `entry_ids` 指定具体条目。`scope` 和 `entry_ids` 至少提供一个，同时提供时 `entry_ids` 优先。

与 `start_translation mode=polish` 的区别：start_polish 支持 `scope` 和 `intensity` 参数，可按条目状态自动筛选润色范围。

**参数:**
- `entry_ids` (可选): 要润色的条目 `key` 列表（来自 `get_visible_entries`）。与 `scope` 至少提供一个
- `scope` (可选): 润色范围。`"all"` 全部有译文(默认), `"passed"` 已通过检查(stage=1/3/4/5/6), `"has_issues"` 待审核(stage=2)
- `intensity` (可选): 润色强度。`"light"` 轻度, `"medium"` 中度, `"heavy"` 重度。默认 `"medium"`

**副作用:**
- 启动后台润色任务，任务 ID 可通过 `get_task_status` 查询进度、`stop_task` 停止

**使用规则:**
- ⚠ **前置条件**: 需要 API Key 已配置（与 `start_translation` 相同），调用前先通过 `get_translation_config` 确认 `api_key_configured` 为 true
- `scope` 和 `entry_ids` 至少提供一个
- 返回: `{task_id, intensity, scope, entry_count}`，用 `get_task_status` 查询进度，用 `stop_task` 停止
- 典型流程: `start_polish scope=passed intensity=light` → 润色所有已通过检查的条目

---

## 3. stop_task

**描述:**
控制后台翻译/润色任务——停止、暂停或恢复。注意: 工具名为 stop_task 但实际支持三种操作，通过 `action` 参数区分。传 `task_id` 操作指定任务，不传则操作所有活跃任务。

活跃任务包含 running 和 paused 两种状态的任务。completed/cancelled/failed 的任务不在此列。

**参数:**
- `task_id` (可选): 要操作的任务 ID。不传、传 `null` 或 `""` 均表示操作所有活跃任务
- `action` (可选): 操作类型。`"stop"` 停止(默认，不可恢复), `"pause"` 暂停(等待当前批次完成后挂起), `"resume"` 恢复(从暂停点继续)

**副作用:**
- stop: 向后台任务发送停止信号，任务无法恢复
- pause: 等待当前批次完成后挂起任务，可用 resume 恢复
- resume: 从暂停点继续执行

**使用规则:**
- 需用户确认。`stop` 不可逆（任务被终止）；`pause` 可逆（可通过 `resume` 恢复）；`resume` 恢复已暂停的任务
- pause 后请尽快 resume，避免任务长时间挂起。忘记恢复的任务可通过 `get_task_status` 检查（status=`paused`）
- 当前无运行中任务时返回 "当前无运行中的任务（running 或 paused 状态）"（非错误）
- 操作全部任务时返回: `{affected_task_ids, action}`，部分失败时额外含 `{failed_task_ids}`
- 操作指定任务时返回: `{task_id, action}`

---

## 4. get_task_status

**描述:**
查询翻译/润色任务的进度状态。不传 `task_id` 时返回所有活跃任务摘要。

**参数:**
- `task_id` (可选): 任务 ID。不传则返回所有活跃任务摘要列表

**使用规则:**
- 查询后台任务的当前进度
- 单个任务返回: `{task_id, status, progress, created_at, metadata}`。`progress` 包含 `{current, total, message}`，分别表示当前进度、总数、状态消息。`current` 和 `total` 为条目计数。
- `metadata` 字段结构：
  - 翻译任务: `{type: "translation", mode: "translate"|"polish"|"mixed"}`
  - 润色任务: `{type: "polish", intensity: "light"|"medium"|"heavy", scope: "all"|"passed"|"has_issues"}`
- 全任务摘要（不传 task_id）返回: `{active_count, total_count, tasks: [{task_id, status, metadata}]}`。注意：全任务摘要不含 `progress` 和 `created_at` 字段
- status 可能值: `"running"`, `"paused"`, `"completed"`, `"cancelled"`, `"failed"`

---

## 5. set_term_config

**描述:**
设置术语数据库配置——术语来源的优先级顺序和本地文件路径。修改后立即持久化。翻译质量依赖术语库匹配，建议在 `start_translation` 前确认配置。

⚠ **重要前置**: 先调用 `get_translation_config` 查看当前术语配置（`term_priority`、`local_json_path`、`local_excel_path`）。

**参数:**
- `term_sources` (可选): 术语来源优先级列表。可选值: `"dynamic"` 动态生成, `"paratranz"` ParaTranz 术语库, `"json"` 本地 JSON, `"excel"` 本地 Excel。按列表顺序优先级递减。例如 `["paratranz", "json"]` 表示优先查 ParaTranz 再查本地 JSON。`"dynamic"` 来源: AI翻译过程中从已翻译条目中自动提取的候选术语（动态生成，非持久化）。在 `term_sources` 列表中包含 `dynamic` 时，翻译时会自动补充动态提取的术语。注意：`dynamic` 在源码中从术语优先级校验列表排除（不参与有效性检查），其行为语义为"总是可用但不需配置"。
- `json_path` (可选): 本地 JSON 术语库文件路径
- `excel_path` (可选): 本地 Excel 术语库文件路径

**副作用:**
- 术语配置被保存，下次启动翻译时生效

**使用规则:**
- **先调 `get_translation_config` → 了解当前配置 → 再调本工具修改**
- `term_sources` 列表的顺序决定检索优先级（先列出的优先）。仅传需要修改的维度，未传参数保持原值
- `json_path` / `excel_path` 仅在对应的来源（`"json"` / `"excel"`）存在于 `term_sources` 中时生效，否则路径被静默忽略
- 传空列表 `[]` 表示禁用所有术语来源
- 无效来源名会被拒绝
- 返回: `{changed}`（修改的字段列表）

---

## 6. get_translation_config

**描述:**
返回当前 LLM 翻译配置快照。包括：当前 provider（如 openai/anthropic）、模型名、temperature、max_tokens、术语优先级列表（term_priority）、本地术语库路径、后处理阶段开关。还会列出所有可用的 profile 预设方案名（`available_profiles`）——这是调用 `set_translation_config` 前获取可选 profile 列表的唯一途径。

**参数:** 无

**使用规则:**
- 修改配置前必须先调用此工具——了解当前状态 + 获取可选 profile 名称列表
- `game_profile` 无预定义有效值列表，使用项目约定值（如 `"skyrim"`），具体取值需向用户确认
- 返回格式示例:
```json
{
  "provider": "openai",
  "model": "gpt-4o",
  "api_key_configured": true,
  "temperature": 0.7,
  "max_tokens": 4096,
  "target_lang": "chinese",
  "game_profile": "skyrim",
  "term_priority": ["paratranz", "json"],
  "local_json_path": "/path/to/terms.json",
  "local_excel_path": "",
  "post_process": {"enabled": true, "consistency_check": true, "format_validation": true, "quality_gate": true, "refinement": true, "polish": true, "arbitration": true},
  "available_profiles": ["openai", "anthropic", "local_proxy"]
}
```

---

## 7. set_translation_config

**描述:**
更新 LLM 翻译参数。只传需要修改的参数，未传参数保持原值。

⚠ **重要前置**: 先调用 `get_translation_config` 获取可用 profile 列表。`profile` 只能从返回的 `available_profiles` 中选择，不能自由输入 URL。

**参数:**
- `profile` (可选): 端点方案名。必须从 `get_translation_config` 返回的 `available_profiles` 中选择（如 `"openai"` / `"anthropic"` / `"local_proxy"`）。不在列表中的值会被拒绝
- `model` (可选): 模型名。注意: 有效值取决于当前 `profile` 对应的 API 提供商。可用模型名无法通过工具查询，需向用户确认或参考提供商文档。
- `temperature` (可选): 生成温度（建议范围 0.0-2.0，不强制校验）
- `max_tokens` (可选): 最大输出 token 数
- `target_lang` (可选): 目标语言（如 `"chinese"`、`"english"`），非标准语言代码格式
- `game_profile` (可选): 游戏 profile 名称

**副作用:**
- 配置被保存，下次启动翻译时生效

**使用规则:**
- **先调 `get_translation_config` → 从返回的 `available_profiles` 选 profile → 再调本工具**
- 返回: `{changed_fields, profile}`

---

## 8. set_scope

**描述:**
设置翻译作用域——定义 `start_translation` 不传 `entry_ids` 时的默认翻译范围。

与 `set_filters` 的区别：`set_scope` 定义"翻译哪些条目"（翻译引擎作用域），`set_filters` 定义"表格显示哪些条目"（视图筛选）。

**参数:**
- `stages` (可选): 目标 stage 列表。合法值: 0=未翻译, 1=已翻译, 2=有疑问, 3=已检查, 5=已审核, 9=已锁定, -1=已隐藏
- `labels` (可选): 目标标签名列表。先调用 `list_labels` 获知已定义的标签名
- `categories` (可选): 目标分类名列表。先调用 `get_statistics` 查看当前集合中实际存在的分类
- `action` (可选): 作用域动作。`"include"` 包含, `"exclude"` 排除, `"only"` 仅限。默认 `"include"`

**副作用:**
- 设置翻译作用域，后续 `start_translation`（不传 entry_ids）自动使用此范围

**使用规则:**
- labels/categories 的值必须真实存在——先用 `list_labels` / `get_statistics` 查
- 只传需要限制的维度，未传维度不限制
- 多维度组合规则：不同维度之间为 AND（交集），同一维度内多个值为 OR（并集）。例如 `stages=[0,1] categories=["NPC_"]` 表示：匹配 (stage=0 OR stage=1) AND (category=NPC_) 的条目。
- `action` 参数说明:
  - `include`: 包含匹配的条目（与其他维度取交集）
  - `exclude`: 排除匹配的条目
  - `only`: 仅限匹配的条目（严格模式，与 `include` 在当前实现中行为相同）
- 返回: `{stages, labels, categories, action}`（当前作用域快照）
- 典型流程: `list_labels` + `get_statistics` → `set_scope stages=[0] labels=["重要"] action=include` → `get_scope_preview` 确认 → `start_translation`

---

## 9. get_scope_preview

**描述:**
预览当前翻译作用域匹配的条目统计（数量、分布）。在 `start_translation` 前用于确认翻译范围。

**参数:** 无

**使用规则:**
- 依赖 `set_scope` 先设置作用域
- 读取当前翻译作用域并统计匹配条目数量
- 返回: `{matched, total, scope: {stages, labels, categories, action}}`（匹配条目数、集合总数、当前作用域快照），不返回具体条目列表
- 若从未调用 `set_scope`，默认作用域为全部未翻译条目 (stage=0)，此默认值在 `start_translation` 不存在 entry_ids 且无 scope 时自动设置
- 不返回具体条目列表，仅返回匹配计数。如需具体条目，使用 `get_visible_entries` 配合对应筛选条件
