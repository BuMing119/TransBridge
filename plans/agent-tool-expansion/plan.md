# Agent 工具系统全面扩展 (agent-tool-expansion)

**对应需求**: [FR9](../docs/requirements.md) — Agent 工具系统全面扩展
**技术模块**: backend (smart_assistant)
**业务域**: Agent 工具系统
**状态**: ✔️ 已实现（26 Story 全部完成）
**创建日期**: 2026-05-10
**更新日期**: 2026-05-21（追加 Story 26: 断点续传与暂停/恢复）

## 功能边界

### 范围内
- 新建 `smart_assistant/tools/` 子包（11+ 个模块文件）
- ToolResult 数据类替代自由字典返回格式（含字典兼容 + success 语义修正）
- ExecutionContext 包装 AppContext + TaskManager（含 __getattr__ 代理兼容 v1 工具）
- HITLRequest/HITLResponse 统一人机交互协议（confirm / file_select / compare_confirm）
- GuardChain 中间件统一入口（execute_with_guardrails），GUI 和 MCP 共享
- TaskManager 单例管理 long_running 工具生命周期（线程安全强化）
- @require_collection / @validate_params 装饰器（含装饰器顺序规范）
- AppContext ViewModel 扩展（filter_state + 标签数据上移 + _translation_scope + pyqtSignal）
- _filter_entries() 公共筛选函数，消除多 Story 重复实现
- P0: 表格筛选/搜索/编辑/选择/批量stage标记 + 翻译执行控制 + 状态查询（~20 工具）
- P1: 标签管理 + 翻译配置（profile 预设方案切换）+ 后处理（含费用确认）+ ParaTranz（~20 工具）
- P2: 文件解析（权限 read）+ 文件写回 + 项目管理查询（~12 工具）
- Agent 注册更新 + orchestrator 可见性优化 + ExecutionEngine 适配 + MCP 护栏接入
- Story 14: 跨 Story 集成测试（筛选→选择→翻译→标记完整链路）
- 独立 PR: ParaTranz API 令牌桶限流、护栏审计日志、ToolSpec 移入 tools/

### 范围外
- 集合管理 CRUD（移除 slot / 重命名 / 迁移源手动追加）。注：创建 slot 和追加条目已由 Story 24 移入范围内。
- 项目创建/版本管理（仅 read 级查询）
- UI 导航操作（navigate_to 已裁剪）
- 新 Skill 定义文件
- Step2 表格 UI 代码改动（通过信号驱动，不改 UI 渲染逻辑）
- API Key 加密存储（桌面个人应用，保持 INI 明文）

## Story 清单

### Story 01: 基础设施搭建 — tools/ 子包 + ToolResult + ExecutionContext + HITL + GuardChain + 装饰器

**范围**: P0 阻塞级。本 Story 是后续所有 Story 的基础设施，升级为**工具系统核心基础设施**。

**验收标准**:
- [ ] `smart_assistant/tools/` 子包存在，含 `__init__.py`、`base.py`、`tool_v1.py`
- [ ] `ToolResult` 数据类定义在 `base.py`：`success: bool`, `message: str`, `data: dict | None`, `failed_items: list | None`, `truncated: bool`, `partial: bool = False`（B3: success 从三态改为 bool + partial 独立字段）
- [ ] `ToolResult` 添加 `get(key, default=None)` 和 `__getitem__` 字典兼容方法（B2: 兼容 `raw_result.get("success", True)` 旧调用）
- [ ] `ExecutionContext` 数据类定义在 `base.py`：包装 `app_context` + `task_manager`，含 `__getattr__` 代理（B4 + H9: v1 工具零改动兼容）
- [ ] `HITLRequest`/`HITLResponse` 数据类定义在 `base.py`，覆盖三种场景：confirm（确认弹窗）、file_select（parser 文件选择）、compare_confirm（下载对比确认）（H5）
- [ ] `execute_with_guardrails(spec, args, ctx)` 统一入口定义在 `base.py`，中间件链：PermissionGuard → InputValidationGuard → 工具执行 → OutputValidationGuard（B6: 消除 GUI/MCP 安全分叉）
- [ ] `_filter_entries(collection, filter_state) -> list[TranslationEntry]` 公共函数定义在 `base.py`（H8: 供 Story 04/08/10 复用）
- [ ] `@require_collection` 装饰器可用，自动注入 collection 并检查非空
- [ ] `@validate_params` 装饰器可用，按 ToolSpec.parameters 格式校验输入参数
- [ ] `base.py` 文档字符串明确推荐装饰器顺序：`@require_collection` 最外层，`@validate_params` 内层（E5）
- [ ] 基础路径遍历检测 `_detect_path_traversal()` 实现在 `InputValidationGuard` 中（E1: 检测 `../`、`..\\`、绝对路径注入）
- [ ] 输出脱敏 `_redact_dict()` 增加对 list 类型的递归处理（E12）
- [ ] 6 个 v1 工具函数从 `tool_registry.py` 迁移至 `tool_v1.py`，返回格式升级为 ToolResult
- [ ] `tool_registry.py` 仅保留 ToolSpec 数据类 + ToolRegistry 类 + v1 注册调用入口
- [ ] 现有所有调用方（prompts.py, execution_engine.py 等）不受影响

