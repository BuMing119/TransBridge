# Story 22: 工具描述全面重写（Claude Code 参考格式）

**Epic**: agent-tool-expansion
**优先级**: P1
**风险**: 低（仅改 description 字符串）
**依赖**: S16-S21（所有合并完成后）
**状态**: 已方案

## 范围

参照 `docs/temp/claude-code-tools-reference.md` 格式，重写全部 45 个非废弃工具的描述。每个工具描述遵循三段结构：

### 描述格式

```
① 功能描述: 一句话定位 + 何时用我 + 做什么 + 与相似工具的区分
② 参数说明: 每个参数 type/required/enum值/默认值/使用说明
③ 使用规则: 注意事项、前置条件、典型组合场景、易混淆工具区分
```

### 涉及文件

- `tools/tool_editor.py` — 7 工具
- `tools/tool_translator.py` — 8 工具
- `tools/tool_writer.py` — 1 工具
- `tools/tool_parser.py` — 6 工具
- `tools/tool_proofreader.py` — 6 工具
- `tools/tool_paratranz.py` — 10 工具
- `tools/tool_default.py` — 7 工具
- `tests/smart_assistant/test_tool_consolidation.py` — description 断言适配

## 分批计划

按 namespace 分 5 批次，每批次：列出描述方案 → 用户确认 → 编码落地。

---

### 批次 1: editor namespace (7 tools)

**文件**: `tools/tool_editor.py`

#### 1. set_filters
**① 功能描述**: 设置条目表格的筛选条件（合并了已废弃的 filter_by_stage/category/label + search_entries + clear_all_filters）。当你需要按翻译阶段、分类、标签筛选条目，或按关键词搜索条目时，使用此工具。所有维度均为可选——只传需要修改的维度即可。
**② 参数**:
- `stages: list[int] | None` — 翻译阶段列表。0=未翻译 1=已翻译 2=有疑问 3=已检查 5=已审核 9=已锁定 -1=已隐藏。None=保持当前筛选，[]=清除阶段筛选
- `categories: list[str] | None` — 分类名列表（如 NPC_、INFO、BOOK）。None=保持，[]=清除
- `labels: list[str] | None` — 标签名列表。None=保持，[]=清除
- `search_query: str | None` — 搜索关键词。None=保持，""=清除搜索
- `search_field: str | None` — 搜索字段。可选: "id"|"key"|"original"|"translation"|"context"|"all"，默认 "original"。None=保持当前字段
- `clear: bool` — 是否先清除所有筛选再应用新值，默认 false
**③ 使用规则**:
- 仅需修改某维度时只传该维度，其他自动保持
- `clear=True` + 新参数 = 全新筛选；`clear=True` 单独传 = 清除所有
- 无效 stage 值或 search_field 会被拒绝
- 典型组合: `set_filters stages=[0,1]` 只看未翻译→ `get_visible_entries` 获取列表→ `edit_translation` 逐条修改

#### 2. manage_entry_labels
**① 功能描述**: 管理条目标签（合并了已废弃的 create_label/assign_label/remove_label/batch_assign_label）。通过 `action` 参数选择操作类型——创建新标签、为条目分配/移除标签、或批量分配。与 `set_filters` 的区别：set_filters 控制"显示哪些条目"，manage_entry_labels 控制"条目有什么标签"。
**② 参数**:
- `action: str` (必填) — 操作类型。可选: "create"|"assign"|"unassign"|"batch_assign"
- `name: str` — 标签名。create/assign/unassign 必填
- `color: str` — 颜色 hex（如 "#409EFF"）。仅 create 使用，默认 "#409EFF"
- `entry_ids: list[str]` — 条目 ID 列表。assign/unassign 必填。batch_assign 不需要（自动使用当前筛选范围内所有条目）
**③ 使用规则**:
- create 不需要 collection，assign/unassign/batch_assign 需要活跃集合
- batch_assign 操作当前筛选范围内所有条目，需用户确认
- 标签不存在时 assign/unassign/batch_assign 返回错误——需先 create
- 典型流程: `manage_entry_labels action=create name=重要` → `manage_entry_labels action=assign name=重要 entry_ids=[...]`

#### 3. get_visible_entries
**① 功能描述**: 获取当前筛选条件下可见的条目列表（分页）。仅返回摘要信息（id/key/original/stage），不返回完整翻译数据。
**② 参数**:
- `limit: int` — 返回条数上限，默认 50，最大 200
- `offset: int` — 偏移量，默认 0
**③ 使用规则**:
- 依赖 `set_filters` 先设置筛选条件
- 上限 200 条，超出时 `truncated=True` + `total_count` 提示
- 如需统计数据而非具体条目，用 `get_statistics`

