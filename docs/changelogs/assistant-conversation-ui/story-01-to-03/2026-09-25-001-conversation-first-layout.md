# 对话式助手布局与消息内进度

日期：2026-09-25。Epic：assistant-conversation-ui；Story：S01～S03。
关联：[已确认方案](../../../../plans/assistant-conversation-ui/plan.md)。

## 改动及原因

用户确认本任务交互示意稿：对话为主，进度和恢复操作跟随任务，输入区精简。正式产品不包含示意稿的“预览状态”控件。

- 新增 `conversation_presentation.py`：项目名称和当前选区、空会话快捷入口、轻量状态刷新、输入区停止接线、按需打开任务/会话详情。读取现有内存投影，不在 UI 定时器中查询存储。
- 新增 `request_progress_view.py`：权威请求状态与事项完成数、可展开执行详情、按请求 ID 发出继续/暂停/取消/核对操作；撤销只在已有服务核验后显示。复用状态文案函数，避免主视图和管理详情矛盾。
- 新增 `panel_layout.py`：窄于 680px 自动收起会话侧栏，调整 splitter 实际宽度，宽屏恢复此前展开状态。
- 修改 `message_list_view.py`：保留历史消息 ID；以源消息 ID 将非持有进度控件放在对应轮次尾部；新消息到达后调整位置，清空时解除并隐藏附件，不重复释放 presenter 所有的卡片。
- 修改 `request_binding.py`：移除常驻顶部管理面板，提交消息保留 ID，加载会话先清理旧进度；选区快照支持真实 AppContext 的 selected_ids，与顶部展示一致。
- 修改 `request_list_view.py`：成为按需打开的管理详情，通过信号投影请求、待处理输入及撤销状态；移除独立停止按钮，保留既有控制信号契约。
- 修改 `input_view.py`：附件、更多、发送/停止；自动执行改为更多菜单内的持久化 QAction，快捷操作与技能完整可达；运行中仍可 Ctrl+Enter 发送补充；无附件时不留空行。
- 修改 `quick_actions.py`：窄宽度未展示操作进入“更多”，不再直接丢失入口。
- 修改 `panel_header.py`、`theme_support.py`：52px 紧凑标题，移除固定工作模式和多余状态控件，弱化外框和输入区重复边框，复用主题颜色。
- 修改 `panel.py`、`session_list_widget.py`、`task_monitor.py`：紧凑侧栏、无边框列表、响应式分栏；没有后台任务时不占底部空间，有任务时保留原折叠监控和控制。
- 修改 `chat_composition.py`、`chat_widget.py`：无边框消息区域和新 presenter 的创建/关闭接线。ChatWidget 原已超过 500 行，本次不增加业务职责，新展示职责独立在上面三个模块；后续新增功能仍不得堆入 facade。
- 新增 `test_conversation_layout.py`：8 个真实 Qt 用例，覆盖输入状态、消息 ID 定位、历史恢复、项目选区、窄屏、菜单操作、撤销条件、切换会话及可选截图。
- 修改 `test_chat_bindings.py`、`test_theme_migration.py`：更新已被本设计取代的外框/窄屏断言。
- 修改 `test_request_lifecycle_panel.py`：通过真实输入区按钮验证停止；摘要取消用例等待后台取消提交结果，替代与异步提交无因果关系的固定 30ms 等待。
- 新增方案及最小更新 `plans/INDEX.md`、`docs/changelogs/INDEX.md`。

## 验证

使用仓库既有 uv 环境，不改依赖或锁文件。沙箱拒绝访问 uv 缓存及 pytest 临时目录；使用 `--no-cache --no-sync`，经权限工具授权运行本地测试，数据仅在本任务专属目录。

最终命令（退出码均为 0）：

```powershell
uv --no-cache run --no-sync pytest tests/ui/tools/smart_assistant tests/ui/characterization/test_chat_widget_contract.py tests/smart_assistant tests/application/assistant_requests -q --tb=short --basetemp=.tmp-assistant-ui-20260925 -p no:cacheprovider
uv --no-cache run --no-sync ruff check src tests
uv --no-cache run --no-sync ruff format --check src tests
git diff --check
```

1,236 项测试通过；65 条弃用警告。格式检查覆盖 1,414 个文件。
新增布局及网络恢复聚焦复验 12 项通过；设置 `TB_ASSISTANT_CAPTURE_DIR` 后生成并查看 1024×760 桌面、480×620 窄窗口及完成状态的真实 Qt 截图，数据来自隔离测试示例。离屏测试显式加载本机微软雅黑字体以核验中文。

中途发现并修复停止按钮引用未打包图标、窄屏 splitter 空白和截图夹具缺少刷新。扩大回归曾有网络恢复时序失败，单独及最终整套复验均通过；摘要取消固定等待的测试竞态已改为等待权威状态。

## 兼容与限制

无数据格式迁移；确认、后台执行、取消保留结果及安全撤销合同不变。未做真实 LLM/远端服务调用，也未运行无关模块全量测试。历史没有源消息 ID 的请求放在对话末尾并显示目标，不按相同文本猜测归属。进度显示“事项”，不伪造词条翻译完成数。

既有 compaction.py、对应测试及其他 changelog 修改属于用户先前工作，本记录不归入、不覆盖。
实现范围内未完成项：无。