**实现步骤**:
1. 创建 `smart_assistant/tools/` 目录及 `__init__.py`
2. 在 `base.py` 定义 `ToolResult` 数据类（`success: bool` + `partial: bool` + `get()`/`__getitem__` 字典兼容）
3. 在 `base.py` 定义 `ExecutionContext` 数据类（含 `__getattr__` 代理转发到 `app_context`）
4. 在 `base.py` 定义 `HITLRequest`/`HITLResponse` 数据类（type: confirm/file_select/compare_confirm）
5. 在 `base.py` 实现 `execute_with_guardrails()` 统一入口（PermissionGuard → InputValidationGuard → 工具执行 → OutputValidationGuard）
6. 在 `base.py` 实现 `_filter_entries()` 公共函数
7. 在 `base.py` 实现 `@require_collection` 装饰器 — `def require_collection(func): wrapper(args, ctx) -> ToolResult`
8. 在 `base.py` 实现 `@validate_params(schema: dict)` 装饰器 — 类型检查 + 异常捕获，文档注明装饰器推荐顺序
9. 在 `InputValidationGuard` 中实现 `_detect_path_traversal()` 基础版（检测 `../`、`..\\`、绝对路径）
10. 在 `OutputValidationGuard._redact_dict()` 中追加 list 递归处理
11. 创建 `tool_v1.py`，将 6 个 v1 工具函数从 `tool_registry.py` 移入，返回 `ToolResult(...)` 替代 `{"success": ...}`
12. 更新 `tool_registry.py`：删除 v1 工具函数体，import 从 `tools.tool_v1` 导入
13. 更新 `smart_assistant/__init__.py` 导出 `ToolResult`, `ExecutionContext`, `HITLRequest`, `HITLResponse`
14. 运行现有调用方验证无 import 错误

**涉及文件**: 新建 `tools/__init__.py`, `tools/base.py`, `tools/tool_v1.py`；修改 `tool_registry.py`, `smart_assistant/__init__.py`, `guardrails/input_validator.py`, `guardrails/output_validator.py`
**详细文档**: `plans/agent-tool-expansion/stories/story-01-infra-tools-package.md`

---

### Story 02: TaskManager — 长运行任务生命周期管理

**验收标准**:
- [ ] `TaskManager` 单例类在 `tools/task_manager.py` 中实现（模块级双重检查锁防止竞态条件，E3）
- [ ] `TaskHandle` dataclass：`stop_event: threading.Event`, `pause_event: threading.Event | None`（B5 联动: 预留字段不实现）, `status: str`, `progress: dict`, `created_at: float`, `metadata: dict`
- [ ] `register(task_id, stop_event, metadata) -> str` — 注册新任务，返回 task_id
- [ ] `cancel(task_id) -> bool` — 设置 stop_event + 更新状态为 "cancelled"
- [ ] `get_status(task_id) -> dict` — 返回 progress dict 时深拷贝（E3: 防止并发遍历 RuntimeError）
- [ ] `list_active() -> list[str]` — 列出所有非终止状态的任务 ID
- [ ] `cleanup(task_id)` — 移除已完成/已取消的任务句柄，确保线程 join（O9: daemon=True 防止阻止进程退出）
- [ ] 跟踪所有线程引用，cleanup 时确保 join（O9）
- [ ] 线程安全（内部使用 `threading.Lock`）

**实现步骤**:
1. 定义 `TaskHandle` dataclass：含 `stop_event`, `pause_event`（预留，默认 None）, `status`, `progress`, `created_at`, `metadata`
2. 实现 `TaskManager` 类，`_tasks: dict[str, TaskHandle]` + `_lock: threading.Lock` + 模块级 `_instance_lock`
3. `register()` 生成 UUID task_id，创建 TaskHandle，存入 _tasks
4. `cancel()` 设置 `handle.stop_event.set()`，更新 `handle.status = "cancelled"`
5. `get_status()` 返回 progress 的深拷贝（`copy.deepcopy(handle.progress)`）
6. `list_active()` 过滤 `status in ("running", "paused")`
7. `cleanup()` 删除非活跃任务句柄，跟踪线程引用确保 join，daemon=True
8. 在 `tools/__init__.py` 导出 `TaskManager`

**涉及文件**: 新建 `tools/task_manager.py`；修改 `tools/__init__.py`
**详细文档**: `plans/agent-tool-expansion/stories/story-02-task-manager.md`

---

### Story 03: AppContext ViewModel 扩展

**验收标准**:
- [ ] `AppContext` 新增 `filter_state` 属性（dict）：`{stage: list[int], category: list[str], label: list[str], search_query: str, search_field: str}`
- [ ] `AppContext` 新增 `filter_changed` pyqtSignal
- [ ] `AppContext` 新增 `set_filter(**kwargs)` 方法 — 合并更新 filter_state 并 emit filter_changed
- [ ] `AppContext` 新增 `clear_filters()` 方法 — 重置 filter_state 为默认值
- [ ] **B1: 标签数据上移** — `AppContext` 新增 `label_library: dict[str, dict]`（标签库）、`entry_labels: dict[str, set[str]]`（条目-标签映射）、`label_data_changed` pyqtSignal
- [ ] 标签数据从 VariantStore 加载后写入 AppContext（VariantStore → AppContext → UI 订阅 + Tools 读写）
- [ ] **E8: _translation_scope 纳入正式属性** — property getter/setter，类型校验（stages 为 list[int]、action 为枚举值）
- [ ] **E6: filter_state 映射契约文档** — 明确 `search_field`（Agent 统一搜索字段）与 Step2 三个独立搜索框（ID/Key/Text）的映射关系
- [ ] 新增属性不破坏现有 AppContext 使用方

