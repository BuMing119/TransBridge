# 智能助手默认界面精简

日期：2026-10-16
Epic：assistant-ui-remediation
Story：S14～S17
关联：[计划](../../../../plans/assistant-ui-remediation/plan.md)

## 增量边界

用户认为上一轮界面过于繁琐，再次调用 bm-pilot，授权按讨论的简洁方向继续实现。本轮降低常驻信息和按钮密度，保留 S01～S13 已交付的附件、审批、任务控制、完整历史、结果与安全撤销能力。

开工时保存本轮相关文件基线，收尾逐文件比较，区分前两轮及其他未提交修改；自建基线和悬停测试诊断目录均已核对归属后清理。未修改既有增量记录，没有提交或推送。术语工作线、依赖锁和构建配置不属于本轮。

以下生产文件位于 `src/transbridge/ui/tools/smart_assistant/`，测试文件位于 `tests/ui/tools/smart_assistant/`。

## 逐文件变化

### S14 页首与入口

- 修改 `panel_header.py`：标题栏由 52 px 双行品牌信息收敛为 44 px 单行会话标题、模型和窗口操作，去掉重复品牌图标及副标题。
- 修改 `conversation_presentation.py`：项目/选区、活动任务提示及会话菜单同排显示；无活动或待处理任务时隐藏状态按钮，全部任务和结果保留在菜单。历史搜索从菜单及对话区域 Ctrl+F 打开；原始要求定位后收起任务面板，关闭时禁用快捷键。
- 修改 `theme_support.py`：增加共用 QUIET_BUTTON_STYLE，次级按钮默认无可见边框，悬停、选中及键盘焦点保留反馈。

### S15 任务按状态展开

- 修改 `request_progress_view.py`：正常任务默认只显示状态、细进度条和详情入口；完成态默认显示完成数量及结果入口。目标、冻结范围、步骤、完整后台进度、暂停/取消、原始要求和撤销范围移入详情。
- 同一文件将待确认、未知结果、失败及暂停的说明和必要操作放在折叠区域之外；查看待确认操作沿用 resume/restore，仍需在确认卡明确批准。普通刷新及完成转换尊重用户手动展开状态；仅摘要展示一项后台进度，其余完整信息在详情中保留。
- 详情/收起使用纯文字，避免中文字体缺失特殊箭头；所有任务身份、真实控制和结果回调保持原路径。

### S16 输入区

- 修改 `input_view.py`：编辑器下仅一行附件图标、执行模式、更多、停止和发送；附件名称仍按需显示。运行中空输入强调停止，有可发送内容时强调发送补充；保留附件解析阻塞、点击及键盘发送行为。
- 修改 `execution_mode_view.py`：按钮紧凑显示“先确认 / 自动执行”，菜单、提示及无障碍说明保留完整执行权限边界，状态仍来自原 QAction。
- 原 `composer_editor.py` 自适应逻辑可满足新布局，未修改；中文字体下长输入、附件及同时显示停止/发送补充不会强制扩宽 480 px 窗口。

### S17 历史与消息操作

- 修改 `history_navigation.py`：短会话默认隐藏整条导航，仅确实有更早消息时显示入口；搜索按需展开，可通过关闭/Escape 收起，保留阅读页及恢复原焦点，错误提示不会被隐藏。搜索操作用带中文提示的 30 px 图标，查询框最小宽度 160 px。
- 修改 `message_list_view.py`：创建导航后立即应用主题，修复首次打开搜索时仍显示默认按钮样式的问题。
- 修改 `message_bubble.py`：移除每条助手重复名称，将复制、定位提示和提交范围移至正文下方；头像与正文对齐。复制仅悬停或键盘聚焦时绘制，保留固定布局、Tab/空格操作、流式全文复制和销毁时焦点连接释放。
- 修改 `submission_scope.py`：新增向后兼容的 compact 参数，消息中的提交范围采用轻量入口，提示完整摘要，打开后仍是原始冻结快照；任务详情保留完整摘要。

### 回归与工作流

