# 智能助手八项 UI 设计优化

日期：2026-10-16
Epic：assistant-ui-remediation
Story：S06～S13
关联：[计划](../../../../plans/assistant-ui-remediation/plan.md)

## 增量边界

用户明确要求按 bm-pilot 优化上一轮八项设计问题。本记录只描述 S06～S13；第一阶段 S01～S05 的既有增量保持不变。开发前保存了 301 个相关 Python 文件快照并逐文件比较，用于区分第一阶段和本轮差异；快照已清理。既有术语工作线、依赖锁和构建配置不归入本记录，没有提交或推送。

以下未注明前缀的生产文件位于 `src/transbridge/ui/tools/smart_assistant/`，测试文件位于 `tests/ui/tools/smart_assistant/`；其余路径明确列出。

## 逐文件变化

### S06 统一任务与控制

- 新增 UI `task_presentation.py`：按 request_id 或完整 job_id/run_id 关联后台工作，分别呈现事项完成数量和实际处理进度，暂停/终态优先于旧阶段文本。
- 修改 `request_progress_view.py`：突出目标，直接提供暂停、继续、取消、原始要求及结果入口，诊断操作置于次级菜单。
- 修改 `request_list_view.py`：按任务目标展示摘要，刷新保留所选任务身份，主操作根据状态显示，提供原始要求/结果入口。
- 修改 `conversation_presentation.py`：常驻任务摘要与会话内可折叠任务列表，取代原独立管理对话框；关联后台工作并接入原始消息定位。
- 修改 `task_monitor.py`：保留完整运行快照供投影，隐藏已归组工作，仅展示其他后台任务；清理按钮依据实际可见终态任务。
- 新增 UI `task_monitor_actions.py`：操作前重验会话/项目/版本范围，批量清理只处理可见任务，防止清理其他会话或隐藏的归组任务。
- 新增 `smart_assistant/tools/request_task_control.py`：固定 JobRef/run_id，通过既有应用控制桥同步实际后台状态、线程暂停信号和持久 user_job_control 等待；终态竞争不重启，失败保留任务上下文和原始异常。
- 修改 `request_binding.py`：既有后台 command 成功后接入真实暂停/恢复；中断使结果页失效，关闭释放结果绑定。

### S07、S10、S11、S12 执行方式、开始引导、技能与布局

- 新增 `execution_mode_view.py`，修改 `input_view.py`：执行模式常驻、说明确认边界并同步恢复状态；运行中发送显示“发送补充”，不扩大自动模式权限。
- 新增 `onboarding_view.py`，修改 `quick_actions.py`：按模型、项目、选择状态提供可执行引导；没有宿主能力时隐藏对应按钮。
- 新增 `skill_launcher.py`：技能选择只打开说明与可编辑请求，显式启动才发送；拒绝后保留内容，切会话/清空/关闭使预览失效。
- 修改 `smart_assistant/skills/skill_executor.py`：支持编辑后的 user_request，返回是否接纳；保留原调用方式。
- 新增 `composer_editor.py`：输入期望高度 44～164 px，紧凑布局允许缩小并内部滚动，避免长草稿撑高窗口。
- 修改 `panel_header.py`：显示当前会话名称；修改 `session_list_widget.py`：窄侧栏保留新建和会话菜单。
- 修改 `panel_layout.py`：minimumSizeHint 按可折叠侧栏宽度计算，解决真实中文字体下 QDockWidget 缓存 541 px 最小宽度、无法缩到 480 px 的根因。
- 修改 `panel.py`：注入真实导航能力，同步会话标题，委托安全后台任务操作。
- 修改 `chat_widget.py`：仅接入导航、模式刷新、技能预览生命周期和窄屏边距；保留延迟初始化前安全关闭。
- 修改 `ui/shell/tool_windows.py`，新增 `ui/shell/assistant_navigation.py`：打开真实 AI 服务设置/项目；结果定位验证原项目和版本、词条身份及工作台显示标识唯一性，拒绝跨版本或重名错定位。

### S08、S09 确认前核对与执行后验收

- 新增 `smart_assistant/operation_preview.py`：只读解析实际操作范围和词条旧值，沿用翻译、润色、后处理等现有范围规则；未知值、执行时生成的译文及多步依赖明确说明。
- 新增 `confirmation_preview_binding.py`：后台加载预览，按确认代际隔离，加载期间禁用执行。
- 新增 `change_preview_view.py`：有完整词条列表的模型表格及可选择复制的全文对照，避免逐行创建控件。
- 修改 `operation_preview.py`、`tool_card.py`、`plan_card.py`、`confirmation_view.py`：呈现可读操作、原文/修改前后译文与范围，把 JSON 移到高级参数；保留原确认与取消契约。
- 新增 `application/assistant_requests/result_review.py`：读取该请求的已保存执行及撤销凭据，提供变更、失败、导出位置与真实撤销范围；不读取当前集合来补造历史结果，保留取消、未执行、待核对状态。
- 新增 UI `result_review.py`：稳定的结果验收页，支持失败筛选、完整对照、定位首条选中词条、可信导出位置和明确撤销。
- 修改 `request_undo_binding.py`：完成任务也可选择撤销，展示实际输入轮次涉及的任务/词条/文件范围，执行前重新核验；重复选择及迟到回调受独立目标代际保护。