**实现步骤**:
1. 在 `AppContext` 的 `__init__` 中初始化 `_filter_state: dict`、`_label_library: dict`、`_entry_labels: dict`、`_translation_scope: dict`
2. 定义 `filter_changed = pyqtSignal(dict)` 和 `label_data_changed = pyqtSignal()` 信号
3. 实现 `set_filter(**kwargs)` — 深度合并 kwargs 到 _filter_state，emit signal
4. 实现 `clear_filters()` — 重置 _filter_state，emit signal
5. 实现 `filter_state` property (getter/setter)
6. 实现 `label_library` / `entry_labels` property (getter/setter)，setter 中 emit `label_data_changed`
7. 实现 `translation_scope` property，setter 中校验 stages 为 list[int]、action 为枚举值
8. 更新 Step2PreviewWidget 标签操作从 AppContext 读写（替换直接操作私有 `_label_library`/`_entry_labels`）
9. 编写 filter_state 映射契约文档

**涉及文件**: 修改 `ui/context.py`、`ui/workbench/step2.py`；更新 `docs/` 映射契约
**详细文档**: `plans/agent-tool-expansion/stories/story-03-appcontext-viewmodel.md`

---

### Story 04: P0 筛选 + 搜索 + 编辑 + 选择 + 批量标记工具 (editor namespace)

**范围**: 合并原 Story 04（筛选与搜索）和 Story 05（编辑与选择），统一负责「筛选→选择→编辑→标记」完整操作链。

**验收标准**:

**筛选与搜索**:
- [ ] `filter_by_stage` 工具 — 参数 `stages: list[int]`，调用 `ctx.set_filter(stage=stages)`，返回 ToolResult
- [ ] `filter_by_category` 工具 — 参数 `categories: list[str]`，调用 `ctx.set_filter(category=categories)`
- [ ] `filter_by_label` 工具 — 参数 `label_names: list[str]`，调用 `ctx.set_filter(label=label_names)`
- [ ] `search_entries` 工具 — 参数 `query: str, field: str`，调用 `ctx.set_filter(search_query=query, search_field=field)`
- [ ] `clear_all_filters` 工具 — 调用 `ctx.clear_filters()`
- [ ] `get_visible_entries` 工具 — 参数 `limit: int(50), offset: int(0)`，复用 `_filter_entries()` 公共函数（H8），返回条目摘要，上限 200，含 `truncated: true` + `total_count`

**编辑与选择**:
- [ ] `select_entries` 工具 — 参数 `entry_ids: list[str], action: str("select"/"deselect")`，操作独立 `_selected_ids: set[str]` 存储在 AppContext 上（H2: 与用户标签系统完全隔离）
- [ ] `edit_translation` 工具 — 参数 `entry_id: str, new_translation: str, new_stage: int | None = None`，直接修改 `TranslationEntry.translation`。不传 new_stage 时保持现有 stage 不变（H4: 不再硬编码 stage=2）
- [ ] `set_stage` 工具 — 参数 `entry_ids: list[str], stage: int`，支持批量设置翻译阶段（H3: 填补批量标记缺口，替代逐条 edit_translation）
- [ ] 所有工具注册到 `editor` namespace
- [ ] 筛选工具 permission: `read`；编辑/标记工具 permission: `write`

**实现步骤**:
1. 创建 `tools/tool_editor.py`，导入 `ToolSpec`, `ToolRegistry`, `ToolResult`, `_filter_entries`, 装饰器
2. 实现 4 个筛选工具 — 校验参数后调用 ctx.set_filter
3. 实现 `_tool_get_visible_entries` — 调用 `_filter_entries(collection, filter_state)` 公共函数
4. 实现 `_tool_select_entries` — 操作 `ctx._selected_ids`（add/discard），返回当前选中数
5. 实现 `_tool_edit_translation` — 修改 translation，可选传 new_stage
6. 实现 `_tool_set_stage` — 批量更新 `entry.stage = stage`
7. 注册全部工具到 `editor` namespace

**涉及文件**: 新建 `tools/tool_editor.py`（含原 Story 04 + 05 全部内容）
**详细文档**: `plans/agent-tool-expansion/stories/story-04-p0-filter-search-tools.md`

---

### Story 05: ~~P0 编辑与选择工具~~ — **已废弃**

**状态**: ⛔ 废弃。内容已合并至 Story 04。原编号保留，原 story 文档归档。

---

### Story 06: P0 翻译执行控制工具 (translator namespace)

**验收标准**:
- [ ] `start_translation` 工具 — 参数 `mode: str("translate"/"polish"/"mixed"), entry_ids: list[str] | None`，调用现有 AutoTranslator，通过 TaskManager 注册任务，返回 task_id
- [ ] `start_polish` 工具 — 参数 `entry_ids: list[str], intensity: str`，调用 LLMPolisher
- [ ] `stop_task` 工具 — 参数 `task_id: str`（**必传**），通过 TaskManager 获取 stop_event 并 set()，需用户确认（require_confirmation: true）（E7: 去掉不传参全部停止的隐式语义）
- [ ] `stop_all_tasks` 工具 — 无参数，停止所有运行中任务（E7: 显式独立工具，替代 stop_task 隐式全停）
- [ ] `get_task_status` 工具 — 参数 `task_id: str | None`，调用 TaskManager.get_status()
- [ ] ~~`pause_task` 工具~~ — **已移除**（B5: 假暂停，status 和实际行为不同步，API 费用持续消耗）
- [ ] 所有工具注册到 `translator` namespace
- [ ] 保留现有 `lookup_terms` / `translate_entries` 工具行为不变