- 新增 `test_conversation_chrome.py`：覆盖任务菜单、菜单/Ctrl+F 搜索、Escape 恢复焦点、窄屏待处理提示和紧凑范围键盘查看。
- 新增 `test_progress_disclosure.py`：覆盖默认密度、详情内控制、手动展开保持、确认/未知/失败/暂停行动及多后台任务摘要。
- 新增 `test_message_chrome.py`：覆盖真实 hover/Tab/空格、复制完整文本、布局稳定、正文与头像对齐及销毁。测试夹具先清理前序待销毁窗口，再确认新窗口曝光，避免 offscreen 鼠标事件未送达。
- 修改 `test_history_navigation.py`：覆盖默认隐藏、搜索开关/焦点、稳定阅读及实际 480 px 查询宽度和图标操作。
- 修改 `test_composer_design.py`：覆盖工具栏同排、模式真实状态、停止/发送强调和中文文字可用宽度。
- 修改 `test_task_design.py`：保留原结果/失败回调，增加真实 RequestBinding 暂停→持久化→继续→完成链路。
- 修改 `test_chat_bindings.py`：标题栏高度断言同步为 44 px，保留窗口语义检查。
- 修改 `test_conversation_layout.py`：移除与行为无关的停止图标断言，保留点击/键盘控制验证；截图入口增加完成详情、失败及搜索展开状态。
- 更新 `plans/assistant-ui-remediation/plan.md` 增加 S14～S17 的范围、验收和完成证据；两个索引只更新本工作线条目。

## 验证证据

使用仓库已有 Python 3.12.12 / uv 环境，Qt 为 offscreen，不调用真实模型。

```powershell
$env:QT_QPA_PLATFORM='offscreen'
uv run --offline --no-sync python -m pytest tests/smart_assistant tests/ui/tools/smart_assistant tests/application/assistant_requests tests/application/assistant_context tests/application/sessions tests/persistence/test_assistant_transcript_store.py tests/persistence/test_session_request_storage.py tests/persistence/v2/test_assistant_session_migration.py tests/infra/test_assistant_prompt_cache.py tests/ui/characterization/test_chat_widget_contract.py tests/integration/bootstrap/test_assistant_request_wiring.py -q -m 'not llm'
uv run --offline --no-sync python -m pytest tests/ui/tools/smart_assistant/test_history_navigation.py tests/ui/tools/smart_assistant/test_message_chrome.py -q --tb=short
uv run --offline --no-sync python -m pytest tests/ui/tools/smart_assistant tests/ui/characterization/test_chat_widget_contract.py -q -m 'not llm' --tb=short
uv run --offline --no-sync ruff check src tests
uv run --offline --no-sync ruff format --check src tests
git -c core.safecrlf=false diff --check
```

- 相关联合检查：1654 passed / 1 failed，210.73 秒。唯一失败来自新增 hover 测试：前序顶层窗口等待 DeferredDelete，offscreen 未派发 Enter/Leave。诊断确认鼠标位置改变但目标未收到事件，修正测试夹具释放/曝光顺序，未改生产逻辑或删减交互断言。
- 原历史+消息组合连续两次 16 passed；随后最后全部助手 UI 与关闭契约 **300 passed**，151.83 秒，包含最后新增的窄屏搜索回归。3 条警告为既有 SWIG 弃用提示；更广检查另有既有 TranslationEntry 兼容入口弃用警告。
- 全库 Ruff check、1767 文件 format --check 和差异空白检查通过。
- 聚焦过程还验证了真实暂停→继续、审批恢复、结果/撤销、主题往返及 480×400/480×620 中文字体布局。
- 初始集成检查曾发现发送图标使用未支持的 semantic 值，已改为现有 navigation 语义；后续检查均通过。首轮默认沙箱无法写入 uv 解释器缓存，后续经执行权限使用现有环境完成离线验证，没有安装依赖。

最终设置 `TB_ASSISTANT_CAPTURE_DIR` 为本任务 `assistant-simplified` 交付目录，运行 `test_conversation_layout.py::test_capture_conversation_layout`：1 passed。真实 Qt 桌面、480×620 窄屏、完成、展开详情、失败和搜索状态均已目视复核；截图是离线样例，不代表用户实际项目执行记录。

## 兼容与限制

无需存储 schema 迁移，没有修改执行权限或任务/撤销契约。本轮涉及的生产模块均小于 500 行，没有向既有超大 facade 增加职责。

本轮无未完成项。未运行真实 LLM、远端账户、原生 Windows 人工交互、屏幕阅读器/高 DPI 人工验收、安装包发布及无关全库业务测试。临时文件已清理，交付截图保留在本任务可视化目录。
