# 智能助手对话式界面

状态：已实现并完成本地 QA（2026-09-25）。用户确认本任务交互示意稿的视觉方向。

## 目标与边界

会话侧栏、紧凑标题、项目/选区上下文、消息与随消息展开的请求进度、精简输入区。
不加入示意稿的状态选择器，不修改请求协议、权限、存储格式或安全撤销合同。
沿用 ADR-040、ADR-043；完成数标注为事项，只有后台任务提供词条进度时才显示词条数量。

## 实施计划

- [x] S01：压缩标题与侧栏，新增实时项目/选区提示、空会话引导；输入区保留附件、更多、发送/停止，快捷操作始终可达。
- [x] S02：请求进度按 source_message_ids 放入消息流；详情保留原请求管理/过程入口，确认和重试沿用现有消息控件；撤销跟随经过核验的请求。
- [x] S03：真实 Qt 回归、离屏视觉检查、Ruff、差异复核及增量记录。

## 文件落点与验收

S01：panel_header、theme_support、session_list_widget、input_view、quick_actions；新增 conversation_presentation 承担上下文/状态展示，避免继续向超过 500 行的 ChatWidget 添加职责。
S02：新增 request_progress_view/presenter，复用 RequestListView 作为折叠管理详情；MessageListView 提供非持有的锚定附件接口，切换/清空会话时不保留旧卡片；RequestViewRefresh 保持后台读取。
S03：tests/ui/tools/smart_assistant 回归与新增布局测试，characterization 和相关 assistant_requests 验证。

实际结果：1,236 项相关测试通过，Ruff check/format --check 与 git diff --check 通过；桌面和窄屏 Qt 截图已核对。真实 LLM 与远端服务未调用。[增量记录](../../docs/changelogs/assistant-conversation-ui/story-01-to-03/2026-09-25-001-conversation-first-layout.md)包含命令、文件清单和验证边界。

验收：没有独立常驻的请求管理区；停止在输入框旁且按真实运行状态启用；普通文本可在运行中补充；未知项目不伪造；会话切换无串卡；同文不同消息按 ID 定位；窄窗口所有操作仍可访问；主题切换保留状态。

## 约束与风险

进度卡仅投影权威请求数据，不推断副作用成功或可撤销性。来源消息不在当前可见历史中时隐藏消息内卡片，任务仍可从“任务与会话详情”访问；清空和历史截断均遵循此规则。后台任务保留可展开的真实控制入口，无任务时隐藏。只做局部接线的 ChatWidget/panel 不新增业务职责；独立展示类承担新增功能。
无需数据迁移。撤回 UI 改动不影响已有会话和项目格式。

## 2026-10-01 修复验证

- [x] 修复来源消息缺失时的卡片定位，以及空白页主题刷新。
- [x] 添加清空后刷新、100 条历史截断、来源恢复和主题往返的回归测试并验证：定向 19 项、联合 1240 项通过，Ruff check/format 与差异检查通过。
- [x] 复核差异并记录[本次增量](../../docs/changelogs/assistant-conversation-ui/story-01-to-03/2026-10-01-002-progress-and-theme-fixes.md)。
