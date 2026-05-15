# 智能助手侧边栏面板

**对应需求**: FR7 (用户界面) — 新增 AI 智能助手入口
**技术模块**: `src/transbridge/ui/tools/smart_assistant/` (新建)
**业务域**: AI 辅助翻译
**状态**: ✅ 已实现（Story-01~09 全部完成，含 ChatWidget 拆分重构）
**创建日期**: 2026-03
**确认日期**: 2026-05-06

## 功能边界

### 范围内

- QDockWidget 侧边栏面板，可拖拽/浮动/停靠到主窗口任意边缘
- ReAct 模式：思考→行动→观察循环，每步用户确认/忽略
- 计划模式：LLM 输出执行计划 → 用户一次确认 → ExecutionEngine 批量调度执行
- 统一执行引擎：DAG 拓扑排序 + 层级并行（ThreadPoolExecutor）
- 消息气泡 UI：用户（绿色右对齐）/ AI（白色左对齐）/ 系统（灰色居中）/ PlanCard（蓝色）/ ToolCard（黄色）
- 快捷指令面板：翻译选中、质量检查、查询术语、导出 JSON
- v1 工具集（6个）：lookup_terms / translate_entries / check_quality / get_collection_summary / export_json / write_back
- 菜单入口（小工具→智能助手）+ 快捷键 Ctrl+K / Ctrl+Shift+I + View 菜单勾选
- 多轮对话管理（ConversationManager，max_turns=20）
- 主窗口状态持久化（QSettings：geometry + DockWidget state）
- PromptBuilder 扩展：parse_hybrid_response() 解析 mode/thought/steps
- LLM 流式调用 + 取消支持（复用现有 LLMClient）

### 范围外

- 自然语言理解以外的 GUI 直接操作（如拖拽排序词条）
- 语音输入/输出
- 助手对话的多语言界面（限定中文交互）
- 移动端/Web 端适配
- 工具插件热加载系统（v1 硬编码注册，架构预留扩展）
- WebSocket 实时推送
- 对话历史的跨会话持久化（仅内存，关闭即清空）

## Story 清单

### Story-01: SmartAssistantPanel 基础框架

**Phase**: 1 | **预估**: 6.5h | **状态**: 📝
**详细文档**: `stories/story-01-panel-framework.md`

**验收标准**:
- [ ] 点击菜单"小工具→智能助手"，右侧展开 QDockWidget 面板
- [ ] 面板最小宽度 360px，可拖拽/浮动/停靠
- [ ] 面板默认隐藏，快捷键 Ctrl+K 可切换显示/隐藏
- [ ] View 菜单勾选状态与面板可见性同步
- [ ] 快捷指令面板显示 4 个按钮（翻译选中/质量检查/查询术语/导出JSON）
- [ ] 点击快捷指令按钮，文字填充到输入框
- [ ] 用户/AI/系统三种气泡样式正确渲染
- [ ] 输入框支持 Ctrl+Enter 发送

**实现步骤**:
1. 创建 `SmartAssistantPanel` (QDockWidget) → `panel.py`
2. 创建 `ChatWidget`（消息滚动区 + 输入区 + 按钮行） → `chat_widget.py`
3. 创建 `MessageBubble`（用户/AI/系统三种样式） → `message_bubble.py`
4. 创建 `QuickActionsPanel`（4 个快捷指令按钮） → `quick_actions.py`
5. 集成到 `MainWindow`（addDockWidget + 菜单 action + QShortcut） → `main_window.py`

### Story-02: 核心后端

**Phase**: 2 | **预估**: 8h | **状态**: 📝
**详细文档**: `stories/story-02-core-backend.md`

**验收标准**:
- [ ] ConversationManager 正确维护多轮对话，max_turns=20
- [ ] ChatWorker 后台调用 LLM 流式接口，chunk 信号逐字传递
- [ ] ChatWorker 支持 cancel() 中断（threading.Event + LLMClient.cancel()）
- [ ] parse_hybrid_response() 正确解析 mode/thought/steps JSON 格式
- [ ] parse_hybrid_response() 兼容旧 tool_calls 格式和纯文本响应
- [ ] ExecutionEngine 对 steps 进行拓扑排序分层
- [ ] ExecutionEngine 同层级步骤在线程池中并行执行
- [ ] ExecutionEngine 支持 cancel() 优雅中断

