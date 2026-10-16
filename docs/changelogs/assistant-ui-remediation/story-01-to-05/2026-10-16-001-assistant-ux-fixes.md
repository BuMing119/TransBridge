# 智能助手附件、确认反馈与会话阅读修复

日期：2026-10-16  
Epic：assistant-ui-remediation；Story：S01～S05  
关联：[实施方案](../../../../plans/assistant-ui-remediation/plan.md)

## 用户可见结果

附件随每次输入保存完整提取文本快照，支持预览、移除与清空；运行中同时保留发送和停止。确认前可检查操作目标及完整参数，执行后原卡片显示真实结果。任务显示提交时保存的项目、版本及选区；向上阅读不再被流式回复拉回底部，超过 100 条消息可分页回看。会话自动命名、隐藏搜索清理、键盘操作、模型标题刷新和紧凑窗口均补齐。

超出模型上下文容量的整批输入明确拒绝并保留历史，缩小材料后可重新发送；普通临时错误仍可重试。拒绝提交受原租约保护，旧窗口不能拒绝另一窗口重新认领的输入。窗口直接销毁时，滚动与附件排队回调由 Qt 接收者生命周期清理。

## 逐文件变化

以下路径均相对仓库根目录；未标“新增”的条目为修改。

### S01：附件、补充输入及失败恢复

- 新增 `src/transbridge/application/assistant_requests/attachments.py`：完整材料信封、可信用户正文与非指令资料的区分、展示摘要、数量及文本上限；显式空列表表示后续输入未附带材料。
- 新增 `src/transbridge/application/assistant_requests/input_acceptance.py`：从服务提取输入接纳事务；原始输入/span 不变，完整附件仅保存到规范 transcript，幂等摘要包含材料。
- `src/transbridge/application/assistant_requests/service.py`：委派输入接纳并提供原始输入读取入口，避免 UI 从 ingress 猜测附件正文。
- `src/transbridge/application/assistant_requests/turns.py`：新增 `reject_routing_input`，持有会话与调度租约锁时记录预算拒绝，不丢失原文且不阻挡后续输入。
- `src/transbridge/application/assistant_requests/journal.py`：增加 `routing.rejected` 的事件名称及分类。
- `src/transbridge/smart_assistant/context_builder.py`：删除把附件文件名冒充可读取材料的 system 上下文段落。
- `src/transbridge/smart_assistant/request_router.py`：当前输入携带完整材料，附件规则仅在确有材料时加入；无附件提示保持原样，保留既有离线回放摘要校验。
- `src/transbridge/smart_assistant/routing_context.py`：近期历史仅展示附件名称摘要，避免已移除材料重新混入当前路由输入。
- `src/transbridge/smart_assistant/file_parser/text_parser.py`：CSV 提取文本保留表头，防止术语表列含义丢失。
- 新增 `src/transbridge/ui/tools/smart_assistant/upload_binding.py`：后台解析、按会话保留窗口内草稿、提交快照、过期回调拦截、清空及关闭；交付使用 QObject slot 防止窗口销毁后访问控件。
- 新增 `src/transbridge/ui/tools/smart_assistant/attachment_dialog.py`：完整提取文本预览、添加/移除/清空，并说明历史保留及退出后的草稿边界。
- `src/transbridge/ui/tools/smart_assistant/input_view.py`：独立发送与停止按钮、停止图标及主题、解析期间发送状态、输入区域的最低布局高度；上传职责交给 UploadBinding。
- `src/transbridge/ui/tools/smart_assistant/chat_widget.py`：接入提交快照、会话隔离与关闭清理；输入接纳后才清理编辑器，Ctrl+Enter 与数字键盘 Enter 一致。
- `src/transbridge/ui/tools/smart_assistant/request_context_status.py`：预算错误按类型拒绝当前路由批次；拒绝持久化后再释放租约，说明本批未发送及重新提交方法。

### S02：确认预览与实际执行结果

- 新增 `src/transbridge/ui/tools/smart_assistant/operation_preview.py`：可读工具名、明确目标/范围摘要及可展开的完整只读参数。
- `src/transbridge/ui/tools/smart_assistant/tool_card.py`：单操作/批量卡接入预览，展示成功、失败、取消、任务启动及结果待核对；忽略与失效状态明确。
- `src/transbridge/ui/tools/smart_assistant/plan_card.py`：计划步骤预览、逐步进度、终态、不完整结果和停止跟进提示。
- `src/transbridge/ui/tools/smart_assistant/confirmation_view.py`：按原步骤身份将结果交付原卡，检查代际及参数，防止重复工具名和迟到回调串卡。
- `src/transbridge/ui/tools/smart_assistant/plan_execution_binding.py`：引擎/代际绑定卡片反馈，转发开始、进度、结束、异常并清理回调映射。
- `src/transbridge/ui/tools/smart_assistant/react_execution_binding.py`：后台副本映射到原确认步骤，兼容乱序/部分结果；缺失和线程异常显示未知结果。
- `src/transbridge/smart_assistant/tool_execution_handler.py`：新增结果通知及原步骤来源；使用 dataclass replace 保留 error_code 等结果字段。

