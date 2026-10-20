# 会话工程绑定、分组导航与系统默认窗口

日期：2026-10-20（工作始于 2026-10-19）  
Epic：assistant-ui-remediation  
Story：S24～S26  
关联：[计划](../../../../plans/assistant-ui-remediation/plan.md)、[FR13 / FR19](../../../requirements.md)、[ADR-018](../../../adr/018-project-session-persistence-v2.md)、[完整 QA](../../../test-reports/assistant-project-conversations-qa-2026-10-19.md)

## 结果与边界

工程内新建对话固定绑定工程与翻译版本；一个工程可创建多个对话。侧栏按工程分组，其他工程的会话仍可阅读。发送、继续、重试、确认或撤销前统一校验，用户确认后沿正常工程打开流程安装精确版本，再恢复原操作。取消、打开失败和过期回调不能发送或混用工作台上下文。旧未绑定历史不猜测归属；合法缺 owner 的旧历史保持可读。

窗口按用户最后一张截图采用系统默认标题栏和外框。会话/模型信息位于内部页首，侧栏通过底色、当前工程和“仅浏览”文字区分状态；不再绘制深色外框或裁剪窗口形状。会话目录和工程标签在后台读取，冷缓存与文件变化不会让完整历史校验阻塞 Qt。

本记录以本会话已执行的修改、当前 Git 差异及未跟踪文件为依据；“新增/修改”对应本任务引入的职责。共享文件仅归属下列符号和行为。既有版本调整 `pyproject.toml`/`uv.lock`，以及其他工作线的工程创建、ParaTranz、保存恢复和输入焦点修改不计入本记录；不重复记录早前 S01～S23。没有提交、推送或发布。

## 应用与工程打开

- 修改 `src/transbridge/application/sessions/gui_facade.py`：创建时保存 Project/Variant 绑定；`read_conversation()` 校验 owner 后提供只读历史与归档请求；`list_sessions()` 从会话记录取得权威归属，不把目录字段当作授权。读取失败保留诊断，不迁移、隔离或激活被预览会话。
- 新增 `src/transbridge/application/sessions/scope_cache.py`：`SessionScopeCache` 按文件签名缓存已验证的归属；源记录变化、缺失或不可签名时重新校验。只优化目录展示。
- 新增 `src/transbridge/application/sessions/legacy_owner.py`：共享 `session_snapshot_for_context()`，仅对真正缺 owner 的合法旧记录作内存兼容归一化，保留原工程/版本并标记降级；明确 owner 不受旧诊断标记影响。
- 修改 `src/transbridge/persistence/session_lifecycle.py`：`V2SessionSnapshotRepository.load()` 复用上述解码规则，避免恢复与预览对同一旧记录产生不同结论。
- 修改 `src/transbridge/persistence/v2/session_catalog.py`：`SessionCatalogEntry` 增加可选绑定字段并校验 Variant 必须伴随 Project，兼容读取缺字段的旧目录。
- 修改 `src/transbridge/persistence/current_project.py`：`prepare_path()` 接收明确目标版本，`prepare_identity()` 验证指定工程与版本；不回退到工程最近活动版本。
- 新增 `src/transbridge/ui/shell/assistant_project_navigation.py`：提供只读名称查询和精确工程打开适配，繁忙/关闭/服务缺失时明确拒绝；不新增 AI 切换本地工程工具。
- 修改 `src/transbridge/ui/shell/assistant_navigation.py`：注入工程标签查询与执行前切换端口。
- 修改 `src/transbridge/ui/coordinators/project_coordinator.py`：`confirm_project_switch()` 复用保存/放弃/取消语义，`open_project_identity()` 在真实视图安装且身份吻合后交付一次完成回调。
- 修改 `src/transbridge/ui/coordinators/project_open_operation.py`：显式传递目标请求上下文至激活边界，继续使用既有后台准备和工作台安装流程。

## 会话浏览与执行门

以下文件均位于 `src/transbridge/ui/tools/smart_assistant/`：