**实现步骤**:
1. 创建 `ConversationManager`（add_system/user/assistant/observation/plan_result） → `conversation_manager.py`
2. 创建 `ChatWorker`（QThread + chunk/finished/error 信号 + cancel） → `chat_worker.py`
3. 创建 `ExecutionEngine`（StepResult + _topological_levels + execute + cancel） → `execution_engine.py`
4. 扩展 `PromptBuilder.parse_hybrid_response()` → `prompt_builder.py`

### Story-03: 循环控制与 UI 卡片

**Phase**: 3 | **预估**: 8h | **状态**: 📝
**详细文档**: `stories/story-03-loop-control-cards.md`
**验收标准**:
- [ ] 用户发送消息后，ChatWidget 调用 LLM 并显示响应
- [ ] LLM 返回 mode=plan 时显示 PlanCard（步骤列表 + 依赖 + 执行/取消按钮）
- [ ] 用户确认 PlanCard 后 ExecutionEngine 按依赖顺序执行，每步显示进度
- [ ] 全部执行完成后聚合结果反馈给 LLM，LLM 输出最终总结
- [ ] LLM 返回 mode=react 且单步时显示 ToolCard（执行/忽略按钮）
- [ ] LLM 返回 mode=react 且多步时显示 BatchToolCard（全部执行按钮）
- [ ] 工具执行完成后结果反馈给 LLM 继续推理（ReAct 循环）
- [ ] 用户可点击"忽略"跳过工具调用，LLM 能继续推理
- [ ] ReAct 循环最大深度 10 轮，超出自动终止
- [ ] 计划执行中某步失败，不阻塞后续无依赖步骤

**实现步骤**:
1. 实现 ChatWidget 混合循环控制（_run_llm_round / _on_llm_finished / 模式分发） → `chat_widget.py`
2. 创建 `ToolCard`（黄色卡片 + 参数表 + 执行/忽略按钮 + 状态显示） → `tool_card.py`
3. 创建 `PlanCard`（蓝色卡片 + 步骤列表 + 依赖标注 + 进度预览 + 执行/取消） → `plan_card.py`
4. 创建 `BatchToolCard`（步骤概览 + 全部执行按钮） → `tool_card.py` 或独立文件
5. 在 ChatWidget 中集成 ExecutionEngine 信号（step_started/step_finished/all_finished/progress） → `chat_widget.py`

### Story-04: 工具系统

**Phase**: 4 | **预估**: 6h | **状态**: ✅ 已完成
**详细文档**: `stories/story-04-tool-system.md`

**验收标准**:
- [ ] ToolRegistry 支持注册/查找/列出全部工具
- [ ] ToolSpec 包含 name/display_name/description/parameters/is_long_running/execute
- [ ] build_tool_schema_for_prompt() 生成 LLM 可用的工具描述
- [ ] lookup_terms 工具正确查询术语库并返回结果
- [ ] translate_entries 工具复用 AutoTranslator 执行翻译
- [ ] check_quality 工具复用 PostProcessor 执行质量检查
- [ ] get_collection_summary 工具返回当前集合统计
- [ ] export_json 工具导出当前集合到 JSON 文件
- [ ] write_back 工具复用 PluginWriter/EETWriter/XTWriter 写回译文

**实现步骤**:
1. 创建 `ToolRegistry` + `ToolSpec` 数据类 → `tool_registry.py`
2. 注册 v1 核心工具（lookup_terms / translate_entries / check_quality / get_collection_summary） → `tool_registry.py`
3. 注册 v1 扩展工具（export_json / write_back） → `tool_registry.py`

### Story-05: 体验优化

**Phase**: 5 | **预估**: 6h | **状态**: ✅ 已完成
**详细文档**: `stories/story-05-experience-optimization.md`

**验收标准**:
- [ ] 关闭主窗口后重新打开，面板位置/宽度/可见状态正确恢复
- [ ] 面板关闭时 ChatWorker 被正确终止（wait + deleteLater）
- [ ] LLM 配置缺失时提示用户先配置 API
- [ ] 工具执行失败时显示友好错误消息
- [ ] 网络错误时提供重试提示
- [ ] 计划执行支持中途取消，线程安全
- [ ] ContextBuilder 正确收集当前 AppContext 状态（集合概况/选中条目数）