**实现步骤**:
1. 创建 `tools/tool_translator.py`
2. 实现 `_tool_start_translation` — 创建 threading.Event → TaskManager.register() → 在后台线程启动 AutoTranslator.translate() → 进度回调更新 TaskManager 状态
3. 实现 `_tool_start_polish` — 同上但使用 LLMPolisher
4. 实现 `_tool_stop_task` — 必传 task_id，require_confirmation=true
5. 实现 `_tool_stop_all_tasks` — 遍历所有活跃任务并 cancel
6. 实现 `_tool_get_task_status` — 代理到 TaskManager
7. 注册工具到 `translator` namespace

**涉及文件**: 新建 `tools/tool_translator.py`
**详细文档**: `plans/agent-tool-expansion/stories/story-06-p0-translation-control.md`

---

### Story 07: P0 状态查询工具 + check_quality 增强 (default + proofreader namespace)

**验收标准**:
- [ ] `get_app_state` 工具 — 返回当前 step/活跃集合/项目/版本/筛选状态/API状态，permission: read
- [ ] `list_collections` 工具 — 返回所有已加载集合摘要，permission: read
- [ ] `switch_collection` 工具 — 参数 `collection_name: str | slot_index: int`，permission: write
- [ ] `get_current_filters` 工具 — 返回当前 filter_state，permission: read
- [ ] `get_statistics` 工具 — 返回条目总数/翻译率/stage分布/分类分布/标签分布（合并 get_collection_summary 功能，O8），permission: read
- [ ] `get_collection_summary` 标记 **deprecated**，保留转发到 get_statistics（O8）
- [ ] `check_quality` 工具（现有 proofreader namespace）— 返回格式升级为 ToolResult
- [ ] 状态查询工具注册到 `default` namespace

**实现步骤**:
1. 创建 `tools/tool_default.py`
2. 实现 `_tool_get_app_state` — 聚合 ctx 的各种状态信息
3. 实现 `_tool_list_collections` — 遍历 ctx 中的 slots/collections
4. 实现 `_tool_switch_collection` — 设置 ctx.active_slot
5. 实现 `_tool_get_current_filters` — 返回 ctx.filter_state
6. 实现 `_tool_get_statistics` — 遍历 collection 统计（合并原 get_collection_summary 功能）
7. 在 `tool_registry.py` 中标记 `get_collection_summary` deprecated
8. 在 `tool_registry.py` 中将现有 `_tool_check_quality` 迁移至 `tools/tool_proofreader.py`（新建），返回格式升级
9. 在模块底部注册

**涉及文件**: 新建 `tools/tool_default.py`, `tools/tool_proofreader.py`（部分）；修改 `tool_registry.py`（移动 check_quality + 标记 deprecated）
**详细文档**: `plans/agent-tool-expansion/stories/story-07-p0-state-query-proofread.md`

---

### Story 08: P1 标签管理工具 (editor namespace)

**前置依赖**: ⚠️ **硬依赖 Story 03**（AppContext 标签数据上移）。Story 03 未完成前，本 Story 无法实现。

**验收标准**:
- [ ] `list_labels` — 列出所有标签（name/color/count），permission: read
- [ ] `create_label` — 参数 `name: str, color: str`，permission: write
- [ ] `assign_label` — 参数 `entry_ids: list[str], label_name: str`，permission: write
- [ ] `remove_label` — 参数 `entry_ids: list[str], label_name: str`，permission: write
- [ ] `batch_assign_label` — 参数 `label_name: str`（批量分配给当前筛选范围内所有条目），复用 `_filter_entries()` 获取当前筛选条目（H8），permission: write，require_confirmation: true
- [ ] 所有工具通过 `ctx.label_library` / `ctx.entry_labels` 操作标签数据（不再通过 UI 层间接访问）
- [ ] 所有工具注册到 `editor` namespace

**实现步骤**:
1. 在 `tool_editor.py` 中实现标签管理函数
2. 通过 `ctx.label_library` 和 `ctx.entry_labels` 操作标签数据（B1 联动：数据已由 Story 03 上移至 AppContext）
3. `batch_assign_label` 调用 `_filter_entries(collection, filter_state)` 获取当前筛选条目再批量操作
4. 注册工具

**涉及文件**: 追加 `tools/tool_editor.py`
**详细文档**: `plans/agent-tool-expansion/stories/story-08-p1-label-tools.md`

---

### Story 09: P1 翻译配置工具 (translator namespace)

**安全改造**: 移除 `base_url` 自由输入，改为 profile 预设方案切换（H7 用户方案）。

**验收标准**:
- [ ] `get_translation_config` — 返回当前 LLM 配置/术语库设置/后处理阶段/作用域/当前 profile，permission: read
- [ ] `set_translation_config` — 参数 `profile: str | None, model: str | None, temperature: float | None, max_tokens: int | None, term_db: str | None, post_process_stages: list[str] | None`，permission: write
  - **profile 参数**：切换到 INI 中 `[llm_profiles]` 预设的 API 端点方案（如 `openai` / `anthropic` / `local_proxy`）
  - **不再接受 base_url 参数**：Agent 不能自由输入 URL，只能切换预设方案（H7）
- [ ] INI 配置文件新增 `[llm_profiles]` 节，格式：`profile_name = https://api.example.com/v1`
- [ ] `set_scope` — 参数 `stages/ labels/ categories/ action`，操作 `ctx.translation_scope` 正式属性（E8），permission: write
- [ ] `get_scope_preview` — 返回当前作用域下匹配的条目统计，permission: read
- [ ] 所有工具注册到 `translator` namespace

