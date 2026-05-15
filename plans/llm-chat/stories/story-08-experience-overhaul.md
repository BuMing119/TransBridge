# Story 08: AI助手页面体验全面翻新

**所属方案**: `plans/llm-chat/plan.md`
**技术模块**: `src/transbridge/ui/tools/smart_assistant/` + `src/transbridge/infra/`
**业务域**: AI 辅助翻译 — 智能助手 UI
**状态**: 已确认
**创建日期**: 2026-05-11
**更新日期**: 2026-05-14（FR7.16 重写 Story-08-2/08-3，追加 Story-08-5）

## 前置依赖

### 上游 Story
- Story-07（同 plan）：UI 层 import 更新已完成 → `chat_widget.py` / `plan_card.py` import 路径已统一
- Story-06（同 plan）：后端包 layering 已完成 → `smart_assistant/` 包结构稳定

### 跨 Plan 依赖
- `agent-upgrade/plan.md` → ADR-008/010/012（安全护栏、可观测性、MCP）已实现

### 引用的架构决策
- ADR-008: SmartAssistant 代码分层 — UI 组件归入 `ui/tools/smart_assistant/`
- ADR-010: 共享基础设施提取 — `infra/` 包（MarkdownRenderer 归入此包）
- ADR-012: 安全护栏（中间件链）— 自动模式下的 admin 级工具始终需确认

## 概述

对 SmartAssistant 面板 UI 层进行全面体验升级，覆盖视觉风格、布局结构、交互流程三大维度，同时新增 Markdown 渲染器作为 infra/ 共享基础设施。共 5 个子 Story，预估 15.5h。

---

## 子 Story 清单

### Story-08-1: Markdown 渲染器基础设施

**Phase**: 8.1 | **预估**: 3h | **依赖**: 无

**验收标准**:
- [ ] `src/transbridge/infra/markdown_renderer.py` 存在，实现 `MarkdownRenderer` 类
- [ ] `render(text: str) -> QWidget` 方法返回渲染后的 QWidget
- [ ] 支持标题（H1-H6）、粗体/斜体/行内代码、代码块（带语言标注）、无序/有序列表、表格、链接、水平线
- [ ] 代码块使用等宽字体+深色背景，表格使用 QTableWidget 渲染
- [ ] 链接可点击（QDesktopServices.openUrl）
- [ ] 文本可选择和复制
- [ ] 不规范 Markdown（未闭合标签/混搭格式）降级为纯文本 QLabel，不抛异常
- [ ] 零 PyQt 外第三方依赖（纯正则+字符串解析，不使用 markdown/mistune 等库）
- [ ] `infra/__init__.py` 导出 `MarkdownRenderer`

**实现步骤**:
1. 实现 `MarkdownRenderer` 类：逐行解析 → tokenize → 映射到 QWidget 组件 → `markdown_renderer.py`（新建）
2. 实现代码块渲染：深色背景 QTextEdit（只读、等宽字体） → `markdown_renderer.py`
3. 实现表格渲染：QTableWidget（只读、自适应列宽） → `markdown_renderer.py`
4. 容错处理：最外层 try/except + 解析失败降级为 QLabel 纯文本 → `markdown_renderer.py`
5. 更新 `infra/__init__.py` 公开导出 `MarkdownRenderer` → `infra/__init__.py`

**涉及文件**:

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/infra/markdown_renderer.py` | 新建 | MarkdownRenderer 类（纯正则解析） |
| `src/transbridge/infra/__init__.py` | 修改 | 导出 MarkdownRenderer |

---

### Story-08-2: 文档流视觉风格 (FR7.16 重写)

**Phase**: 8.2 | **预估**: 4h | **依赖**: Story-08-1（MarkdownRenderer 渲染消息内容）
**对应需求**: FR7.14, FR7.16.1, FR7.16.2, FR7.16.4

> **注意**: 本子 Story 已于 2026-05-14 按 FR7.16 重写。原设计为微信气泡风格（左右对齐、强色差气泡），新设计为现代 AI 网页文档流风格（统一左对齐、文字头像、微弱色差）。

**验收标准**:
- [ ] `MessageBubble` 重写为文档流组件：取消左右对齐，所有消息统一左对齐排列
- [ ] 新增圆形文字头像：`AvatarLabel` 组件（24x24px，用户="U" 深色底白字，AI="A" 品牌色底白字），放置在消息内容左侧
- [ ] 消息内容区 max-width 720px，整体居中（左右 margin auto），无气泡边框
- [ ] 用户消息：极淡灰色背景 `#f7f7f7`，AI 消息：纯白无背景，仅通过头像和微弱色差区分
- [ ] 消息间距（16-20px）作为视觉分隔，替代气泡边框
- [ ] MarkdownRenderer 继续用于消息内容渲染，适配更宽的内容区
- [ ] ToolCard/PlanCard：保持颜色标识（黄=工具/蓝=计划），但改为统一左对齐内联样式，与文档流协调
- [ ] 全局字体保持：正文 13px，行距 1.5，中文微软雅黑

