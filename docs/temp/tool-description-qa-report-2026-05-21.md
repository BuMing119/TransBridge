# 工具描述 QA 审查报告 — LLM 调用端视角

**日期**: 2026-05-21
**范围**: 5 个文件，共 42 个工具（所有工具在同一运行时内可用，文件仅为组织方式）
**审查方法**: 逐工具对照源码验证，从 LLM 调用端视角评估业务逻辑清晰度、上帝视角问题、调用规则完整性

---

## 一、总体结论

42 个工具描述中，**0 个 OK（无问题）**，**36 个 Minor**，**3 个 Major**，**1 个 Blocker**。

核心发现：**文档整体可调用，但存在系统性的"上帝视角"问题**——大量工具假设 LLM 知晓 UI 内部状态（当前集合、当前筛选、已解析文件等），而未在工具描述中指明如何获取这些状态。

---

## 二、跨工具共性问题（按影响面排序）

### P0 — 状态不可见（上帝视角）（影响 ~30 个工具）

LLM 调用端需要知晓以下关键状态，但引用这些状态的工具未说明如何获取：

| 被引用的状态 | 引用此状态的工具数 | 查询工具 | 问题 |
|-------------|-------------------|---------|------|
| 当前活跃集合 (active collection) | 15+ | `get_app_state` / `list_collections` 存在 | 引用此状态的工具（特别是 batch3 全部 parser 工具）未提及这些查询工具 |
| 当前筛选条件 (current filters) | 8+ | `get_current_filters` 存在 | `set_filters`、`get_visible_entries`、`manage_entry_labels(batch_assign)` 均未提及此查询工具 |
| 当前翻译作用域 (translation scope) | 5+ | `get_scope_preview` 存在（仅返回计数） | `start_translation`、`run_postprocess` 未提及；`get_scope_preview` 不返回具体条目列表 |
| 已解析文件路径 (parsed file paths) | 6 | `get_app_state` 返回 `esp_file`/`eet_file`/`xt_file`（查询工具存在但文档未引用） | `write_back` 的 target 推断依赖此状态，但工具描述中完全未提及可用 `get_app_state` 查询 |
| 当前选中条目 (selected entries) | 2 | **无查询工具** | `select_entries` 返回仅含 count，选中集合是纯"只写"状态——LLM 可以写入但永远无法读取 |
| ParaTranz 选中项目 | 9 | `get_paratranz_project` 存在 | `get_project_info`、`compare_with_remote`、`download_entries`、`upload_entries`、`export_artifact`、`get_upload_history`、`switch_paratranz_project` 均未提及此查询工具 |

**建议**: 在每个引用上述状态的工具描述中，明确指出通过哪个工具可以查询当前状态。对于完全无查询工具的状态（选中条目），要么补充查询工具（如 `get_selected_entries`），要么在工具描述中明确告知 LLM "你只能从之前的工具调用返回值中追踪此状态"。

### P1 — 领域术语无定义（影响 ~25 个工具）

以下核心术语在首次出现时缺乏定义：

| 术语 | 问题 |
|------|------|
| 翻译集合槽位 (slot) | Parser 工具共用前导中有行为描述（create_slot vs append），但"槽位"本身从未被概念定义——它是什么、生命周期如何 |
| 激活 (activate) | 从未明确定义 = "使该集合成为后续操作的默认目标" |
| stage (翻译阶段) | 枚举值有列出，但各值的语义和工作流顺序（0→1→2→3→5 的流转路径）从未说明 |
| variant | 出现在 `get_app_state`/`get_current_project` 中，无任何定义，无有效值列表 |
| profile (LLM配置) | `get_translation_config` 有示例，但从未说明 profile 包含哪些设置项 |
| 作用域 (scope) | `set_scope` 定义了参数，但 scope 与 filters 的区别仅在对比中暗示，无正面定义。scope 多维度之间的组合逻辑（跨维度 AND，维度内 OR）完全未文档化 |
| collection (翻译集合) | `list_collections` 返回了字段但从未定义集合是什么、它与源文件的关系、如何创建和销毁 |
| workspace | `list_local_projects` 使用了"本地工作空间"但未定义其边界或磁盘映射 |