**实现步骤**:
1. 实现 QSettings 状态持久化（saveState/restoreState + geometry） → `main_window.py`
2. 创建 `ContextBuilder`（构建当前上下文信息供 system prompt 使用） → `context_builder.py`
3. 错误处理完善（LLM 配置缺失/工具执行失败/网络错误/取消安全） → `chat_widget.py`

### Story-08: AI助手页面体验全面翻新

**Phase**: 8 | **预估**: 15.5h（5 子 Story） | **状态**: 📝
**对应需求**: FR7.14 | **架构引用**: ADR-010, ADR-008
**详细展开**: 见下方 [Story-08 详细节](#story-08-ai助手页面体验全面翻新-2)

**子 Story**:
- Story-08-1: Markdown 渲染器基础设施（3h, 2 文件: markdown_renderer.py 新 + `__init__.py`）
- Story-08-2: 视觉风格现代化（4h, 4 文件: message_bubble/tool_card/plan_card/chat_widget）
- Story-08-3: 布局重组与滚动优化（3h, 3 文件: panel/chat_widget/quick_actions）
- Story-08-4: 流式打字机与自动模式（3h, 3 文件: chat_widget/tool_card/plan_card）
- Story-08-5: 思考过程折叠显示（2.5h, 2 文件: thinking_indicator.py 新 + chat_widget）

## 新建文件清单

```
src/transbridge/smart_assistant/     # Story-06 (NEW: 后端业务逻辑包)
├── __init__.py                     # 公开 API 导出
├── conversation_manager.py         # Story-02 (搬迁自 ui/tools/)
├── chat_worker.py                  # Story-02 (搬迁自 ui/tools/)
├── execution_engine.py             # Story-02 (搬迁自 ui/tools/)
├── tool_registry.py                # Story-04 (搬迁自 ui/tools/)
├── context_builder.py              # Story-05 (搬迁自 ui/tools/)
└── prompts.py                      # Story-04 (搬迁自 ui/tools/)

src/transbridge/ui/tools/smart_assistant/
├── __init__.py
├── panel.py                       # Story-01, 08-3
├── chat_widget.py                 # Story-01, 03, 05, 07, 08-2, 08-3, 08-4
├── message_bubble.py              # Story-01, 08-2
├── quick_actions.py               # Story-01, 08-3
├── tool_card.py                   # Story-03, 08-2, 08-4
└── plan_card.py                   # Story-03, 07, 08-2, 08-4

src/transbridge/infra/
└── markdown_renderer.py            # Story-08-1 (NEW: 共享 Markdown 渲染组件)
```

## 需修改的现有文件

| 文件 | Story | 修改内容 |
|------|-------|---------|
| `src/transbridge/ui/main_window.py` | Story-01, 05 | 集成 DockWidget + 菜单/快捷键 + QSettings 持久化 |
| `src/transbridge/ai_translator/prompt_builder.py` | Story-02 | 新增 parse_hybrid_response() |
| `src/transbridge/infra/__init__.py` | Story-08-1 | 导出 MarkdownRenderer |

## Story-06/07 文件变更

### 搬迁（Story-06）

| 源路径 | 目标路径 |
|--------|---------|
| `ui/tools/smart_assistant/conversation_manager.py` | `smart_assistant/conversation_manager.py` |
| `ui/tools/smart_assistant/chat_worker.py` | `smart_assistant/chat_worker.py` |
| `ui/tools/smart_assistant/execution_engine.py` | `smart_assistant/execution_engine.py` |
| `ui/tools/smart_assistant/tool_registry.py` | `smart_assistant/tool_registry.py` |
| `ui/tools/smart_assistant/context_builder.py` | `smart_assistant/context_builder.py` |
| `ui/tools/smart_assistant/prompts.py` | `smart_assistant/prompts.py` |

### 修改（Story-07）

| 文件 | 修改内容 |
|------|---------|
| `ui/tools/smart_assistant/chat_widget.py` | 4 处 import 改为 `from src.transbridge.smart_assistant.xxx` |
| `ui/tools/smart_assistant/plan_card.py` | 1 处 import 改为 `from src.transbridge.smart_assistant.execution_engine import StepResult` |
| `smart_assistant/prompts.py` | 1 处 import 改为包内相对导入 `.tool_registry` |

## 架构依赖

- [ADR-004: QThread + 信号总线异步模式](../../docs/adr/004-qthread-async-pattern.md) — ChatWorker 采用 QThread 模式，信号/槽线程通信，BaseException 取消控制
- [ADR-005: TOML Prompt 模板，不使用 LangChain](../../docs/adr/005-toml-prompt-no-langchain.md) — 不引入 LangChain/LangGraph，PromptBuilder 自建扩展，复用现有 LLMClient

### Story-06: 新建后端包 + 文件搬迁

**Phase**: 6 | **预估**: 2h | **状态**: 📝
**对应需求**: FR7.12 | **架构引用**: ADR-008
**详细文档**: `plans/llm-chat/stories/story-06-layering-backend.md`

**验收标准**:
- [ ] `src/transbridge/smart_assistant/` 包存在且含 `__init__.py`
- [ ] `__init__.py` 导出 7 个公开符号（ConversationManager, ChatWorker, ExecutionEngine, StepResult, ToolRegistry, ToolSpec, ContextBuilder, build_system_prompt）
- [ ] 6 个后端文件从 UI 目录搬迁到新包，原位置文件删除
- [ ] `prompts.py` 中 `from .tool_registry import ToolRegistry` 包内相对导入正确
- [ ] `from src.transbridge.smart_assistant import ConversationManager` 可成功执行

**实现步骤**:
1. 创建 `src/transbridge/smart_assistant/__init__.py`（公开 API 导出） → `smart_assistant/__init__.py` (新建)
2. 搬迁 6 个后端文件到新包，删除 UI 目录下原文件 → `conversation_manager.py`, `chat_worker.py`, `execution_engine.py`, `tool_registry.py`, `context_builder.py`, `prompts.py`
3. 更新 `prompts.py` 包内 import：`from .tool_registry import ToolRegistry` → `prompts.py`

### Story-07: UI 层 import 更新 + 验证

**Phase**: 7 | **预估**: 1.5h | **状态**: 📝
**对应需求**: FR7.12 | **架构引用**: ADR-008
**详细文档**: `plans/llm-chat/stories/story-07-layering-ui.md`

**验收标准**:
- [ ] `chat_widget.py` 4 处跨包导入改为绝对导入（指向 `src.transbridge.smart_assistant`）
- [ ] `plan_card.py` 1 处跨包导入改为绝对导入（`ExecutionEngine.StepResult`）
- [ ] `main_window.py` 导入路径保持不变
- [ ] 启动应用无 ImportError
- [ ] 智能助手面板可正常打开和关闭
- [ ] LLM 对话功能正常（发送消息 → 收到回复）
- [ ] 6 个工具均可正常执行
- [ ] PlanCard 和 ToolCard 显示和交互正常

**实现步骤**:
1. 更新 `chat_widget.py` 的 4 处 import（conversation_manager, chat_worker, execution_engine → 改为 `from src.transbridge.smart_assistant.xxx import ...`） → `chat_widget.py`
2. 更新 `plan_card.py` 的 1 处 import（execution_engine.StepResult → 改为绝对导入） → `plan_card.py`
3. 启动应用验证：面板可见、对话可用、工具可执行 → 手动验证

### Story-08: AI助手页面体验全面翻新

**Phase**: 8 | **预估**: 18h（5 子 Story） | **状态**: 📝
**对应需求**: FR7.14, FR7.16 | **架构引用**: ADR-010, ADR-008
**详细文档**: `stories/story-08-experience-overhaul.md`

**概述**: 对 SmartAssistant 面板 UI 层进行全面体验升级，从微信气泡风格重构为现代 AI 网页文档流风格（FR7.16）。覆盖视觉风格、布局结构、交互流程三大维度，同时新增 Markdown 渲染器作为 infra/ 共享基础设施。共 5 个子 Story（08-1 ~ 08-5）。Story-08-2/08-3 已于 2026-05-14 按 FR7.16 重写。

**子 Story 速览**:
- Story-08-1: Markdown 渲染器基础设施（3h）— 已编码
- Story-08-2: 文档流视觉风格（4h）— 已重写 [FR7.16]
- Story-08-3: 文档流布局重组（3.5h）— 已重写 [FR7.16]
- Story-08-4: 流式打字机与自动模式（3h）— 已编码
- Story-08-5: 思考过程折叠显示（2.5h）— 待编码

### Story-08 文件变更清单

| 文件 | Story | 操作 | 变更内容 |
|------|-------|------|---------|
| `src/transbridge/infra/markdown_renderer.py` | 08-1 | **新建** | MarkdownRenderer 类（纯正则解析，零外部依赖） |
| `src/transbridge/infra/__init__.py` | 08-1 | 修改 | 导出 MarkdownRenderer |
| `ui/tools/smart_assistant/message_bubble.py` | 08-2 | **重写** | MarkdownRenderer 渲染 + 新气泡样式（圆角/阴影/配色） |
| `ui/tools/smart_assistant/chat_widget.py` | 08-2, 08-3, 08-4, 08-5 | 修改 | 视觉样式 + 观测折叠 + 移除Agent指示器 + 上传栏移位 + 滚动优化 + 流式打字机 + 自动模式 + thought展示改造 + Ctrl+O快捷键 |
| `ui/tools/smart_assistant/thinking_indicator.py` | 08-5 | **新建** | ThinkingIndicator 组件（动画 + 折叠/展开 + 样式） |
| `ui/tools/smart_assistant/tool_card.py` | 08-2, 08-4 | 修改 | 卡片样式翻新 + 自动模式支持 |
| `ui/tools/smart_assistant/plan_card.py` | 08-2, 08-4 | 修改 | 卡片样式翻新 + 自动模式支持 |
| `ui/tools/smart_assistant/quick_actions.py` | 08-3 | **重写** | 从固定 Panel 改为 chips 标签式工具栏 |
| `ui/tools/smart_assistant/panel.py` | 08-3 | 修改 | 集成新的 chips 工具栏，调整整体布局 |
| `ui/tools/smart_assistant/__init__.py` | 08-3 | 不变 | SmartAssistantPanel 导出保持 |

### Story-08 新增风险

| 风险 | 影响 | 回退方案 |
|------|------|---------|
| Markdown 渲染器正则解析覆盖不全（嵌套格式/边界情况） | 部分消息渲染异常 | 最外层 try/except + 降级纯文本兜底；逐步完善正则覆盖 |
| 流式渲染中 MarkdownRenderer 频繁重建 QWidget 导致性能问题 | 长消息卡顿 | 节流渲染（每 50ms 或每 5 chunk 合并渲染一次）；超长消息（>5000字）降级纯文本 |
| 自动模式下工具执行失败无用户干预 | 错误级联 | 工具失败后追加错误信息到对话并暂停自动模式，等待用户决策 |
| 布局重组后观测面板折叠状态与用户预期不符 | 用户找不到观测数据 | 首次启动默认折叠但显示 tooltip 提示「点击展开观测面板」 |
| ThinkingIndicator 动画与流式渲染时序冲突 | 动画残留或闪烁 | `_on_llm_finished` 中先停止动画再替换；widget 销毁前调用 `stop_animation()` |

### Story-09: ChatWidget 拆分重构

**Phase**: 9 | **预估**: 6.5h（3 子 Story） | **状态**: 📝
**对应问题**: QA 报告 C1（ChatWidget 1120行超重，违反 ADR-008） | **架构引用**: ADR-008
**详细文档**: `stories/story-09-chatwidget-refactor.md`

**概述**: ChatWidget 当前 1120 行/48 方法，混合 UI 渲染与 LLM 编排/工具执行等 10+ 职责。按 ADR-008 拆分为 3 个类：ChatWidget（纯 UI ~400行）、ConversationOrchestrator（LLM编排 ~300行）、ToolExecutionHandler（工具执行 ~200行）。

**子 Story**:
- Story-09-1: 提取 ToolExecutionHandler（2h, 2 文件: tool_execution_handler.py 新建 + chat_widget.py 改）
- Story-09-2: 提取 ConversationOrchestrator（3h, 2 文件: conversation_orchestrator.py 新建 + chat_widget.py 改）
- Story-09-3: 精简 ChatWidget 为纯 UI（1.5h, 3 文件: chat_widget.py 改 + panel.py 改 + __init__.py）

### Story-09 文件变更清单

| 文件 | Story | 操作 | 说明 |
|------|-------|------|------|
| `ui/tools/smart_assistant/tool_execution_handler.py` | 09-1 | **新建** | ToolExecutionHandler 类（工具权限/执行/重试/护栏） |
| `ui/tools/smart_assistant/conversation_orchestrator.py` | 09-2 | **新建** | ConversationOrchestrator 类（LLM轮次/模式分发/流式/Worker） |
| `ui/tools/smart_assistant/chat_widget.py` | 09-1/2/3 | 修改 | 搬迁后精简，1120→~400行 |
| `ui/tools/smart_assistant/panel.py` | 09-3 | 修改 | 适配新接口 |

### Story-10: ToolResult 观察消息序列化增强

**Phase**: 10 | **预估**: 3h | **状态**: ✅ 已完成
**对应需求**: FR7.17 | **架构引用**: ADR-012（更新 2026-05-14）
**详细文档**: `stories/story-10-toolresult-observation.md`

**概述**: ToolResult.data 当前被 _handle_result() 丢弃，LLM 看不到工具返回的结构化数据。新增 to_observation() 序列化方法将 data 格式化为 LLM 可解析文本，新增 pagination/execution_meta/tool_suggestions 三个扩展字段，修复 6 个缺失 data 的工具。

**验收标准**:
- [ ] ToolResult.to_observation() 正确序列化 data/pagination/meta/suggestions
- [ ] 大数据（50+ 条目）自动摘要为 count + sample，不超 2000 字符
- [ ] 向后兼容：data=None 时输出格式不变
- [ ] 6 个工具补充 data 参数
- [ ] get_visible_entries 首次使用 pagination，筛选工具首次使用 tool_suggestions

**实现步骤**:
1. 扩展 ToolResult 类（新增 3 字段 + to_observation + _serialize_data） → `smart_assistant/tools/base.py`
2. 更新 _handle_result() 调用 to_observation() → `ui/tools/smart_assistant/tool_execution_handler.py`
3. 优化 add_observation() 换行感知截断 → `smart_assistant/conversation_manager.py`
4. 修复 6 工具补充 data + 示例性 pagination/suggestions → `smart_assistant/tools/tool_editor.py` + `smart_assistant/tools/tool_translator.py`

### Story-10 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `smart_assistant/tools/base.py` | 修改 | 新增 3 字段 + to_observation() + _serialize_data() |
| `ui/tools/smart_assistant/tool_execution_handler.py` | 修改 | _handle_result() 改用 to_observation() |
| `smart_assistant/conversation_manager.py` | 修改 | add_observation() 换行感知截断 |
| `smart_assistant/tools/tool_editor.py` | 修改 | 5 工具补 data + pagination/suggestions |
| `smart_assistant/tools/tool_translator.py` | 修改 | 1 工具补 data |

## 风险与回退方案

| 风险 | 影响 | 回退方案 |
|------|------|---------|
| LLM 响应格式不稳定（JSON 解析失败） | Plan/ReAct 模式无法正确分发 | 容错解析（截断 JSON 修复）+ 降级为纯文本回复 + 用户可手动重试 |
| ExecutionEngine DAG 存在环 | 死锁 | _topological_levels 检测环后兜底为单层串行，并在日志中警告 |
| ChatWorker 线程未正确终止 | 内存泄漏 / 崩溃 | 面板关闭时 wait(3000) + terminate() + deleteLater()，ChatWidget.destroyEvent 中清理 |
| 工具执行中修改 Collection 导致线程不安全 | 数据竞争 / 崩溃 | 所有 Collection 修改通过主线程信号完成，工具仅返回结果数据，ChatWidget 在主线程槽函数中写回 |

---

> 原始详细设计见 [docs/dev/llm_chat_plan.md](../../docs/dev/llm_chat_plan.md)（含完整 UI 伪代码、后端 API 设计、消息流示例、验证计划）