#### 4. select_entries
**① 功能描述**: 选择/取消选择条目（使用独立选择集合，不影响标签系统）。选择后可用于批量操作。
**② 参数**:
- `entry_ids: list[str]` (必填) — 条目 ID 列表
- `action: str` — 操作: "select"|"deselect"|"clear"，默认 "select"
**③ 使用规则**:
- 选择集合独立于标签系统（存储在 `_selected_ids` 中）
- action="clear" 时无需 entry_ids
- 典型组合: `get_visible_entries` → `select_entries action=select` → `set_stage`

#### 5. edit_translation
**① 功能描述**: 编辑单条条目的翻译文本。可同时设置翻译阶段。与 `set_stage` 的区别：edit_translation 改文本（可附带改 stage），set_stage 只改 stage（支持批量）。
**② 参数**:
- `entry_id: str` (必填) — 条目 ID
- `new_translation: str` (必填) — 新译文
- `new_stage: int | None` — 新翻译阶段（可选）。不传则保持原 stage
**③ 使用规则**:
- 单条目操作，批量修改 stage 用 `set_stage`
- new_stage 合法值: 0=未翻译 1=已翻译 2=有疑问 3=已检查 5=已审核 9=已锁定 -1=已隐藏

#### 6. set_stage
**① 功能描述**: 批量设置多条条目的翻译阶段。与 `edit_translation` 的区别：set_stage 只改 stage 不改文本，支持批量操作。
**② 参数**:
- `entry_ids: list[str]` (必填) — 条目 ID 列表
- `stage: int` (必填) — 目标 stage。0=未翻译 1=已翻译 2=有疑问 3=已检查 5=已审核 9=已锁定 -1=已隐藏
**③ 使用规则**:
- 支持批量操作，一次可修改多条条目
- 典型组合: `get_visible_entries` → 筛选 → `select_entries` → `set_stage stage=3`

#### 7. list_labels
**① 功能描述**: 列出所有已定义的标签及其使用次数。只读操作。需要管理标签内容时用 `manage_entry_labels`。
**② 参数**: 无
**③ 使用规则**:
- 标签库未初始化时返回空列表（非错误）
- 返回: `[{id, name, color, count}]`

---

### 批次 2: translator namespace (8 tools)

**文件**: `tools/tool_translator.py`

#### 8. start_translation
**① 功能描述**: 启动 AI 翻译任务（后台运行，返回 task_id 用于查询进度和停止）。支持三种模式：translate（翻译）、polish（润色）、mixed（混合）。与 `start_polish` 的区别：start_translation 默认模式为 translate，start_polish 默认模式为 polish 且需要 entry_ids。
**② 参数**:
- `mode: str` — 翻译模式: "translate"|"polish"|"mixed"，默认 "translate"
- `entry_ids: list[str] | None` — 指定要翻译的条目 ID 列表。不传则使用当前 translation_scope 作用域（默认=全部未翻译条目）
**③ 使用规则**:
- 需要 API Key 已配置，术语数据库建议提前设置
- 返回 `task_id`，用 `get_task_status` 查询进度，用 `stop_task` 停止
- 前置: `set_scope` 设置翻译范围 → `start_translation` 执行
- 典型流程: `set_scope stages=[0]` → `start_translation mode=translate` → `get_task_status`

#### 9. start_polish
**① 功能描述**: 启动 AI 润色任务（后台运行）。与 `start_translation mode=polish` 的区别：start_polish 专为润色设计，支持 intensity 参数，要求明确指定 entry_ids。
**② 参数**:
- `entry_ids: list[str]` (必填) — 要润色的条目 ID 列表
- `intensity: str` — 润色强度: "light"|"medium"|"heavy"，默认 "medium"
**③ 使用规则**:
- 必须明确指定要润色的条目（不支持作用域自动选择）
- 返回 `task_id`，通过 TaskManager 管理生命周期

#### 10. stop_task
**① 功能描述**: 停止翻译/润色任务（合并了已废弃的 stop_all_tasks）。传 task_id 停止指定任务，不传或传 "" 则停止所有活跃任务。
**② 参数**:
- `task_id: str | None` — 要停止的任务 ID。不传、传 None 或传 "" 均表示停止全部活跃任务
**③ 使用规则**:
- 停止全部时返回 `stopped_task_ids` 和可选的 `failed_task_ids`
- 无活跃任务时返回 "当前无运行中的任务"（非错误）
- 需用户确认（操作不可逆）
- 典型用法: `stop_task`（停止全部）/ `stop_task task_id=xxx`（停止指定）

#### 11. get_task_status
**① 功能描述**: 查询翻译/润色任务的进度状态。不传 task_id 时返回所有活跃任务摘要。
**② 参数**:
- `task_id: str | None` — 任务 ID。不传则返回所有任务摘要
**③ 使用规则**:
- 单个任务返回: `{status, progress, message}`
- 所有任务返回: `[{task_id, status, progress}]`