**建议**: 在首次出现处加一句话定义，或建立术语表供所有工具引用。

### P2 — 缺少"何时不应调用"护栏（影响 ~35 个工具）

几乎所有工具都有"何时用"或典型流程，但几乎没有工具列出反模式：
- 当前置条件不满足时调用会怎样？
- 重复调用有何影响（幂等性）？
- 与其他工具功能重叠时如何选择？

### P3 — 错误返回格式未文档化（影响 ~30 个工具）

大多数工具仅文档化了成功返回格式。例如 `edit_translation` 在源码中有两条错误路径（条目未找到、stage 值非法），但文档均未提及。LLM 不知道失败时返回什么形状，无法编写错误处理逻辑。

### P4 — 工具重叠无区分说明（影响 ~5 对工具）

以下工具对功能重叠但缺乏选择指南：

| 工具 A | 工具 B | 重叠点 |
|--------|--------|--------|
| `get_project_info` | `get_paratranz_project` | 都调用同一 API 返回项目信息。区别仅在于 `get_project_info` 可选 project_id + 多返回 member_count，`get_paratranz_project` 零参数且不报错（返回 null）。语义差异不足以为 LLM 提供清晰选择依据 |
| `get_app_state` | `get_current_filters` + `get_current_project` | 前者包含后两者的核心信息（project、variant、collection、filters），但三者的使用场景从未区分 |
| `start_translation mode=polish` | `start_polish` | 功能重叠（都启动润色），但 start_polish 提供 intensity 和 scope 参数而 mode=polish 不提供。选择规则不清晰 |

---

## 三、严重级别分布

### Blocker (1 个)

| # | 工具 | 文件 | 核心问题 |
|---|------|------|---------|
| 1 | `export_artifact` | batch4 | **工具代码已损坏**：调用 `client.export_artifact(pid)` 但 `ParatranzProjectAPI` 上不存在该方法，运行必报 `AttributeError`。正确的导出轮询逻辑在 `ArtifactWorkflow` 中但未被本工具使用。此外：文档中"artifact"概念完全未定义（LLM 无法向用户解释导出的是什么）、异步模型未说明（标记为 long_running 但立即返回不轮询）、返回格式标注为"取决于 API 版本"不可靠 |

### Major (3 个)

| # | 工具 | 文件 | 核心问题 |
|---|------|------|---------|
| 1 | `select_entries` | batch1 | 无法读取已选条目列表（仅返回 `{selected_count}`），无可用的查询工具。选中集合对 LLM 而言是纯"只写"状态——LLM 可以写入 ID 但永远无法获知当前选中了哪些条目。与直接传 `entry_ids` 给后续工具相比，此工具价值不明确，LLM 会倾向于跳过它 |
| 2 | `import_strings` | batch3 | **路径规则矛盾**: 共享规则和所有其他 parser 工具均写明"路径遍历（`../` 或绝对路径）会被拒绝"，但 `import_strings` 单独声称"支持相对路径和绝对路径"。源码 `_validate_path` 对绝对路径统一拒绝——文档不仅内部矛盾，还与代码行为不一致；.strings 文件格式缺少结构定义（仅注明 UTF-8/UTF-16 编码） |
| 3 | `get_project_info` | batch4 | 与 `get_paratranz_project` 功能高度重叠（调用同一 API、核心返回字段相同），虽有细微差异（可选 project_id vs 零参数、返回 member_count vs 不返回、无上下文时报错 vs 返回 null），但语义差异不足以让 LLM 在两者间做出明确选择 |

### Minor (36 个)

其余 36 个工具均为 Minor。共性问题已在第二节覆盖，逐工具详情见第四节。