### S13 会话内历史阅读

- 新增 `history_navigation.py`：会话内更早/更新/最新分页、完整历史搜索及命中跳转；默认单行入口，按需展开搜索，旧页收到新内容时提示返回最新。
- 新增 `message_scroll.py`：集中管理底部跟随、滚动定位和销毁保护。
- 修改 `message_history_view.py`：移除独立历史对话框，保留规范历史合并与只读记录。
- 修改 `message_list_view.py`：历史页只呈现只读记录，保留当前任务卡锚点；实时区最多 100 控件，历史页最多 100 可见控件，返回最新释放历史页。
- 修改 `message_bubble.py`、`streaming_presenter.py`：整条消息复制，流式更新同步可复制正文。
- 修改 `chat_composition.py`：统一接入历史导航、确认上下文、结果绑定，并收紧外围布局间距。

### 回归与计划

新增 `test_task_design.py`、`test_composer_design.py`、`test_onboarding_design.py`、`test_skill_launch_design.py`、`test_history_navigation.py`、`test_change_review.py`、`test_completed_undo.py`、`tests/smart_assistant/test_operation_change_preview.py`、`tests/smart_assistant/test_request_task_control.py`、`tests/application/assistant_requests/test_result_review.py`。

更新既有 `test_attachment_composer.py`、`test_chat_bindings.py`、`test_confirmation_feedback.py`、`test_conversation_layout.py`、`test_conversation_reading.py`、`test_network_retry_recovery.py`、`test_request_lifecycle_panel.py`、`test_request_timeline_view.py`、`test_session_management_actions.py`、`test_window_minimum_size.py`：保留审批、身份、权限和恢复断言，按真实异步预览等待可执行状态，覆盖菜单选择、响应式布局和回看生命周期。

计划增加 S06～S13，索引仅更新本工作线条目。

## 验证证据

在现有 Python 3.12.12 / uv 环境执行，所有模型入口使用离线替身，Qt 使用 offscreen。

```powershell
$env:QT_QPA_PLATFORM='offscreen'
uv run --offline --no-sync python -m pytest tests/smart_assistant tests/ui/tools/smart_assistant tests/application/assistant_requests tests/application/assistant_context tests/application/sessions tests/persistence/test_assistant_transcript_store.py tests/persistence/test_session_request_storage.py tests/persistence/v2/test_assistant_session_migration.py tests/infra/test_assistant_prompt_cache.py tests/ui/characterization/test_chat_widget_contract.py tests/integration/bootstrap/test_assistant_request_wiring.py -q -m 'not llm'
uv run --offline --no-sync ruff check src tests
uv run --offline --no-sync ruff format --check src tests
git -c core.safecrlf=false diff --check
```

- 最终联合回归：1628 passed，退出码 0，251.51 秒。72 条警告来自既有 SWIG 与旧 TranslationEntry mutation 兼容入口。
- 全库 Ruff check 通过；1764 个文件 format --check 通过；Git 差异空白检查通过。

- 聚焦验证包括真实 runtime 暂停/恢复与请求执行 28 项、最终确认/结果 75 项、任务导航/过程 16 项，以及真实中文字体布局 8 项。
- 首轮完整联合检查 1614 passed / 1 failed：界面尚未初始化时关闭缺少 _input_actions 属性；修复后独立关闭契约通过，随后执行上述最终联合回归。
- 初始 UI 回归发现异步预览导致立即点击旧测试未执行、菜单测试阻塞、旧发送/尺寸断言不匹配；改为等待真实可执行状态、显式选择菜单并保留原语义断言。真实布局发现长输入撑高和中文字体最小宽度问题，均修正根因并补几何回归。
- 实际控件截图：设置 TB_ASSISTANT_CAPTURE_DIR 为本任务 assistant-design 输出目录，运行 test_conversation_layout.py::test_capture_conversation_layout，1 passed；桌面、480×620 窄屏、完成态和独立结果页均已目视检查。截图为明确离线样例，不代表用户真实项目执行记录。

## 兼容与限制

无需存储 schema 迁移。旧凭据没有保存的信息明确显示未知；AI 翻译尚未执行时不伪造拟译文。定位第一条选中记录，工作台标识冲突时拒绝错误定位；撤销使用实际输入轮次，涉及其他任务时明确列出范围。

新职责分别提取到独立模块。panel 和 RequestBinding 保持 500 行以内；既有 ChatWidget 仍超过 500 行，本轮仅做组合接线和生命周期小修，未增加业务控制职责。以后新增职责前仍须按提交/执行/呈现边界提取完整切片并补回归。

本轮八项无未完成项。未运行真实 LLM、远端账户操作、原生 Windows 人工交互、安装包发布或无关全库业务测试。临时基线及探针目录已清理，保留交付截图。