#### 12. get_translation_config
**① 功能描述**: 返回当前 LLM 翻译配置（provider/model/profile/术语库/后处理阶段）。只读操作。
**② 参数**: 无
**③ 使用规则**:
- 返回当前 INI 配置 + 运行时状态
- 修改配置用 `set_translation_config`

#### 13. set_translation_config
**① 功能描述**: 更新 LLM 翻译参数。profile 参数切换到 INI 中 `[llm_profiles]` 预设的 API 端点方案（H7: 不能自由输入 URL，只能选预设方案）。
**② 参数**:
- `profile: str | None` — INI `[llm_profiles]` 中预设的端点方案名（如 "openai"/"anthropic"/"local_proxy"）
- `model: str | None` — 模型名
- `temperature: float | None` — 生成温度
- `max_tokens: int | None` — 最大输出 token 数
- `target_lang: str | None` — 目标语言代码
- `game_profile: str | None` — 游戏 profile
**③ 使用规则**:
- profile 值必须是 INI 中预设的方案名，不在列表中的值会被拒绝
- 仅传需要修改的参数，未传参数保持原值
- 修改后立即持久化到 INI

#### 14. set_scope
**① 功能描述**: 设置翻译作用域——定义 `start_translation` 的默认翻译范围。与 `set_filters` 的区别：set_scope 定义"翻译哪些条目"，set_filters 定义"显示哪些条目"。
**② 参数**:
- `stages: list[int] | None` — 目标 stage 列表
- `labels: list[str] | None` — 目标标签列表
- `categories: list[str] | None` — 目标分类列表
- `action: str | None` — 作用域动作: "include"|"exclude"|"only"，默认 "include"
**③ 使用规则**:
- 典型流程: `set_scope stages=[0] action=include` → `get_scope_preview` 确认范围 → `start_translation`

#### 15. get_scope_preview
**① 功能描述**: 预览当前作用域下匹配的条目统计（数量、分布），用于确认翻译范围。在 `start_translation` 前调用。
**② 参数**: 无
**③ 使用规则**:
- 依赖 `set_scope` 先设置作用域
- 返回统计信息而非具体条目列表

---

### 批次 3: writer + parser namespace (7 tools)

**文件**: `tools/tool_writer.py` (1) + `tools/tool_parser.py` (6)

#### 16. write_back
**① 功能描述**: 将译文写回源文件（合并了已废弃的 write_to_esp/eet/xt/strings）。通过 `target` 参数选择目标格式，dispatch 表自动路由到对应实现。需用户确认（admin 权限）。
**② 参数**:
- `target: str` (必填) — 写回目标: "esp"|"eet"|"xt"|"strings"。根据已加载的文件类型选择——有 ESP 插件选 esp，有 EET 源选 eet，有 XT 源选 xt，仅需 strings 导出选 strings
- `path: str | None` — 输出路径。不传则使用当前已解析的源路径（esp/eet/xt）或需要手动指定（strings）
**③ 使用规则**:
- esp/strings 目标需要活跃槽位（已解析插件）
- eet/xt 目标需要已解析对应源文件（`ctx.eet_path`/`ctx.xt_path` 非空）
- admin 权限操作，需用户确认
- 典型用法: `write_back target=esp`（就地写回）/ `write_back target=strings path=./output`（导出 strings）

#### 17-22. parse_esp / parse_eet / parse_xt / parse_sst / import_json / import_strings
**① 功能描述**:
- `parse_esp`: 解析 ESP/ESM 插件文件，提取翻译条目到当前集合。支持 .esp/.esm/.esl 格式
- `parse_eet`: 解析 EET XML 翻译文件，加载翻译条目
- `parse_xt`: 解析 XT XML 翻译文件，加载翻译条目
- `parse_sst`: 解析 SST 二进制翻译文件（SSU8/SSU9 格式），加载翻译条目
- `import_json`: 从 JSON 文件导入翻译集合
- `import_strings`: 从 .strings 文件导入翻译
**② 参数**（6 工具共享）:
- `path: str | None` — 文件路径。不传则触发 HITL 文件选择对话框
**③ 使用规则**:
- 所有 parser 工具均为 `read` 权限（解析不产生持久化副作用）
- 文件扩展名白名单: .esp/.esm/.esl/.xml/.json/.strings
- path 含路径遍历（`../`、绝对路径）时拒绝
- 解析结果追加到当前集合，不覆盖已有条目

---

### 批次 4: proofreader + paratranz namespace (16 tools)

**文件**: `tools/tool_proofreader.py` (6) + `tools/tool_paratranz.py` (10)

#### 23. run_consistency_check
**① 功能描述**: 执行术语一致性检查——验证翻译中的术语是否与术语库一致。只读操作。与 `run_format_validation` 的区别：consistency_check 关注术语内容一致性，format_validation 关注格式/标记正确性。
**② 参数**: 无
**③ 使用规则**:
- 依赖已配置的术语数据库
- 返回不一致列表及建议修正