---

## 四、按文件的工具评级汇总

### batch1 — Editor 工具 (7 个)

| 工具 | 级别 | 一句话 |
|------|------|--------|
| `set_filters` | Minor | 多次强调"未传参数保持当前值"，但未告知可用 `get_current_filters` 查询当前筛选值 |
| `get_visible_entries` | Minor | "单独查询完整文本"指向不存在的工具（当前工具集中无 `get_entry_detail` 类工具）；截断至 200 字符是硬编码的，LLM 实际无法获取完整文本 |
| `select_entries` | **Major** | 无法读取已选状态（无查询工具）；与直接传 entry_ids 相比价值不明确 |
| `edit_translation` | Minor | stage 值 4/6/7/8 无效的原因未说明（此说明在 `set_stage` 中有但本工具缺失）；两条错误返回路径（条目未找到、stage 非法）完全未文档化 |
| `set_stage` | Minor | stage 工作流语义缺失（0→1→2→3→5 的流转路径），不过 stage 值的枚举和含义已列出；select_entries 在典型流程中作为建议出现而非强制依赖，这一点是清晰的 |
| `manage_entry_labels` | Minor | `batch_assign` 用户取消确认对话框后的行为未定义；`label_id` 仅在 create 时返回但后续所有操作均使用 `name` 而非 `label_id`，其用途未说明 |
| `list_labels` | Minor | `id` 与 `name` 的区别在返回格式中已体现，但作为标识符的选用规则（何时用 id 何时用 name）未说明 |

### batch2 — Translator 工具 (9 个)

| 工具 | 级别 | 一句话 |
|------|------|--------|
| `start_translation` | Minor | "mixed"模式有列出但完全未定义（源码中 mixed 无任何分支逻辑，与 translate 行为相同）；未说明已有翻译任务运行中时再次调用的行为 |
| `start_polish` | Minor | `scope="passed"` 的 stage 范围文档写 1/3/5 但源码实际包含 4/6（过滤条件不一致）；缺少 API key 已配置的前置条件声明（`start_translation` 有但本工具缺失） |
| `stop_task` | Minor | 工具名 `stop_task` 未能反映其同时支持 pause/resume；"活跃任务"定义模糊——实际包含 running 和 paused 两种状态，但文档混用"活跃"和"运行中"两个词 |
| `get_task_status` | Minor | metadata 字段结构因任务类型而异（translation 有 mode，polish 有 intensity+scope），但文档未展开 schema；progress 字段内部结构（current/total/message）未说明，单位也未标注 |
| `set_term_config` | Minor | "dynamic"来源有列出但功能未定义（源码中从 term_priority 列表中排除 dynamic 不做校验，行为语义不明） |
| `get_translation_config` | Minor | 返回的 `post_process_stages` 字段文档 key 名与实际源码不一致（文档写 `consistency` 源码为 `consistency_check`，文档写 `format` 源码为 `format_validation`）；`game_profile` 无有效值列表或发现途径 |
| `set_translation_config` | Minor | `model` 参数有效值无法发现（不像 `profile` 那样有 `available_profiles` 列表可查）；`target_lang` 示例值为 "chinese" 但参数描述为"语言代码"，格式不一致 |
| `set_scope` | Minor | 多维度间 AND 组合（跨维度取交集）、维度内 OR 组合（同维度内任一匹配）的逻辑未文档化；`action=include` vs `action=only` 的区别未解释 |
| `get_scope_preview` | Minor | 返回的 `scope` 字段结构可参照 `set_scope` 的参数文档推断，但未内联定义；默认 scope 为"全部未翻译条目 (stage=0)"这一信息在 `start_translation` 中有但本工具未提 |

### batch3 — Writer + Parser 工具 (7 个)