- 新增 `panel_sessions.py`：从面板提取会话创建、保存、切换、重命名、删除与投影；后台读取单条目标历史，按 generation 丢弃迟到结果；预览与后端活动会话分离，各会话保留未发送草稿。启动等待有效目录和工作台就绪，不能把错误当作空目录而误建会话。本记录不把并行工作线补入的初次输入/搜索焦点保留逻辑重复归为新增成果。
- 新增 `session_directory.py`：单 worker 读取目录和工程标签，Qt timer 安装最新完整快照；合并刷新请求，失败保留旧列表，关闭丢弃回调。缓存缺失时说明仍在加载，不误称工程被删除。
- 新增 `session_workspace.py`：统一比较实际工作台与会话绑定；确认后切换精确工程/版本，再复核目标、generation 和就绪状态并恢复原动作。工作台变化暂停执行，匹配范围的预览也只在显式动作后激活。
- 新增 `session_execution_access.py`：为不同 UI 入口提供同一就绪检查与确认调用，不重复实现切换流程。
- 新增 `request_session_loading.py`：分离正式恢复与只读请求展示；预览不恢复 scheduler、控制调用或写回历史。
- 修改 `session_binding.py`：预览只恢复展示，使用空控制器快照，避免旧执行状态跟随历史视图生效。
- 修改 `request_binding.py`：委托加载模块；唤醒、选择轮次、构造模型输入与控制操作均校验作用域；排队回调再次验证。暂停和取消仍按原任务身份处理。
- 修改 `request_input_binding.py`：发送先通过统一边界，再采集真实目标选区/上下文，拒绝接纳时保留输入。
- 修改 `request_context_preparation.py`：后台上下文准备与交付前验证当前执行资格，阻止切工程后的旧准备继续执行。
- 修改 `request_turn_selection.py`：轮次选择和交付时校验工程/版本及会话身份。
- 修改 `request_management_binding.py`：重试输入、失败重试、重新处理和上下文重试统一走执行门；对话框返回与后台提交重验范围。
- 修改 `request_confirmation_binding.py`：确认动作加入统一执行前校验。
- 修改 `request_undo_binding.py`：撤销入口及异步提交前重验当前会话范围，保留原执行证据约束。
- 修改 `routing_recovery_binding.py`：澄清/重新规划在入口、弹窗返回和提交前重验身份。
- 修改 `chat_widget.py`：legacy 发送、重试与清空入口接入统一门；仅作既有职责内接线。
- 修改 `confirmation_view.py`：确认被切换取消/拒绝时恢复当前卡片可操作状态；已销毁、过期或属于旧代际的卡片不再回调。
- 修改 `plan_card.py`：区分过期、执行中和等待确认，支持切换取消后恢复当前计划卡。
- 修改 `tool_card.py`：单步及批量卡同样支持恢复等待确认，已过期卡不能重新启用。

## 侧栏与窗口

以下文件均位于 `src/transbridge/ui/tools/smart_assistant/`：

- 修改 `panel.py`：接入独立会话/工作台协调器及状态提示；明确系统 Window/标题栏/系统菜单/窗口按钮标志。内部 header 放到内容布局，关闭隐藏复用、释放资源及任务栏身份沿用原契约。
- 修改 `panel_header.py`：只显示会话与模型；移除自定义窗口按钮和鼠标拖动逻辑，默认会话名显示“新对话”。
- 修改 `window_presentation.py`：移除不再使用的自定义最小化入口，保留服务就绪刷新。
- 修改 `session_list_widget.py`：工程分组、当前工作台身份、搜索、展开状态与焦点保留；窄侧栏和收起入口保持可达。
- 新增 `session_groups.py`：工程组标题、展开/收起、数量和当前工程标识。
- 新增 `session_row.py`：两行会话/版本信息、省略与完整提示、完整选中底色及“仅浏览”标识；辅助标签保持父子关系，不生成独立窗口。
- 新增 `session_scope_notice.py`：按真实状态区分其他工程/版本、读取/切换中、工作台未就绪及未绑定；已匹配时不重复提示切换。
- 修改 `theme_support.py`：去掉多层外描边，次级按钮弱化边框；使用主题颜色明确刷新导航选中态、侧栏和提示底色，覆盖浅/深主题实际像素表现。
- 撤销本任务早前暂建的 `window_frame.py`：删除自绘描边和 mask 实现，没有保留多余 resize grips。该文件未作为最终生产文件交付，不能解读为删除仓库既有窗口公共 API。

## 测试与文档变化