#### 24. run_format_validation
**① 功能描述**: 执行翻译格式校验——检查标记、占位符、特殊字符是否正确保留。只读操作。
**② 参数**: 无
**③ 使用规则**:
- 检查 SSE 特有的格式标记（如 `<alias=>`、`{{param}}` 等）
- 返回格式错误列表及位置

#### 25. run_llm_refinement
**① 功能描述**: 使用 LLM 修复翻译问题。长运行操作（后台执行），需用户确认（预估费用）。
**② 参数**: 无（使用后处理器预设配置）
**③ 使用规则**:
- 先运行 `run_consistency_check`/`run_format_validation` 发现问题再调用
- 确认提示显示预估条目数和费用
- 返回 `task_id` 用于进度查询

#### 26-28. run_llm_polish / run_llm_arbitration / get_quality_report
**① 功能描述**:
- `run_llm_polish`: LLM 润色翻译文本（后台，需确认费用）
- `run_llm_arbitration`: LLM 裁决多个候选翻译方案（后台，需确认费用）
- `get_quality_report`: 获取最近一次质量检查/修复/润色/裁决的报告摘要。只读
**② 参数**: 无（polish/arbitration）/ 无（report）
**③ 使用规则**:
- polish/arbitration 为长运行工具，需确认 + 返回 task_id
- report 返回最近一次操作的统计结果

#### 29-38. ParaTranz (10 tools)
分组说明：以下 10 个工具操作 ParaTranz 平台。

**① 功能描述**（按功能分组）:

**项目查询**:
- `list_projects`: 列出 ParaTranz 项目（可选过滤 all/mine）。只读
- `get_project_info`: 获取单个项目详细信息。只读
- `get_paratranz_project`: 获取当前选中的 ParaTranz 项目。只读
- `switch_paratranz_project`: 切换到指定的 ParaTranz 项目（输入 project_id）。write 权限

**数据同步**:
- `compare_with_remote`: 对比本地翻译与 ParaTranz 远程差异（前 20 条详情）。只读
- `download_entries`: 从 ParaTranz 下载翻译条目（单阶段，自动附加对比摘要）。长运行，需确认。write 权限
- `upload_entries`: 上传翻译条目到 ParaTranz。长运行，需确认。write 权限

**术语 + 工件**:
- `export_artifact`: 导出 ParaTranz 翻译工件。长运行。write 权限

**历史**:
- `get_upload_history`: 获取上传历史记录。只读

**② 参数**:
- `project_id: str | None` — 项目 ID（list_projects/get_project_info/compare/upload/download/export）
- `force_overwrite: bool` — 是否强制覆盖（upload_entries）
- `filter: str` — 过滤: "all"|"mine"（list_projects）

**③ 使用规则**:
- 所有 ParaTranz 工具需要有效的 API token
- download/upload/export 为长运行操作
- 典型流程: `list_projects` → `get_project_info` → `compare_with_remote` → `download_entries`

---

### 批次 5: default namespace (7 tools)

**文件**: `tools/tool_default.py`

#### 39-45. default tools

**① 功能描述**:

**状态查询**（只读）:
- `get_app_state`: 返回当前应用全局状态——当前 step（1/2/3）、活跃集合名、项目信息、筛选状态、API 连接状态。一站式概览
- `get_current_filters`: 返回当前筛选条件的完整快照（stages/categories/labels/search）。
- `get_statistics`: 返回当前集合的详细统计——条目总数、翻译率、stage 分布、分类分布、标签分布。替代已废弃的 get_collection_summary
- `get_current_project`: 获取当前活跃项目信息。只读

**集合操作**:
- `list_collections`: 列出所有已加载的翻译集合及基本信息（名称/条目数/来源）。只读
- `switch_collection`: 切换活跃翻译集合（按名称或 slot_index）。write 权限

**项目管理**:
- `list_local_projects`: 列出本地工作空间中的所有项目。只读

**② 参数**:
- `collection_name: str | None` — 集合名称（switch_collection）
- `slot_index: int | None` — 槽位索引（switch_collection）

**③ 使用规则**:
- 状态查询工具均为只读，可随时调用
- `get_app_state` 是所有状态查询的聚合入口
- `switch_collection` 是少数几个修改全局状态的操作之一

---

## 验收标准

- [ ] 45 个工具的描述全部按三段格式重写
- [ ] 每个 description 包含: ①功能定位 ②参数详情（类型/必填/enum/默认值）③使用规则
- [ ] 旧 S21 三原则描述被替换为 Claude Code 格式
- [ ] `build_tool_schema_for_prompt()` 输出包含新格式描述
- [ ] 现有测试全部通过（description 文本变更不破坏功能）