| 工具 | 级别 | 一句话 |
|------|------|--------|
| `write_back` | Minor | 已解析文件路径可通过 `get_app_state` 查询（返回 esp_file/eet_file/xt_file），但文档完全未提及此查询途径；target 自动推断规则已明确列出优先级，LLM 可据此执行 |
| `parse_esp` | Minor | "槽位"概念无定义（仅描述了 create_slot vs append 两种行为，未解释槽位本身）；未提及可用 `list_collections` 或 `get_app_state` 查询当前活跃集合 |
| `parse_eet` | Minor | EET vs XT 的格式区分规则（根元素判断法）仅在 parse_eet 中有详细说明，parse_xt 仅简单交叉引用"见 parse_eet" |
| `parse_xt` | Minor | "xTranslator"是什么（上古卷轴翻译工具）未解释；slot 概念同 parse_esp |
| `parse_sst` | Minor | SSU8/SSU9 格式区别和缩写含义未解释；解析后数据"仅用于查看"但具体能做什么（能编辑吗？能导出其他格式吗？）未说明 |
| `import_json` | Minor | "标准格式"和"DSD 格式"无 schema 定义，无示例 JSON 结构，LLM 无法判断自己的 JSON 是否合规 |
| `import_strings` | **Major** | 路径规则矛盾（见第三节）；.strings 文件格式无结构定义（仅注明编码），LLM 无法判断文件是否合法 |

### batch4 — Proofreader + ParaTranz 工具 (12 个)

| 工具 | 级别 | 一句话 |
|------|------|--------|
| `run_postprocess` | Minor | 六个阶段有简短标签（如"术语一致性检查"）但缺具体做什么的单句解释；scope 来源说"从当前翻译作用域解析"但未解释作用域是什么 |
| `get_quality_report` | Minor | reports 数组是聚合后的一条（覆盖全部已运行阶段），但示例中的 `"phase": "polish"` 容易误导为每阶段一条 |
| `list_quality_reports` | Minor | 无排序说明（源码按修改时间降序）；LLM 无法读取 Excel 内容查看历史报告详情，工具本身价值有限（文档已坦承此点） |
| `list_projects` | Minor | uid 参数描述自相矛盾：先说"传 `my` 只看自己的项目（默认行为）"，又说"传 `""` 或省略则查看全部项目"。源码默认值为 `"my"`，后者描述错误 |
| `get_project_info` | **Major** | 与 get_paratranz_project 重叠无区分（见第三节） |
| `compare_with_remote` | Minor | "本地翻译"指向当前 ctx.collection 但文档中仅说"本地翻译"未具体说明来源；details 条目的 status 枚举值（only_local/different）未列出 |
| `download_entries` | Minor | 返回 schema 直接引用内部 Python 类名 "TranslationEntry" 而非内联定义字段；标记 write 权限但与实际行为（纯下载读取、不修改本地集合）语义不符 |
| `upload_entries` | Minor | key 格式示例 `NPC_:00012345` 已给出但构成规则（record_type:form_id）从未正式定义；缺少典型调用流程 |
| `export_artifact` | **Blocker** | 见第三节 Blocker 详情 |
| `get_upload_history` | Minor | 无"何时调用"的场景指引（上传前检查上次同步时间？上传后验证？）；返回的 status 枚举值缺失（仅示例中展示了 success） |
| `get_paratranz_project` | Minor | 与 get_project_info 重叠（见 P4）；null 返回场景已在文档中说明（"无选中项目时返回 `{"selected_project": null}`"），这一点是清晰的 |
| `switch_paratranz_project` | Minor | 无鉴权前置条件（需 ParaTranz API 已配置）；切换过程中未保存数据是否丢失未说明 |

### batch5 — Default 工具 (7 个)