- 新增 `tests/application/sessions/test_gui_session_scope.py`：绑定持久化、旧目录、跨工程只读预览、归档 owner 先验校验、损坏记录及多格式缺 owner 兼容。
- 新增 `tests/application/sessions/test_scope_cache.py`：冷读取、签名变化和缺失记录不得沿用旧归属。
- 修改 `tests/application/sessions/test_gui_session_management.py`：测试服务构建支持注入文件系统，以验证只读路径无写入。
- 修改 `tests/application/sessions/test_persistence_adapter.py`：显式 owner 不能被陈旧的缺 owner 诊断覆盖。
- 新增 `tests/ui/test_assistant_project_navigation.py`：精准版本打开、缺失/繁忙/失败、dirty 决策及安装后回调。
- 新增 `tests/ui/tools/smart_assistant/test_request_session_scope.py`：只读展示、上下文收集时机和会话切换执行边界。
- 新增 `tests/ui/tools/smart_assistant/test_execution_entrypoint_scope.py`：发送/重试/继续/确认/撤销与排队后的统一校验。
- 新增 `tests/ui/tools/smart_assistant/test_session_workspace_navigation.py`：取消、失败、同工程不同版本、迟到回调、旧历史和工作台就绪。
- 新增 `tests/ui/tools/smart_assistant/test_session_directory.py`：14 项覆盖实际冷/文件变化读取在线程中执行、阻塞期间 QTimer 响应、最新代际、关闭、错误保留、初次恢复与新选择保护。
- 新增 `tests/ui/tools/smart_assistant/test_session_project_groups.py`：分组、搜索、工程和浏览会话身份分离。
- 新增 `tests/ui/tools/smart_assistant/test_session_scope_notice.py`：状态文案、真实身份与主题像素。
- 新增 `tests/ui/tools/smart_assistant/test_session_sidebar_layout.py`：180/200/260px、10/14pt 中文字体、长名称与两种主题的几何/像素回归。
- 新增并按最终窗口要求重写 `tests/ui/tools/smart_assistant/test_window_frame.py`：原生 flags、内部页首、无重复按钮/遮罩、主题与草稿保留。
- 新增 `tests/ui/tools/smart_assistant/test_window_native_interaction.py`：Windows 系统窗口样式、八方向命中、标题命中、系统命令、最小尺寸及关闭复用；离屏环境显式跳过。
- 修改 `tests/ui/tools/smart_assistant/test_chat_bindings.py`：面板由独立会话协调器接线、系统标题栏与内部页首的契约。
- 修改 `tests/ui/tools/smart_assistant/test_composer_design.py`：布局断言指向真实内部标题/模型，移除对已删除窗口按钮的依赖。
- 修改 `tests/ui/tools/smart_assistant/test_keyboard_navigation.py`：等待实际会话绑定；模型启动回调被替换后让真实控制器进入 THINKING，主动刷新验证停止按钮，再验证 Tab/菜单/发送。修复测试只设置 view running、被正常周期刷新重置的竞态，没有改产品行为。
- 修改 `tests/ui/tools/smart_assistant/test_submission_scope_display.py`：测试先创建绑定会话，再验证后续工作台变化不能改变已提交的范围说明。
- 修改 `tests/ui/tools/smart_assistant/test_visual_hierarchy.py`：导航选中态、实际填色和内部页首层次，取消自绘窗口遮罩要求。
- 修改 `tests/ui/tools/smart_assistant/test_window_minimum_size.py`：移除已删除窗口按钮断言，保留真实内容在最小尺寸可达。
- 修改 `docs/requirements.md`：FR13.1～13.8、FR19.5/19.6 对齐固定绑定、只读浏览、统一执行门、旧历史和最终系统默认外框。
- 修改 `docs/adr/018-project-session-persistence-v2.md`：补录浏览/激活合同、后台展示缓存、精确打开、旧 owner 兼容和回调边界，不改变原接受日期。
- 修改 `plans/assistant-ui-remediation/plan.md`：补录 S24～S26、文件落点、验收、兼容及证据；不追溯声称开发前已有该计划。
- 新增 `docs/test-reports/assistant-project-conversations-qa-2026-10-19.md`：记录实证发现、修复、历次测试及最终验收边界。
- 最小更新 `plans/INDEX.md`、`docs/INDEX.md`、`docs/changelogs/INDEX.md` 中直接相关条目，保留其他工作线内容。

## 验证

- 后端/会话/精准工程打开组合：170 passed，退出码 0。
- Windows 原生窗口与窗口契约：15 passed，退出码 0；实际为 Windows、150% DPI，仅作用于测试自建窗口。
- 助手 UI/关闭完整组合最终一轮：505 passed、1 failed、12 skipped，退出码 1。键盘测试已修复并通过；剩余一例为快速追加输入后等待第三请求终态的 6 秒超时。三条输入和两批路由均已保存，第三轮获选时已耗用约 5.66 秒；具体延迟来源未完全解释，不能声称整组全绿。
- 上述超时与相邻快速输入用例两轮定向复验均为 2 passed。后续有界诊断的 16 份落盘状态均为三个请求 completed，因终端汇总截断不计为精确 pytest 通过数；未复现可确认的工程作用域错误或请求丢失。
- 键盘修复后全部前序 17 个文件：186 passed。目录及相关范围用例组合 115 passed，独立作用域复审组合 89 passed；这些与最终组合重叠，不累计。
- `uv run --no-sync --no-cache ruff check src tests`、`uv run --no-sync --no-cache ruff format --check src tests` 通过，最后一次格式检查覆盖 1948 个文件；`git -c core.safecrlf=false diff --check` 通过。

精确 pytest 命令、初次受限目录权限失败、键盘测试竞态和最终复验见[完整 QA](../../../test-reports/assistant-project-conversations-qa-2026-10-19.md)。前序 174、164 项和并行复审/聚焦测试与最终组合存在重叠，不相加作为覆盖量。

## 兼容与未覆盖范围

不升级 Session schema、不批量重绑旧历史。新版目录可读取缺绑定字段的旧格式；旧版目录解码器不识别新增可选字段，降级须先备份并转换目录，不能直接宣称旧版可读取新版目录。本次未执行降级或改写用户历史。

ChatWidget 549 行/36 方法已作职责复核，本次仅作统一执行门接线；新职责独立为目录、浏览、工作台切换与展示模块。后续新增责任须沿输入或渲染边界拆分。

不调用真实 LLM/ParaTranz，不发布安装包。Windows 150% DPI 验证只操作自建测试窗口，不等同于人工鼠标拖动、多显示器 DPI、触控或屏幕阅读器验收。测试临时目录在核对归属后清理，既有历史增量保持原样。

遗留：完整 UI 组合中一次未稳定复现的请求终态等待超时，保留为验证限制；后续稳定复现时应独立诊断第三轮准备/GUI 交付延迟，本轮不猜测修改产品逻辑或放宽超时。
