# 工具描述文档 LLM 可理解性 QA 审查

**日期**: 2026-05-21
**审查范围**: `docs/temp/batch1-5*.md` — 42 个工具
**审查方法**: 42 Agent 并行，每个 Agent 从 LLM 调用者视角审查一个工具
**审查维度**: 业务逻辑清晰度 / 调用规则 / 参数准确性 / 返回格式 / 副作用描述

---

## 总体统计

| 评分 | 数量 | 占比 |
|------|------|------|
| ✅ 通过 | 15 | 35.7% |
| ⚠️ 需改进 | 26 | 61.9% |
| 🔴 有问题 | 1 | 2.4% |

---

## ✅ 通过（15 个工具）

| # | 工具 | 命名空间 | 关键亮点 |
|---|------|---------|---------|
| 1 | set_filters | editor | 与 manage_entry_labels 区分清晰，常用组合示例完整 |
| 2 | edit_translation | editor | 与 set_stage 职责边界明确，new_stage 默认行为说明到位 |
| 3 | list_labels | editor | 只读，无参，返回格式清晰，空库不报错 |
| 4 | get_task_status | translator | 单/全任务返回格式区分明确，paused 状态已包含 |
| 5 | set_translation_config | translator | profile 约束（必须来自 available_profiles）双重强调 |
| 6 | set_scope | translator | 与 set_filters 区分清晰（引擎作用域 vs 视图筛选） |
| 7 | get_scope_preview | translator | 前置条件明确，返回格式简洁，无歧义 |
| 8 | parse_esp | parser | 共享规则表 + 专用描述设计优秀，参数清晰 |
| 9 | parse_eet | parser | EET vs XT 消歧策略（根元素检查/问用户）设计最佳 |
| 10 | parse_xt | parser | 与 parse_eet 对称，扩展名要求清晰 |
| 11 | import_json | parser | 定位明确，不记录文件路径的例外已标注 |
| 12 | list_collections | default | 返回字段表清晰，key/label 均可用于 switch_collection |
| 13 | compare_with_remote | paratranz | 下载前预览定位准确，details 最多 20 条明确 |
| 14 | switch_paratranz_project | paratranz | 典型流程完整（list→switch→其他工具） |
| 15 | list_labels | editor | （同上 #3） |

---

## ⚠️ 需改进（26 个工具）

### 高优先级（可能导致 LLM 调用失败或行为错误）

| # | 工具 | 问题 | 建议修复 |
|---|------|------|---------|
| 1 | **start_polish** | §2 说 entry_ids 可选 + scope 可替代，但 §1(start_translation) line 15 说 start_polish "必须传 entry_ids"——**跨章节矛盾** | 统一 §1 的表述为 "支持 scope 或 entry_ids" |
| 2 | **stop_task** | pause 被标注为"操作不可逆"，但 pause 实际可恢复（resume）——**行为描述错误** | 将"不可逆"改为仅对 stop 生效；分类标注 pause/resume 可逆 |
| 3 | **list_projects** | 参数描述自相矛盾：行 64 写"不传则查看全部"，同时写"默认 'my'"——LLM 无法确定 omit 行为 | 统一：删掉"不传则查看全部"，保留"默认 my" |
| 4 | **download_entries** | 描述承诺"下载的数据在返回结果中"，但返回结构只列出 `downloaded_count` 和 `diff_summary`，**未给出实际条目数据存放字段** | 补全返回字段（如 `entries: [...]`）及其结构 |
| 5 | **get_translation_config** | 返回字段用自然语言列举（"包括：provider、模型名……"），未给出结构化 JSON 格式，**LLM 无法确定字段名是 camelCase 还是 snake_case** | 补上 JSON 返回示例，明确所有字段名 |

### 中优先级（LLM 理解有偏差但不致命）

