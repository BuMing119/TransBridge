# AI 翻译版本快照与显式保存计划

- **状态**：已完成（2026-08-27，相关 QA 通过）
- **日期**：2026-08-27
- **对应需求**：AI 翻译、润色、混合和自定义工作流在运行前创建版本快照；完成报告提供“保存翻译”，保存后再创建版本快照。

## 目标

- 所有 AI 工作流在真正启动 Worker 前创建当前活动版本的自动快照，失败时不进入 AI 调用。
- AI 结果完成后仍可先检查报告；只有用户点击“保存翻译”时才持久化当前结果，并在保存成功后创建第二个快照。
- V2 权威项目生命周期和旧版 `VariantStore` 都遵守同一用户可见顺序与失败语义。

## 非目标

- 不改变翻译、校改、混合或自定义工作流的 Prompt、候选生成和接受规则。
- 不把关闭报告、取消任务或失败任务视为“保存翻译”。
- 不新增第三套快照格式，也不改变已有快照加载与项目导出格式。
- 不把批量多插件翻译纳入本次单版本快照事务；批量任务没有唯一活动版本边界。

## 当前实现事实与关键约束

- `AITranslatorWindow` 的 translate、polish、mixed 和 custom 预设最终进入三个运行入口；custom 复用所选有效模式，不需要独立 Worker。
- translate Worker 当前直接更新内存 `TranslationEntryCollection`；polish/mixed 在结果提交边界更新集合，之后统一打开 `_TranslationReportDialog`。
- `_TranslationReportDialog` 当前只有“打开 Excel”和“关闭”，没有持久化动作。
- V2 `ProjectLifecycleService.save_snapshot()` 已能原子写快照，但 `GuiProjectCommandFacade`/`AppContext` 尚未向 AI UI 暴露批量译文提交、保存和快照组合能力。
- 旧版项目通过 `VariantStore.collect_from()`、`save()`、`save_snapshot()` 持久化；需保持兼容。
- 快照和项目保存包含磁盘 I/O，必须通过后台 Worker 执行，不阻塞 Qt 事件循环。
- `ai_translator_window.py` 接近 500 行门禁；新增编排责任放入独立模块。

## Story 1：统一 AI 版本快照事务

### 验收标准

- translate、polish、mixed 以及 custom 派生执行在 Worker 启动前创建名称可识别的“AI …前”快照。
- 无活动项目/版本、快照写入失败或运行身份已失效时，AI Worker 不启动，并显示可操作错误。
- V2 后置保存将当前集合的译文和阶段以一次版本变更提交，随后保存正式版本，最后创建“AI …后”快照；任一步失败都不伪报成功。
- 旧版后置保存先从当前集合收集版本数据并保存，再创建快照。
- 同一完成报告重复点击不会重复提交或创建多个后置快照。

### 文件与步骤

- 修改 `application/projects/gui_facade.py`：增加 V2 批量条目状态提交与快照委托，复用现有 `_commit_variant` 和生命周期服务。
- 修改 `ui/context.py`：提供统一的活动版本快照、AI 结果保存接口，内部适配 V2 与旧版项目。
- 新增 `ui/tools/ai_translator/version_snapshot.py`：生成稳定快照名称，使用 `ApiWorker` 编排前置快照和“提交 → 保存 → 后置快照”，管理重复调用与错误投影。
- 最小修改 `ai_translator_window.py`：三个运行入口在前置快照完成回调后再启动既有 Worker。

### 测试策略

- application 测试覆盖 V2 批量提交一次 revision、未知条目拒绝、生命周期快照委托和错误保真。
- UI 纯协调器测试覆盖 V2/旧版调用顺序、前置失败阻断、后置失败不创建快照、重复保存幂等。
- 窗口 slice 测试覆盖四种模式都走前置快照门禁。

## Story 2：完成报告的“保存翻译”动作

### 验收标准

- translate、polish、mixed/custom 的完成报告都显示“保存翻译”按钮。
- 点击后按钮进入忙碌/禁用状态；成功时显示“翻译已保存并创建快照”，且按钮保持已完成状态。
- 保存或快照失败时报告保留、按钮可重试，并显示具体失败诊断；不得静默关闭报告。
- 只关闭报告不会保存版本或创建后置快照；任务取消/错误界面不提供伪成功动作。
- 现有“打开 Excel”、条目定位、报告后台渲染和窗口所有权保持兼容。

### 文件与步骤

- 修改 `_translation_report_dialog.py`：通过可选 callback/信号接入保存动作，只负责按钮状态和结果反馈。
- 修改 `_translation_progress_window.py`、`result_view.py` 和 `run_controller.py`：把当前运行的保存协调器传给三类报告入口。
- 补充报告 UI 和运行组合测试，确认 translate/polish/mixed 的回调绑定一致。

### 测试策略

- Qt 测试覆盖按钮可见性、单次执行、失败重试、成功锁定和关闭不保存。
- 现有报告、AI translator slice、运行控制器回归不得改变。

## 依赖顺序

Story 1 → Story 2。先建立可独立验证的保存/快照事务，再让报告按钮消费该能力。

## 风险与回退

- V2 集合条目必须按稳定 `EntryKey.local_key` 映射；映射缺失时整体拒绝提交，避免部分保存。
- 翻译完成到用户点击保存之间如果切换了活动项目/版本，协调器必须按启动时身份拒绝保存，避免写入错误版本。
- 前置快照名称使用模式和时间，底层 V2 内容寻址仍负责唯一存储；旧版文件名继续由 `VariantStore` 清理非法字符。
- 若保存按钮 UI 回归，可移除按钮绑定而不迁移快照数据；已创建快照仍由现有生命周期加载能力读取。

## 明确假设

- “保存翻译”指把当前 AI 已接受并显示在工作台集合中的结果保存到活动版本，而不是写回 ESP/EET/SST 等源文件。
- 为兑现“先创建快照”，没有活动项目版本或前置快照失败时，本次 AI 运行不启动。
- 本需求覆盖单一活动版本上的 translate、polish、mixed 和 custom；批量插件翻译另行设计跨版本快照策略。

## 完成证据

- 项目生命周期与快照命令回归：49 passed。
- AI 翻译窗口、润色/混合进度、完成报告与版本快照回归：77 passed。
- V2/旧版保存顺序、项目切换保护、后置快照重试幂等、任务失败终态与报告按钮专项测试：19 passed。
- 本次 14 个相关 Python 文件 Ruff lint/format 与 `git diff --check` 通过。
- 全仓 Ruff 检查已执行；失败来自仓库既有未格式化/历史 lint 项，本次相关文件单独检查全部通过。