**实现步骤**:
1. 在 `tool_translator.py` 中实现配置工具
2. `get_translation_config` — 读取 LLMConfig.load_from_file() + INI `[llm_profiles]` + ctx 中的配置状态
3. `set_translation_config` — 若传 profile 则校验是否在 `[llm_profiles]` 预设列表中，拒绝不在列表的值；其他参数直接更新 LLMConfig 并保存
4. 扩展 INI 配置模型支持 `[llm_profiles]` 节
5. `set_scope` / `get_scope_preview` — 操作 `ctx.translation_scope` 正式属性（带类型校验）

**涉及文件**: 追加 `tools/tool_translator.py`；修改 `config/llm.py`（新增 `[llm_profiles]` 支持）
**详细文档**: `plans/agent-tool-expansion/stories/story-09-p1-translation-config.md`

---

### Story 10: P1 后处理全套工具 (proofreader namespace)

**验收标准**:
- [ ] `run_consistency_check` — 执行术语一致性检查，permission: read
- [ ] `run_format_validation` — 执行格式校验，permission: read
- [ ] `run_llm_refinement` — LLM 修复，is_long_running，permission: write，**require_confirmation: true**（E10: 确认提示显示预估条目数和费用）
- [ ] `run_llm_polish` — LLM 润色，is_long_running，permission: write，**require_confirmation: true**（E10）
- [ ] `run_llm_arbitration` — LLM 裁决，is_long_running，permission: write，**require_confirmation: true**（E10）
- [ ] `get_quality_report` — 获取最近报告摘要，permission: read
- [ ] 提取 `_run_postprocess_phase()` 工厂函数，减少 3 个 long_running 工具的 PostProcessor 胶水代码重复（E9）
- [ ] 所有工具注册到 `proofreader` namespace

**实现步骤**:
1. 在 `tool_proofreader.py` 中实现后处理工具
2. 实现 `_run_postprocess_phase()` 工厂函数（统一初始化 PostProcessor、进度回调、TaskManager 注册）
3. 封装现有 PostProcessor 的方法调用
4. long_running 工具设置 `require_confirmation=True`，确认消息包含预估条目数和费用
5. 注册工具

**涉及文件**: 追加 `tools/tool_proofreader.py`
**详细文档**: `plans/agent-tool-expansion/stories/story-10-p1-postprocess-tools.md`

---

### Story 11: P1 ParaTranz 平台工具 (paratranz namespace)

**验收标准**:
- [ ] `list_projects` — 列出 ParaTranz 项目（all/mine），permission: read
- [ ] `get_project_info` — 项目详细信息，permission: read
- [ ] `compare_with_remote` — 对比本地与远程差异（前 20 条详情），permission: read
- [ ] `upload_entries` — 上传条目，is_long_running，permission: write，force_overwrite 需确认
- [ ] `download_entries` — 下载条目，is_long_running，permission: write，require_confirmation: true。**单阶段执行**（O7: 下载完成后自动附加对比摘要到 ToolResult.data，不再分两阶段交互）
- [ ] `export_artifact` — 导出工件，is_long_running，permission: write
- [ ] `get_upload_history` — 上传历史，permission: read
- [ ] **实施前完成 API surface 审查**（O10: 确认 ParatranzClient 完整 API，产出 API-工具映射表）
- [ ] 所有工具注册到 `paratranz` namespace

**实现步骤**:
1. 审查 `ParatranzClient` 完整 API surface，产出 API-工具映射表（O10）
2. 创建 `tools/tool_paratranz.py`
3. 封装现有 ParatranzClient API 调用
4. `download_entries` 单阶段实现：后台线程下载 + 完成后自动对比摘要写入 ToolResult.data（O7）
5. long_running 工具通过 TaskManager 管理
6. 注册工具

**涉及文件**: 新建 `tools/tool_paratranz.py`
**详细文档**: `plans/agent-tool-expansion/stories/story-11-p1-paratranz-tools.md`

---

### Story 12: P2 解析 + 写回 + 项目查询工具 (parser/writer/default namespace)

**权限修正**: parser 6 工具从 `write` 改为 `read`（H6: 解析不产生持久化副作用，本质是读取文件系统加载到 ctx）。

**验收标准**:
- [ ] parser namespace 6 工具：`parse_esp` / `parse_eet` / `parse_xt` / `parse_sst` / `import_json` / `import_strings`（均为 **read**，H6；path 可选——不传触发 HITL file_select）
- [ ] 文件扩展名白名单：`.esp`, `.esm`, `.esl`, `.xml`, `.json`, `.strings`（E1: 路径遍历强化）
- [ ] 路径遍历检测强化：read 工具拒绝遍历路径，write/admin 工具拒绝所有非项目目录内路径（E1）
- [ ] writer namespace 4 工具：`write_to_esp` / `write_to_eet` / `write_to_xt` / `write_to_strings`（均为 admin，需用户确认）
- [ ] default namespace 2 工具：`list_local_projects` / `get_current_project`（均为 read）
- [ ] 现有 `write_back` 标记 deprecated，保留转发

**实现步骤**:
1. 创建 `tools/tool_parser.py` — 封装现有 parser 模块（EET_XmlParser/XT_XmlParser/SST_Parser/PluginParser），权限 read
2. 在 parser 工具中应用文件扩展名白名单（仅允许 `.esp/.esm/.esl/.xml/.json/.strings`）
3. 路径遍历检测强化：read 工具拒绝 `../`/`..\\`/绝对路径；write/admin 工具拒绝非项目目录内路径
4. 创建 `tools/tool_writer.py` — 封装现有 PluginWriter/EET/XT writer，所有工具 require_confirmation
5. 在 `tool_default.py` 追加 `list_local_projects` / `get_current_project` — 读取 workspace.json
6. 在 `tool_registry.py` 中标记 `write_back` deprecated，添加转发逻辑
7. 注册各工具