### S03：提交范围与任务文案

- 新增 `src/transbridge/ui/tools/smart_assistant/submission_scope.py`：从保存的输入投影范围及详情，匹配执行层“第一个来源输入”的语义；旧数据明确显示未知。
- `src/transbridge/ui/tools/smart_assistant/request_binding.py`：提交保存可选项目名称，接入附件正文、消息 ID、范围投影及刷新信号。
- `src/transbridge/ui/tools/smart_assistant/request_view_refresh.py`：后台加载消息/请求范围，避免 UI 使用当前工作台选区替代历史范围。
- `src/transbridge/ui/tools/smart_assistant/request_list_view.py`：选中任务显示保存范围，归属操作改为“选择补充要求所属的任务”“移动未执行事项”。
- `src/transbridge/ui/tools/smart_assistant/request_progress_view.py`：任务卡接入范围摘要与只读详情。
- `src/transbridge/ui/tools/smart_assistant/request_management_binding.py`：同步输入从规范 transcript 读取正文，保留消息 ID 和范围；调整操作文案。
- `src/transbridge/ui/tools/smart_assistant/conversation_presentation.py`：待处理补充要求提示使用直接可理解的任务文案。

### S04：阅读、历史及稳定消息身份

- 新增 `src/transbridge/ui/tools/smart_assistant/message_history_view.py`：每页 30 条的只读历史；按消息 ID 使用规范正文合并本地提示，过滤内部路由文本但保留已提交直接回复和完整回答。
- `src/transbridge/ui/tools/smart_assistant/message_list_view.py`：保留最多 100 个主流控件，轻量归档可读记录；向上阅读保持位置、底部跟随和显式回到底部；QObject 拥有定时器及滚动 slot，清空同时清理历史/范围。
- `src/transbridge/ui/tools/smart_assistant/message_bubble.py`：显示输入范围，长文本仍可选择复制。
- `src/transbridge/ui/tools/smart_assistant/streaming_presenter.py`：流式纯文本可选择。
- `src/transbridge/smart_assistant/conversation_manager.py`：`add_assistant_turn` 返回规范消息 ID，既有忽略返回值调用保持兼容。
- `src/transbridge/smart_assistant/conversation_orchestrator.py`：完成气泡关联规范 ID；删除旧附件 system 回调入口。
- `src/transbridge/ui/tools/smart_assistant/request_control_binding.py`：已提交直接回复的气泡沿用持久消息 ID，避免历史重复和旧正文覆盖。
- `src/transbridge/ui/tools/smart_assistant/chat_composition.py`：统一接入只读历史入口、底部跟随、工具结果通知及附件显示摘要。

### S05：会话和窗口

- `src/transbridge/application/sessions/gui_facade.py`：默认会话首次保存按用户正文命名，解析附件信封且保留手工名称。
- `src/transbridge/ui/tools/smart_assistant/session_list_widget.py`：收起搜索和新建会话清除过滤，重命名保留完整名称；键盘导航、重命名、删除确认和更多菜单可达。
- 新增 `src/transbridge/ui/tools/smart_assistant/window_presentation.py`：显示/激活窗口时更新公开模型名称并接管最小化。
- `src/transbridge/ui/tools/smart_assistant/panel.py`：接入窗口展示；最低边界 480×400、聊天区域不可折叠，移除旧 Dock 忽略高度策略，让完整输入区域自然传递最低高度；文件已低于 500 行。

### 回归与文档