| 工具 | 级别 | 一句话 |
|------|------|--------|
| `get_app_state` | Minor | variant 字段无任何定义或有效值列表；"判断现在处于什么阶段"中的"阶段"（项目阶段）与翻译 stage 字段（翻译阶段）存在轻微语义过载 |
| `list_collections` | Minor | collection 概念无定义（仅列出返回字段但未解释集合是什么）；`esp_name` 对非 ESP 来源（JSON/Strings 导入）为 null 未说明 |
| `switch_collection` | Minor | `slot_index` 参数文档要求"先调用 `list_collections` 查看各集合对应的序号"，但 `list_collections` 返回中无 index/slot_index 字段，只能靠数组位置推算 |
| `get_current_filters` | Minor | stage 值只列出数字（0/1/2/3/5/9/-1）无语义映射，而源码 `_STAGE_LABELS` 中有完整映射；category/label 有效值的发现途径未说明（不像 `list_labels` 那样可查询） |
| `get_statistics` | Minor | `stage_distribution` 和 `category_distribution` 内部结构（key-value 映射）未定义；未说明统计是否受当前筛选条件影响 |
| `list_local_projects` | Minor | workspace 概念无定义（边界是什么、磁盘映射到哪、项目如何进入）；项目生命周期（创建/打开/关闭/删除）未说明 |
| `get_current_project` | Minor | variant 无定义；与 `get_app_state` 返回的项目信息高度重叠（两者均返回 name/variant/collection），重叠未区分，LLM 不知道选哪个 |

---

## 五、修复优先级建议

### 立即修复 (Blocker 1 项)

1. **`export_artifact`** — 修复代码：将 `client.export_artifact(pid)` 替换为正确的 `ArtifactWorkflow` 调用链（`trigger_export` → 轮询 `get_artifacts` → `download_artifacts`），或直接移除该工具。同时：定义 artifact 概念为"ParaTranz 服务端导出的翻译工件包（如 .zip 翻译包）"，明确异步模型（阻塞等待 vs 返回 task_id 供轮询），锁定返回 schema

### 短期修复 (Major 3 项)

1. **`select_entries`** — 补充选中状态查询能力：在返回数据中增加 `selected_ids` 字段，或新增只读的 `get_selected_entries` 工具。若选择不做，则需在文档中明确告知 LLM 此工具为可选中间步骤且选中状态不可查询
2. **`import_strings` 路径规则** — 统一为与其他 parser 工具一致：绝对路径被拒绝。删除文档中"支持绝对路径"的错误表述
3. **`get_project_info` vs `get_paratranz_project`** — 合并或明确区分：建议保留 `get_paratranz_project` 作为零参数的"当前选中项目"查询工具，将 `get_project_info` 重新定位为需要 project_id 的"特定项目详情"查询工具，并移除其回退到当前项目的逻辑

### 短期修复 (P0-P1 共性问题)

1. 为每个"不可见状态"在引用它的工具描述中添加查询指引（如"调用 `get_current_filters` 查看当前筛选"、"调用 `get_app_state` 查看已解析文件路径"）
2. 为完全无查询工具的状态（选中条目）补充查询工具或明确告知 LLM 只能从返回值追踪
3. 建立术语表（collection, slot, activate, stage, variant, profile, scope, workspace），在文档头部统一引用

### 中期改进 (P2-P4 共性问题)

1. 每个工具补充"何时不应调用"反模式（前置条件不满足时的行为、重复调用幂等性、与重叠工具的对比）
2. 每个工具补充错误返回格式（至少给出失败时的字段形状）
3. 为重叠工具添加"选择指南"（何时用 A 不用 B，各自的适用场景）

---

## 六、签名

QA 审查完成。42/42 工具已审查，1 Blocker + 3 Major + 36 Minor。`export_artifact` 因额外发现代码级缺陷（方法不存在，运行时必崩溃）从 Major 升级为 Blocker。`list_labels` 和 `set_term_config` 中各有一项子主张经源码核实不成立，已从清单中移除。

审查方法：逐工具对照源码 `_PARAM_SCHEMAS`、函数实现及 `ToolResult.ok()` 返回值进行验证。所有工具在同一运行时内可用，跨文件引用不构成问题。