**涉及文件**: 新建 `tools/tool_parser.py`, `tools/tool_writer.py`；追加 `tools/tool_default.py`；修改 `tool_registry.py`
**详细文档**: `plans/agent-tool-expansion/stories/story-12-p2-parser-writer-project.md`

---

### Story 13: Agent 注册更新 + ExecutionEngine 适配 + MCP 护栏 + orchestrator 优化

**范围调整**: ExecutionContext 定义已移至 Story 01（B4）；本 Story 负责 ExecutionEngine 消费 ExecutionContext + MCP 护栏接入。

**验收标准**:
- [ ] AgentRegistry 新增 4 个 Agent（parser/editor/paratranz/writer），扩展 3 个（translator/proofreader/orchestrator）的工具列表
- [ ] AgentSpec 工具列表支持 `namespace:*` 通配符（O3: 如 `tools: ["filter:*", "editor:set_stage"]` 自动展开为 namespace 下所有工具）
- [ ] ExecutionEngine 工具执行上下文升级：组装 `ExecutionContext(app_context=ctx, task_manager=TaskManager())` 传入 `spec.execute()`（B4 联动）
- [ ] MCP 通道接入 GuardChain 中间件（B6 联动：`adapter.py` 的 `call_tool()` 改为调用 `execute_with_guardrails()`，不再直接 `spec.execute()`）
- [ ] MCP 模式下 admin/write 确认自动拒绝（无 UI 通道降级策略）
- [ ] orchestrator Agent 不直接暴露 50+ 工具 schema，改为 7 个元工具描述（parse/manage_entries/translate/check_quality/sync_paratranz/write/query_state）
- [ ] `Orchestrator.map_to_steps()` 修复：从 LLM action 字段映射到具体 tool_name，而非始终取 `tools[0]`（E4）
- [ ] 现有 ReAct 循环 / PlanCard / Skill 调用不受影响

**实现步骤**:
1. 更新 `agent_registry.py` 的 `init_presets()` 方法 — 新增 4 个 Agent 定义 + 更新 3 个现有 Agent 的 tools 列表
2. 实现 `namespace:*` 通配符展开逻辑（在 ToolRegistry 或 AgentSpec 解析时展开）
3. 更新 `execution_engine.py` 的 `_run_single()` — 组装 ExecutionContext 传给 `spec.execute()`（而非直接传裸 AppContext）
4. 更新 `mcp/adapter.py` 的 `call_tool()` — 改为调用 `execute_with_guardrails()` 统一入口；MCP 模式下 admin/write 确认自动拒绝
5. 修复 `orchestrator.py` 的 `map_to_steps()` — 从 LLM action 字段映射到具体 tool_name
6. 在 orchestration prompts 中实现 7 个元工具描述
7. 验证：对话测试确保 orchestrator 能正确路由到子 Agent

**涉及文件**: 修改 `agents/agent_registry.py`, `execution_engine.py`, `mcp/adapter.py`；可能修改 `agents/orchestrator.py`
**详细文档**: `plans/agent-tool-expansion/stories/story-13-agent-integration.md`

---

### Story 14: 跨 Story 集成测试（新增）

**验收标准**:
- [ ] 完整链路测试：筛选（filter_by_stage/category/label）→ 搜索（search_entries）→ 选择（select_entries）→ 编辑（edit_translation）→ 批量标记（set_stage）→ 翻译执行（start_translation → get_task_status）
- [ ] 标签系统测试：create_label → assign_label → filter_by_label → batch_assign_label → remove_label
- [ ] 安全护栏测试：路径遍历拒绝、MCP 中间件链、权限拒绝（parser write 操作应被拒）
- [ ] 翻译配置测试：profile 预设方案切换（合法/非法 profile）、set_scope/get_scope_preview
- [ ] ParaTranz 集成测试：compare_with_remote → download_entries（单阶段）→ upload_entries
- [ ] 文件解析测试：parse_esp/parse_eet 等 6 工具的 read 权限 + 扩展名白名单
- [ ] 文件写回测试：write_to_esp 等 4 工具 admin 确认流程

**实现步骤**:
1. 编写集成测试脚本，覆盖筛选→选择→翻译→标记完整链路
2. 验证各 Story 之间的数据传递（filter_state → _selected_ids → entry.stage）
3. 验证安全护栏在不同权限级别下的行为
4. 验证 MCP 模式下降级策略

**涉及文件**: 新建测试文件 `tests/test_agent_tool_integration.py`（或类似路径）
**详细文档**: `plans/agent-tool-expansion/stories/story-14-integration-tests.md`

---

### Story 15: FR9.11 工具补完 — 搜索维度扩展 + ParaTranz 项目查询与切换

**优先级**: P1 | **新增工具**: 2 | **增强工具**: 1 | **涉及文件**: 4

对 FR9.2 和 FR9.5 已编码工具的补完：
- `search_entries` 的 field 从 4 值扩展至 6 值（id/key/original/translation/context/all），底层 `filter_entries()` 补全 translation/context/all 搜索分支
- 新增 `get_paratranz_project`（read）和 `switch_paratranz_project`（write）两个工具，项目选中状态存 `AppContext.paratranz_project_id`（会话内有效），切换后自动关联已有 PT 工具的 `project_id` 默认值