**实现步骤**:
1. 重写 `message_bubble.py` → 文档流布局组件 → `message_bubble.py`（重写）
   - 移除 `_BUBBLE_MAX_WIDTH` 和左右 `addStretch()` 逻辑
   - 新增 `AvatarLabel` 内部类（圆形 QLabel，24x24px，QSS 圆角 12px）
   - 布局改为：`[头像] [内容区(max-width:720px)]`，水平左对齐
   - 用户消息：`_inner` 背景色 `#f7f7f7`，AI 消息：`_inner` 无背景
   - 移除 system role 特殊处理，改为统一的轻量标签样式
2. 更新 `chat_widget.py` 消息区样式 → `chat_widget.py`
   - `_msg_layout` 添加左右 padding 以支持内容居中
   - `_scroll` 内部消息容器适配 720px 最大宽度
3. 更新 `tool_card.py` / `plan_card.py` → 内联左对齐，移除独立卡片凸起感 → `tool_card.py`, `plan_card.py`
   - 卡片宽度跟随消息区（max-width 720px），左对齐
   - 柔和阴影替代强边框，与文档流融合
4. 更新 `thinking_indicator.py` → 文档流样式协调（左对齐 + 720px max-width）→ `thinking_indicator.py`

**涉及文件**:

| 文件 | 操作 | 说明 |
|------|------|------|
| `ui/tools/smart_assistant/message_bubble.py` | **重写** | 文档流布局 + AvatarLabel + 720px 居中 |
| `ui/tools/smart_assistant/chat_widget.py` | 修改 | 消息容器宽度限制 + padding |
| `ui/tools/smart_assistant/tool_card.py` | 修改 | 内联左对齐样式 |
| `ui/tools/smart_assistant/plan_card.py` | 修改 | 内联左对齐样式 |
| `ui/tools/smart_assistant/thinking_indicator.py` | 修改 | 文档流协调样式 |

---

### Story-08-3: 文档流布局重组 (FR7.16 重写)

**Phase**: 8.3 | **预估**: 3.5h | **依赖**: Story-08-2（在文档流视觉基础上调整整体布局）
**对应需求**: FR7.14, FR7.16.3, FR7.16.5, FR7.16.7, FR7.16.8, FR7.16.9

> **注意**: 本子 Story 已于 2026-05-14 按 FR7.16 重写。原设计仅涉及观测折叠/chips/滚动优化，新设计追加了输入框重构、系统消息融入、观测对话流化、面板放宽。

**验收标准**:
- [ ] **输入框重构**: 大面积居中设计，最小高度 60px、最大高度 200px、宽度跟随消息区（max-width 720px 居中）
- [ ] 输入框 placeholder: "输入消息，Ctrl+Enter 发送" 丰富为更友好的提示文本
- [ ] 发送按钮/自动模式/清空对话/上传按钮移至输入框下方，水平居中排列
- [ ] **系统消息融入**: 系统消息（工具结果/错误/状态）改为轻量横条标签，融入文档流左对齐
  - 成功消息：浅绿左边框（`border-left: 3px solid #4CAF50`）+ 淡绿背景
  - 失败消息：浅红左边框（`border-left: 3px solid #f44336`）+ 淡红背景
  - 中性消息：浅灰左边框 + 淡灰背景
  - 替代当前居中灰色小字样式
- [ ] **观测对话流化**: 移除独立 `QTabWidget` 观测面板，改为对话流内可折叠块
  - 通过命令（如输入框输入 `/obs`）或快捷按钮开关观测信息显示
  - 观测数据以轻量系统消息形式插入对话流（如"本轮 Token: 输入 1200 / 输出 800"）