- 新增 `tests/application/assistant_requests/test_user_attachments.py`：规范保存、首次/后续材料、移除、预算、幂等、权限、重启、批次拒绝及旧租约。
- 新增 `tests/smart_assistant/test_routing_material_policy.py`：当前/历史/空附件规则；无附件保持原提示。
- 新增 `tests/ui/tools/smart_assistant/test_attachment_composer.py`：真实离线 provider 输入、管理/切换、CSV 表头、停止与发送、预算恢复、草稿保护及父窗口销毁。
- 新增 `tests/ui/tools/smart_assistant/test_confirmation_feedback.py`：实际执行回原卡、失败/取消/未知、部分与乱序结果、参数预览及旧代际。
- 新增 `tests/ui/tools/smart_assistant/test_conversation_reading.py`：滚动阅读、135 条历史、分页/清空、长文本复制、规范历史覆盖、路由过滤与 Qt 销毁。
- 新增 `tests/ui/tools/smart_assistant/test_submission_scope_display.py`：旧数据、原始来源范围、换选区及恢复后范围保持。
- 新增 `tests/ui/tools/smart_assistant/test_session_navigation.py`：搜索收起、键盘操作、长标题及模型刷新。
- 新增 `tests/ui/tools/smart_assistant/test_window_minimum_size.py`：大量历史、范围卡、待处理提示、附件、双按钮、展开任务与极端分隔条；逐级检查祖先控件无裁切。
- `tests/application/sessions/test_gui_session_management.py`：首次正文自动命名、附件信封与手工名称保持。
- `tests/smart_assistant/test_context_builder.py`：不再声称附件文件名等同可读正文。
- `tests/smart_assistant/test_conversation_manager.py`：返回规范消息 ID。
- `tests/smart_assistant/test_conversation_orchestrator_lifecycle.py`：真实/旧 fake 气泡身份及迟到 completion 保护。
- `tests/ui/tools/smart_assistant/test_chat_bindings.py`：更新尺寸与容器高度契约。
- `tests/ui/tools/smart_assistant/test_confirmation_lifecycle.py`：终态改为明确待核对，替换旧“永久执行中”断言。
- `tests/ui/tools/smart_assistant/test_conversation_layout.py`：独立停止/发送契约，保留可选实际控件截图验证。
- `tests/ui/tools/smart_assistant/test_request_lifecycle_panel.py`：停止入口迁移、直接回复及 coverage 回答的稳定消息 ID。
- `tests/ui/tools/smart_assistant/test_request_view_refresh.py`：支持范围投影回调。
- `tests/ui/tools/smart_assistant/test_session_management_actions.py`：隐藏搜索清除及新建后可见性。
- 新增 `plans/assistant-ui-remediation/plan.md`；最小更新 `plans/INDEX.md` 与 `docs/changelogs/INDEX.md` 的本工作线条目。

## 验证证据

在现有 Python 3.12.12 / uv 环境执行，Qt 使用 offscreen，全部 provider 为离线测试替身。

```powershell
$env:QT_QPA_PLATFORM='offscreen'
uv run --no-sync pytest tests/smart_assistant tests/ui/tools/smart_assistant tests/application/assistant_requests tests/application/assistant_context tests/application/sessions tests/persistence/test_assistant_transcript_store.py tests/persistence/test_session_request_storage.py tests/persistence/v2/test_assistant_session_migration.py tests/infra/test_assistant_prompt_cache.py tests/ui/characterization/test_chat_widget_contract.py tests/integration/bootstrap/test_assistant_request_wiring.py -q -m 'not llm'
uv run --no-sync ruff check src tests
uv run --no-sync ruff format --check src tests
git -c core.safecrlf=false diff --check
```

- 最终联合回归：1566 passed，退出码 0，62.86 秒。72 条警告来自既有 SWIG 和旧 TranslationEntry mutation 兼容入口。
- Ruff check 通过；1739 个文件 format --check 通过；差异空白检查通过。
- 首轮联合回归 1532 passed / 26 failed；失败均为新增附件 system 文案导致原路由回放摘要失配。修为按需加入材料协议后，36 项评估与最终整组通过；未修改 capture、供应商响应或摘要校验。
- 生命周期回归先验证旧实现直接销毁窗口会触发已删除 QListWidget，再验证修复后通过；滚动延迟回调与主题/窗口组合也通过。
- 实际控件截图命令：设置 `TB_ASSISTANT_CAPTURE_DIR` 为本任务可写可视化目录后，执行 `uv run --no-sync pytest tests/ui/tools/smart_assistant/test_conversation_layout.py::test_capture_conversation_layout -q`；1 passed，桌面/窄屏图已检查。最小窗口裁切由最终联合回归中的两项几何测试验证。

## 兼容、职责及遗留

无需存储 schema 迁移；旧纯文本输入保持原格式，旧范围资料缺失时不猜测。每次最多 10 个附件、提取文本合计 200000 字；超限拒绝而不截断。已发送材料保存到原始历史；未发送附件仅在当前窗口内随会话保留，退出后不恢复，UI 已明确说明。图片及原始排版不属于提取文本。

新增职责已拆为附件接纳/管理、操作预览、范围投影、分页历史和窗口展示。既有 ChatWidget、ConversationOrchestrator、ToolExecutionHandler 仍超过 500 行，本次仅做接口接线及原职责内的小修复；未新增综合控制器。继续扩展这些类前须按提交、执行或呈现边界拆出完整切片，避免增加新的职责和生命周期耦合。

本轮问题无未完成项。未运行真实 LLM、远端账户调用、安装包发布和全部无关业务测试；未验证原生 Windows 手动交互。既有术语工作线、依赖锁与构建配置不纳入本记录。任务临时探针目录已清理，保留交付截图；没有执行 Git 提交或推送。