**涉及文件**: `tools/tool_editor.py`, `tools/base.py`, `tools/tool_paratranz.py`, `ui/context.py`
**详细文档**: `plans/agent-tool-expansion/stories/story-15-tool-completion.md`

---

### Story 16: Agent 死代码清理 + 注册样板消除

删除 `agents/orchestrator.py` + `agents/agent_worker.py`（~194 行从未运行），7 模块调用 `ToolRegistry.register_tools()` 消除注册样板（-35 行）。**零风险**，工具数不变。

**详细文档**: `plans/agent-tool-expansion/stories/story-16-dead-code-registration.md`

---

### Story 17: set_filters 合并 (5→1)

`filter_by_stage/category/label` + `search_entries` + `clear_all_filters` → `set_filters`。6 个可选参数（均为 `None`=保持/`[]`=清除），`clear` 控制叠加语义。旧工具保留 deprecated wrapper。**净减 4 工具**。

**详细文档**: `plans/agent-tool-expansion/stories/story-17-set-filters-merge.md`

---

### Story 18: stop_task 合并 (2→1)

`stop_task` + `stop_all_tasks` → `stop_task`（`task_id` 改为可选，`None`/`""`=停止全部）。保留 `require_confirmation=True`。**净减 1 工具**。

**详细文档**: `plans/agent-tool-expansion/stories/story-18-stop-task-merge.md`

---

### Story 19: write_back 合并 (4→1)

`write_to_esp/eet/xt/strings` → `write_back`，dispatch 表路由。4 个实现重命名为 `_write_to_*_impl`，外层统一 `@require_collection`。保留 `admin` 权限 + 确认。**净减 3 工具**。

**详细文档**: `plans/agent-tool-expansion/stories/story-19-write-back-merge.md`

---

### Story 20: manage_entry_labels 合并 (4→1)

`create_label` + `assign_label` + `remove_label` + `batch_assign_label` → `manage_entry_labels`，`action` 参数（`create/assign/unassign/batch_assign`）。⚠️ **用户裁决**：含 `create_label`。需在 S17 后串行（同文件）。**净减 3 工具**。

**详细文档**: `plans/agent-tool-expansion/stories/story-20-manage-entry-labels-merge.md`

---

### Story 21: 工具描述强化 + 系统 prompt + 测试补全

重写合并后工具 description（三原则），系统 prompt 追加工具选择指南，参数矩阵测试 ~30 用例，LLM schema 回归验证。**合并后总计 42 工具**。

**详细文档**: `plans/agent-tool-expansion/stories/story-21-descriptions-tests.md`

---

### Story 22: 工具描述全面重写（Claude Code 参考格式）

参照 `docs/temp/claude-code-tools-reference.md` 三段格式（①功能描述 ②参数说明 ③使用规则），重写全部 45 工具描述。分 5 批次（editor→translator→writer/parser→proofreader/paratranz→default），每批次先确认方案再编码。

**详细文档**: `plans/agent-tool-expansion/stories/story-22-tool-description-rewrite.md`

---

### Story 23: TranslationEntry.key 升主索引

Collection 主索引从 `id` 切换为 `key`（跨 ParaTranz 同步稳定）。3 子 Story: 23a Collection 索引改造 → 23b 工具适配 + 23c 解析层/测试适配。23a 阻塞级先执行，23b+23c 并行。涉及 12+ 文件。

**详细文档**: `plans/agent-tool-expansion/stories/story-23-key-primary-index.md`

---

### Story 24: Parser 工具副作用补全 — 解析结果落地为 Slot 或追加条目

**对应需求**: FR9.12 | **优先级**: P1 | **涉及文件**: 3

为 `tool_parser.py` 中 6 个工具新增 `action` 参数（`create_slot`/`append`），使解析结果不再丢弃，而是创建 CollectionSlot 或追加到当前活跃集合。permission 从 `read` 改为 `write`，副作用通过 PermissionGuard 触发 HITL 确认。

**详细文档**: `plans/agent-tool-expansion/stories/story-24-parser-side-effects.md`

---

### Story 25: 后处理工具统一 — run_postprocess 替代 5 个独立工具

**对应需求**: FR9.4 | **优先级**: P0 | **涉及文件**: 2

QA审计发现 5 个后处理工具全部运行时崩溃（调用不存在的 API + 缺少 LLMClient）。废弃 5 个独立工具，新增 1 个 `run_postprocess` 统一工具，直接包装 GUI 同款 `PostProcessor.process_entries()` 五阶段流水线。proofreader namespace 6→2 工具。

**详细文档**: `plans/agent-tool-expansion/stories/story-25-postprocess-unification.md`

---

### Story 26: 后处理断点续传与暂停/恢复

**对应需求**: FR9.4.7, FR9.4.8 | **优先级**: P2 | **涉及文件**: 4

为 `run_postprocess` 工具补全 checkpoint resume 和 pause/resume 功能。`PostProcessor.process_entries()` 已原生支持 `pause_event` 和 `checkpoint` 参数，`PostProcessCheckpoint` 已有完整 save/load API，`TaskHandle` 已预留 `pause_event` 字段——本 Story 在工具层串联这些已有能力。