- [ ] **面板最小尺寸放宽**: `SmartAssistantPanel` setMinimumWidth 从默认值放宽到 400px，setMinimumHeight 放宽到 300px
- [ ] **工具栏居中**: 快捷指令 chips 行和上传按钮在消息区下方、输入框上方居中排列
- [ ] 保留原 Story-08-3 滚动优化：平滑滚动 +「回到底部」浮动按钮（位置适配新布局）
- [ ] 窗口宽度 < 400px 时内容不溢出（弹性布局兜底）

**实现步骤**:
1. 输入框重构 → `chat_widget.py`
   - `_input.setMaximumHeight` 改为 200px（当前 100px），允许随内容自动增长
   - 输入框容器 QHBoxLayout 改为居中包裹（左右 stretch + 固定宽度的内容区）
   - 按钮行（发送/自动模式/清空/上传）移到输入框下方，居中排列
2. 系统消息融入 → `chat_widget.py`
   - 重写 `add_system_message()`: 创建轻量横条 widget（QFrame + 左边框 + 淡色背景）
   - 移除 `MessageBubble` 中 system role 的居中灰色样式
3. 观测对话流化 → `chat_widget.py`
   - 移除 `_obs_tabs`, `_obs_header` 及相关 toggle 逻辑
   - 新增 `_show_obs_inline()`: 将 Token/工具统计以可折叠系统消息形式插入对话流
   - 新增 `/obs` 命令解析：在 `_on_send` 中检测 `/obs` 前缀切换观测显示
   - 观测数据继续后台采集（`_obs_collector` 不变）
4. 面板放宽 → `panel.py`
   - `self.setMinimumWidth(400)` + `self.setMinimumHeight(300)`
5. Chips/上传居中 → `chat_widget.py` + `quick_actions.py`
   - `QuickActionsChips` 外层容器改为居中布局（左右 stretch + chips widget）
   - 上传按钮和 label 移入 chips 同一行

**涉及文件**:

| 文件 | 操作 | 说明 |
|------|------|------|
| `ui/tools/smart_assistant/chat_widget.py` | 修改 | 输入框重构 + 系统消息融入 + 观测对话流化 + 居中布局 |
| `ui/tools/smart_assistant/panel.py` | 修改 | 最小尺寸放宽 (400x300) |
| `ui/tools/smart_assistant/quick_actions.py` | 修改 | 居中布局适配 |

---

### Story-08-4: 流式打字机与自动模式

**Phase**: 8.4 | **预估**: 3h | **依赖**: Story-08-3（布局重组完成后才能正确测试交互流程）

**验收标准**:
- [ ] ChatWorker.chunk 信号连接 `_on_llm_chunk` 实现逐字追加到当前 AI 气泡
- [ ] 流式渲染使用 MarkdownRenderer，每次 chunk 到达时重新渲染完整内容
- [ ] 流式输出过程中用户发送新消息 → 正确 cancel 旧 worker → 清理残留气泡 → 开始新对话
- [ ] ChatWidget 新增「自动模式」开关（QCheckBox 或 Toggle 按钮），默认关闭
- [ ] 自动模式关闭时：PlanCard/ToolCard 正常显示确认按钮（当前行为）
- [ ] 自动模式开启时：LLM 返回工具调用 → 不显示确认卡片 → 直接执行 → 显示结果摘要
- [ ] 自动模式开启时：LLM 返回计划 → 不显示 PlanCard → 直接执行 → 显示步骤结果汇总
- [ ] admin 级工具在自动模式下仍然弹窗确认（安全护栏优先）
- [ ] 自动模式开关状态持久化到 QSettings
- [ ] 网络错误重试逻辑保持当前行为不变

**实现步骤**:
1. 流式打字机：`_on_llm_chunk` 追加到当前 AI 气泡（MarkdownRenderer 增量渲染） → `chat_widget.py`
2. 中断安全：发送新消息时检测旧 worker → cancel + wait + 清理 → `chat_widget.py`
3. 自动模式开关 UI：Toggle 按钮 + QSettings 持久化 → `chat_widget.py`
4. 自动模式逻辑：`_on_llm_finished` 检测开关状态 → 直接执行或显示卡片 → `chat_widget.py`
5. 安全护栏优先：admin 级工具跳过自动模式，始终弹窗确认 → `chat_widget.py`
6. 更新 ToolCard/PlanCard 支持自动执行回调 → `chat_widget.py`, `tool_card.py`, `plan_card.py`

**涉及文件**:

| 文件 | 操作 | 说明 |
|------|------|------|
| `ui/tools/smart_assistant/chat_widget.py` | 修改 | 流式打字机 + 自动模式逻辑 + 中断安全 |
| `ui/tools/smart_assistant/tool_card.py` | 修改 | 自动模式支持 |
| `ui/tools/smart_assistant/plan_card.py` | 修改 | 自动模式支持 |

---

### Story-08-5: 思考过程折叠显示

**Phase**: 8.5 | **预估**: 2.5h | **依赖**: Story-08-4（流式完成后才能正确显示/替换思考指示器）

**背景**: 当前 LLM 返回的 thought（思考过程）以完整 JSON 原文显示在流式气泡中，流式结束后又被 `_on_llm_finished` 提取为独立气泡重复显示（已知 bug：`self._streaming_bubble = None` 过早清空导致 `had_streaming` 判断失效）。用户期望改成 Claude Code 风格——默认折叠思考过程，仅显示简洁的动画指示器。

**验收标准**:
- [ ] thought 存在时，流式气泡结束后替换为 `ThinkingIndicator`（"正在思考中..." + 三点循环动画），不显示第二个重复气泡
- [ ] 三点动画循环：`.  ` → `.. ` → `...` → `.  `（500ms 间隔，QTimer 驱动）
- [ ] `ThinkingIndicator` 为非气泡形式：紧凑横条（高度 28-32px），浅灰背景 + 左侧动画图标，区别于 MessageBubble
- [ ] 按 `Ctrl+O` 展开详细 thought 文本（QTextEdit 只读，等宽字体，最大高度 200px），再次按 `Ctrl+O` 或点击收起按钮折叠
- [ ] 无 thought（纯文本回复）时不显示 ThinkingIndicator
- [ ] 动画在 thought 内容展开时暂停，折叠后恢复；工具执行完成/下一轮 LLM 开始时自动移除
- [ ] Bug 修复：`_on_llm_finished` 中 `had_streaming` 状态在 `self._streaming_bubble = None` 前保存，避免 thought 重复显示

**数据流**:
```
LLM 响应 → _on_llm_finished
  ├─ parse_hybrid_response(response) → {mode, thought, steps}
  ├─ 修复: had_streaming = self._streaming_bubble is not None  ← 在清空前保存
  ├─ self._streaming_bubble = None  (清空流式气泡)
  ├─ if thought and not had_streaming:  ← 只在无流式气泡时单独显示
  │     # 不走这分支（大多数情况有流式）
  ├─ if thought:
  │     _show_thinking_indicator(thought)  ← 替换为 ThinkingIndicator
  │         └─ ThinkingIndicator.set_thought(thought)
  │              ├─ 默认: QLabel("正在思考中...") + 三点动画
  │              └─ Ctrl+O → toggle_expand() → QTextEdit(thought)
  └─ 工具执行 / 下一轮 LLM → _hide_thinking_indicator()
```

**关键接口**:

```python
# ThinkingIndicator (新建)
class ThinkingIndicator(QWidget):
    def set_thought(self, text: str) -> None: ...
    def clear(self) -> None: ...
    def stop_animation(self) -> None: ...
    def toggle_expand(self) -> None: ...

# ChatWidget (修改)
class ChatWidget(QWidget):
    def _on_llm_finished(self, response: str) -> None:
        # Bug 修复：保存 had_streaming 状态
        had_streaming = self._streaming_bubble is not None
        if self._streaming_bubble:
            self._streaming_bubble.set_text(self._streaming_text)
            self._streaming_bubble = None
        ...
        if thought and not had_streaming:
            ...  # 不再 add_assistant_bubble
        self._show_thinking_indicator(thought)

    def _show_thinking_indicator(self, thought: str) -> None: ...
    def _hide_thinking_indicator(self) -> None: ...
    def _toggle_thought_expand(self) -> None: ...
```

**实现步骤**:
1. 新建 `thinking_indicator.py`：`ThinkingIndicator(QWidget)` 组件 → `thinking_indicator.py`（新建）
   - 默认状态：QLabel("正在思考中...") + QLabel("...") 三点动画（QTimer 500ms 循环）
   - 展开状态：QTextEdit（只读、等宽字体、max-height 200px）显示完整 thought 文本
   - `set_thought(text)` / `clear()` / `stop_animation()` / `toggle_expand()` 公共接口
   - 样式：浅灰背景 `#f5f5f5`，圆角 8px，左对齐，高度 28-32px