| # | 工具 | 问题 | 建议修复 |
|---|------|------|---------|
| 6 | **get_visible_entries** | stage 返回值列出枚举数字(0-5,9,-1)但未说明含义；原文/译文截断 200 字的注释放置不够醒目 | stage 值附简短含义；在描述中提及截断约束 |
| 7 | **select_entries** | entry_ids 标注"必填"，但 action=clear 时只需空列表——语义矛盾 | 改为"select/deselect 时必填，clear 时忽略" |
| 8 | **set_stage** | stage 枚举有跳跃（0-3→5→9→-1），未解释为何缺 4/6/7/8；空 entry_ids 行为未说明 | 补充"非连续值由 ParaTranz 平台定义"说明；标注空列表行为 |
| 9 | **manage_entry_labels** | batch_assign 需用户确认但无对应参数（如 confirm=true），LLM 无法通过接口表达确认 | 建议增加 confirmed 参数或明确"系统会弹出确认弹窗" |
| 10 | **start_translation** | 缺 ⚠ 重要前置标签。set_term_config 和 set_translation_config 都有 ⚠ 提醒，但 start_translation 的预检建议只是普通叙述 | 开头加 `⚠ 重要前置: 先调用 get_translation_config 确认配置` |
| 11 | **set_term_config** | term_sources 与 path 参数的依赖关系未说明（json_path 被设置但 term_sources 不含 "json" 时行为？）；空列表行为未定义 | 补充：无效组合的校验规则 |
| 12 | **write_back** | 多源并存（同时有 ESP 和 EET）时 target 推断优先级未定义；output_dir "仅 strings 可用" 藏于枚举描述尾部 | 补充优先级规则；output_dir 限制独立警告行 |
| 13 | **parse_sst** | 共同规则（line 65）称 action=create_slot 会"记录文件路径供 write_back 推断"，但 parse_sst 专用规则说"不支持 write_back"——矛盾 | 修正共同规则为条件性表述 |
| 14 | **import_strings** | path 相对路径解析基准（cwd vs 项目根）未说明；返回字段缺少类型标注 | 明确路径解析规则 |
| 15 | **run_postprocess** | 未说明与 start_polish 的区别（全流水线 vs 单润色）；phases 执行顺序未显式说明 | 补充与 start_polish 对比；说明 phases 顺序 |
| 16 | **get_quality_report** | 无参数，无法查看历史报告（与 list_quality_reports 描述矛盾——它说"获取文件名后可用 get_quality_report 查看详情"）；issues 内部结构未定义 | 增加 file 参数或修改 list 的描述 |
| 17 | **list_quality_reports** | 描述写"获取文件名后可用 get_quality_report 查看详情"，但 get_quality_report 无参数无法指定文件 | 与 get_quality_report 对齐：要么给后者加参数，要么修正描述 |
| 18 | **upload_entries** | 返回格式用 set notation `[{"key", "error"}]`——不是合法 JSON；entry_ids 参数命名误导（实际是 key 不是 id） | 改为 `[{"key": "...", "error": "..."}]`；参数描述强调是 key |
| 19 | **get_project_info** | 返回格式用 set notation `{"id", "name", "visibility", "member_count"}`——LLM 可能理解为 set 或数组 | 改为 `{"id": int, "name": str, ...}` |
| 20 | **get_app_state** | 返回字段只列名称无含义说明（esp_file/eet_file/xt_file 对 LLM 不够直观） | 加字段含义表格 |
| 21 | **get_current_filters** | active_filter_count 判定规则未定义（空列表算不算激活？）；filter_state 子字段值域未说明 | 定义 count 规则；标注 stage/search_field 可选值 |
| 22 | **switch_collection** | collection_name 和 slot_index 同时传入冲突时优先级未说明；collection_name 接受 key 还是 label 未明确 | 补充优先级；明确接受 key 或 label |
| 23 | **get_statistics** | 空集合时丢弃 4 个字段（untranslated/translation_rate/stage_distribution/category_distribution），LLM 需做存在性检查 | 空集合也返回完整字段签名（零值） |
| 24 | **get_current_project** | 有项目时返回 `{name, variant, collection}`，无项目时返回 `{active_project: null}`——**顶级 key 不一致**，LLM 无法统一解构 | 统一为 `{name: null, variant: null, collection: null}` |
| 25 | **list_local_projects** | 返回格式 `{projects: [{name}]}` 模糊——是 `{name: string}` 还是项目名列表？ | 明确为 `{projects: [{name: string}]}` |
| 26 | **get_upload_history** | `{history: [...]}` 过于模糊，LLM 无法预知条目结构（timestamp? filename? status?） | 补全历史条目字段定义 |

---

## 🔴 有问题（1 个工具）

| # | 工具 | 问题 | 建议 |
|---|------|------|------|
| 1 | **export_artifact** | (1) 返回 `{"result": ...}` + "具体结构取决于 API 响应"——LLM 完全无法对返回值做任何处理；(2) 未标注"需用户确认"; (3) 产出物去向完全未记录（磁盘路径? 下载URL? stdout?） | (1) 至少给一个示例返回结构；(2) 标注需用户确认；(3) 说明产出物保存位置 |

---

## 跨工具共性问题

| 问题类型 | 影响工具数 | 说明 |
|---------|-----------|------|
| **返回格式非法 JSON** | 4 | `{key, value}` set notation 不是有效 JSON，LLM 解析时会出错（upload_entries, get_project_info, get_paratranz_project, list_local_projects） |
| **返回字段缺类型/结构** | 8 | 没有结构化返回示例，LLM 无法确定字段名和类型 |
| **跨章节矛盾** | 3 | 同一工具在不同工具的描述中被引用时信息不一致（start_polish, parse_sst, list_quality_reports） |
| **默认值/可选行为二义性** | 5 | omit 参数的行为描述自相矛盾（list_projects, switch_collection 等） |
| **缺少使用端前置条件标签** | 3 | 需要用户确认/需要先调用某工具的前置条件不够醒目 |

---

## 结论

- **可用率**: 15/42 (35.7%) 无需修改即可被 LLM 正确理解和使用
- **需修复率**: 26/42 (61.9%) 存在影响 LLM 调用正确性的描述问题
- **阻塞率**: 1/42 (2.4%) export_artifact 对 LLM 基本不可用

**建议**: 优先修复高优先级 5 项（跨章节矛盾、返回字段缺失、行为描述错误），再批量修复中优先级项的返回格式和参数二义性问题。