**验收标准**:
- [ ] `run_postprocess` 每阶段完成后保存 `PostProcessCheckpoint` 到文件；再次调用时检测已有 checkpoint，跳过已完成阶段（`is_batch_completed` 按 phase+entry_ids 匹配）
- [ ] 正常完成或用户停止后自动删除 checkpoint 文件
- [ ] `stop_task` 工具扩展 `action` 参数：`"pause"`（set pause_event） / `"resume"`（clear pause_event） / `"stop"`（set stop_event，默认行为）
- [ ] `get_task_status` 对 paused 任务返回 `"paused"` 状态；`list_active()` 包含 paused 任务
- [ ] `run_postprocess` 将 `pause_event` 传入 `process_entries()`，暂停时等待当前批次完成后挂起
- [ ] checkpoint 文件损坏时跳过恢复，从头开始，记录警告日志

**涉及文件**:
- `src/transbridge/smart_assistant/tools/tool_proofreader.py` — 主战场：checkpoint 创建/保存/加载/清理 + pause_event 传递
- `src/transbridge/smart_assistant/tools/task_manager.py` — `list_active()` 含 paused + `pause()`/`resume()` 方法
- `src/transbridge/smart_assistant/tools/tool_translator.py` — `stop_task` 扩展 `action` 参数

**详细文档**: `plans/agent-tool-expansion/stories/story-26-checkpoint-pause.md`

---

## 独立 PR（不进 Story 排期）

以下三项作为独立 PR，在 Story 开发期间择机提交：

| PR | 内容 | 来源 |
|----|------|------|
| ParaTranz 限流 | 令牌桶限流器（每秒最多 10 请求），防止误操作触发 API 封禁 | O4 |
| 护栏审计日志 | 记录每次护栏触发（权限拒绝、输入校验拦截、输出脱敏）的结构化日志（时间戳、工具名、触发规则、输入摘要） | O6 |
| ToolSpec 迁移 | ToolSpec/ToolRegistry 从 `tool_registry.py` 移入 `tools/` 子包，`tool_registry.py` 变为重导出壳 | O2 |

## P2 后续迭代

| 项目 | 内容 | 来源 |
|------|------|------|
| Reflexion 写工具重试 | 允许 Reflexion 对写工具重试，但需条件控制——在具体流程中根据操作上下文和重试次数动态判断，不在 ToolSpec 层一刀切禁止 | O5（用户方案） |

## 架构依赖

- **ADR-008**: smart_assistant 分层原则（UI→后端单向依赖，后端不依赖 UI）
- **ADR-008 更新 Phase 2**: agents/ 子包 + ToolRegistry namespace + AgentRegistry 预置定义
- **ADR-012**: 安全护栏 read/write/admin 权限分级 + MCP 适配 + 中间件链（GuardChain）
- **ADR-005**: TOML 格式 Skill 定义（新工具可能被 Skill 引用）
- **ADR-011**: StatefulDAGExecutor（工具执行结果需兼容 Checkpoint 序列化——ToolResult 通过 to_dict() 保证 JSON 兼容）

## 风险与回退方案

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| Story 01 工作量过大（核心基础设施升级） | 中 | 高 | Story 01 拆分为 2-3 次对话完成，优先交付 ExecutionContext + ToolResult 以解锁并行 Story |
| tools/ 子包拆分后 import 路径断裂 | 中 | 高 | Story 01 完成后逐文件验证 import |
| AppContext ViewModel 扩展改变现有行为 | 低 | 高 | filter_state/label 为新增属性，不改现有 API；Step2 重构时保留信号订阅兼容 |
| editor 工具纯数据操作与 UI 实际行为不一致 | 中 | 中 | Story 04 完成后 Step2 表格信号订阅验证（B1 联动） |
| TaskManager 线程安全问题 | 低 | 高 | threading.Lock + 深拷贝 + 双重检查锁 + 单元测试覆盖并发场景 |
| 50+ 工具注入 orchestrator prompt 导致 LLM 质量下降 | 中 | 中 | Story 13 的元工具描述 + namespace 通配符方案缓解，必要时降级为完整 schema |
| MCP headless 模式下部分工具不可用 | 低 | 低 | parser/writer 工具通过 HITL 降级（path 可选），纯数据工具全部可用；admin/write 确认自动拒绝 |
| profile 预设方案切换限制 Agent 灵活性 | 低 | 低 | 用户在 INI 中可配置任意数量预设方案（含本地代理），覆盖主流 LLM 提供商 |
| 合并后工具描述过长导致 LLM 困惑 | 中 | 中 | 遵循描述三原则（区分信号/参数前置/组合示例），S21 做 LLM 选择回归测试 |
| write_back target 参数 LLM 误选 | 中 | 高 | 描述中明确推断规则（"有 ESP → esp，有 EET → eet"）+ 回显确认写入类型 |
| S17 和 S20 同文件串行合并冲突 | 低 | 低 | S20 明确在 S17 之后执行，代码 review 确认无冲突 |
| 旧工具 deprecated wrapper 被外部引用 | 低 | 低 | 保留 wrapper 1-2 迭代，观察日志警告后清理 |

## 综合整改状态增量（2026-08-18）

- `partially-verified`：保留 26 Story 历史交付；工具存在/单测通过不等于 registry、parser/writer、Task、ParaTranz 与 Observation 真实调用链已验收。
- `blocked_by`：`platform-contract-foundation-v2` S02～S05、`translation-io-kernel-v2` S01/S04、`unified-task-translation-runtime-v2` S01/S07、`paratranz-sync-service-v2` S02～S04。
- `superseded_by`：TaskManager、AppContext 写状态、parser/writer 直接编排和网络工具合同分别由对应 V2 use case/facade 取代；旧工具名按删除门禁保留。