2. 修改 `chat_widget.py` → `chat_widget.py`
   - Bug 修复：在 `self._streaming_bubble = None` 前保存 `had_streaming`
   - thought 展示：改为 `_show_thinking_indicator(thought)`
   - 新增 `_show_thinking_indicator()` / `_hide_thinking_indicator()`
3. 快捷键绑定 → `chat_widget.py`
   - `QShortcut(QKeySequence("Ctrl+O"), self)` → `_toggle_thought_expand()`
   - 快捷键仅在 ThinkingIndicator 可见时生效
4. 样式适配 → `thinking_indicator.py`

**涉及文件**:

| 文件 | 操作 | 说明 |
|------|------|------|
| `ui/tools/smart_assistant/thinking_indicator.py` | 新建 | ThinkingIndicator 组件（动画 + 折叠/展开 + 样式） |
| `ui/tools/smart_assistant/chat_widget.py` | 修改 | thought 展示逻辑改造 + bug 修复 + Ctrl+O 快捷键 |

---

## 文件变更总清单

| 文件 | Story | 操作 | 变更内容 |
|------|-------|------|---------|
| `src/transbridge/infra/markdown_renderer.py` | 08-1 | **新建** ✅ | MarkdownRenderer 类（纯正则解析，零外部依赖） |
| `src/transbridge/infra/__init__.py` | 08-1 | 修改 ✅ | 导出 MarkdownRenderer |
| `ui/tools/smart_assistant/message_bubble.py` | 08-2 | **重写** | 文档流布局 + AvatarLabel + 720px 居中 [FR7.16] |
| `ui/tools/smart_assistant/chat_widget.py` | 08-2~08-5 | 修改 | 文档流容器 + 输入框重构 + 系统消息融入 + 观测流化 + 居中布局 + 流式 + 自动模式 + thought折叠 [FR7.16] |
| `ui/tools/smart_assistant/thinking_indicator.py` | 08-5 | **新建** | ThinkingIndicator 组件（动画 + 折叠/展开） |
| `ui/tools/smart_assistant/tool_card.py` | 08-2, 08-4 | 修改 | 内联左对齐样式 + 自动模式支持 [FR7.16] |
| `ui/tools/smart_assistant/plan_card.py` | 08-2, 08-4 | 修改 | 内联左对齐样式 + 自动模式支持 [FR7.16] |
| `ui/tools/smart_assistant/quick_actions.py` | 08-3 | 修改 | 居中布局适配 [FR7.16] |
| `ui/tools/smart_assistant/panel.py` | 08-3 | 修改 | 最小尺寸放宽 (400x300) [FR7.16] |

## 风险与注意事项

| 风险 | 影响 | 回退方案 |
|------|------|---------|
| Markdown 渲染器正则解析覆盖不全 | 部分消息渲染异常 | 最外层 try/except + 降级纯文本兜底 |
| 流式渲染中 MarkdownRenderer 频繁重建 QWidget | 长消息卡顿 | 节流渲染（每 50ms 合并）；超长消息降级纯文本 |

## 风险与注意事项

| 风险 | 影响 | 回退方案 |
|------|------|---------|
| Markdown 渲染器正则解析覆盖不全 | 部分消息渲染异常 | 最外层 try/except + 降级纯文本兜底 |
| 流式渲染中 MarkdownRenderer 频繁重建 QWidget | 长消息卡顿 | 节流渲染（每 50ms 合并）；超长消息降级纯文本 |
| 自动模式下工具执行失败无用户干预 | 错误级联 | 工具失败后追加错误信息并暂停自动模式 |
| 布局重组后观测面板折叠状态不符预期 | 用户找不到观测数据 | 首次启动默认折叠但显示 tooltip 提示 |
| ThinkingIndicator 动画与流式渲染时序冲突 | 动画残留或闪烁 | `_on_llm_finished` 先停止动画再替换 |
| `Ctrl+O` 与系统快捷键冲突 | 快捷键无响应 | 提供收起按钮作为备选展开方式 |
| 文档流布局在窗口缩小时内容溢出 | 输入框/消息被挤压 | 弹性布局兜底（< 400px 时取消 720px 限制，改为百分比宽度） |
| AvatarLabel 创建过多导致内存增长 | 长对话性能下降 | AvatarLabel 复用同一 QWidget（仅更新文字），而非每条消息新建 |
