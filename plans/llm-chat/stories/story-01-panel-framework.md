# Story 01: SmartAssistantPanel 基础框架

**所属方案**: `plans/llm-chat/plan.md`
**技术模块**: `src/transbridge/ui/tools/smart_assistant/` (新建)
**状态**: ✅ 已确认
**创建日期**: 2026-05-06

## 前置依赖

### 上游 Story
- 无（本 Story 为 Phase 1 起点）

### 跨 Plan 依赖
- `ui-workbench/plan.md` → `MainWindow` 集成点（在 `_init_central` 中添加 DockWidget）
- `ai-translation/plan.md` → `AppContext` 状态访问（只读引用，无修改）

### 引用的架构决策
- [ADR-004: QThread + 信号总线异步模式](../../../docs/adr/004-qthread-async-pattern.md) — MainWindow 信号/槽模式，QShortcut 注册

## 验收标准

- [ ] 点击菜单"小工具→智能助手"，右侧展开 QDockWidget 面板
- [ ] 面板最小宽度 360px，可拖拽/浮动/停靠
- [ ] 面板默认隐藏，快捷键 Ctrl+K 可切换显示/隐藏
- [ ] View 菜单勾选状态与面板可见性同步
- [ ] 快捷指令面板显示 4 个按钮（翻译选中/质量检查/查询术语/导出JSON）
- [ ] 点击快捷指令按钮，文字填充到输入框
- [ ] 用户/AI/系统三种气泡样式正确渲染
- [ ] 输入框支持 Ctrl+Enter 发送

## 数据流

```
用户触发                     UI 组件                      信号/状态变更
───────                    ────────                      ────────────
菜单"智能助手" / Ctrl+K  → MainWindow._toggle_smart_assistant()
                              ├─ panel.isVisible()?
                              │   ├─ Yes → panel.hide()
                              │   └─ No  → panel.show(); panel.raise()
                              └─ View menu action.setChecked(visible)

用户输入文本 + Ctrl+Enter → ChatWidget._eventFilter()
                              ├─ key == Ctrl+Return?
                              └─ _on_send()
                                   ├─ text.strip() 为空? → return
                                   ├─ _add_user_bubble(text)
                                   │    └─ MessageBubble(role="user", text)
                                   │         ├─ 背景 #DCF8C6, 右对齐
                                   │         └─ 12px 圆角
                                   ├─ _input.clear()
                                   └─ message_sent.emit(text)  → (Story-02 对接)

快捷指令按钮点击          → QuickActionsPanel
                              └─ action_clicked.emit(text)
                                   └─ ChatWidget.set_input(text)
                                        └─ _input.setText(text); _input.setFocus()

面板关闭按钮              → SmartAssistantPanel.closeEvent()
                              └─ View menu action.setChecked(False)
```

## 关键接口

### panel.py

```python
class SmartAssistantPanel(QDockWidget):
    """智能助手侧边栏面板，停靠在 MainWindow 右侧"""

    visibility_changed = pyqtSignal(bool)

    def __init__(self, ctx: AppContext, parent=None):
        """初始化面板布局：QuickActionsPanel | VLine | ChatWidget"""
        ...

    def showEvent(self, event) -> None:
        """面板显示时发射 visibility_changed(True)"""
        ...

    def hideEvent(self, event) -> None:
        """面板隐藏时发射 visibility_changed(False)"""
        ...
```

### chat_widget.py

```python
class ChatWidget(QWidget):
    """聊天区域：消息滚动列表 + 输入框 + 发送/清空按钮"""

    message_sent = pyqtSignal(str)  # 用户发送消息，Story-02 对接

    def __init__(self, ctx: AppContext, parent=None):
        """构建 UI：QScrollArea(消息列表) + QTextEdit(输入) + QPushButton(发送/清空)"""
        ...

    def _on_send(self) -> None:
        """处理发送：校验非空 → 添加用户气泡 → 清空输入 → 发射信号"""
        ...

    def set_input(self, text: str) -> None:
        """快捷指令填入文本并聚焦"""
        ...

    def add_user_bubble(self, text: str) -> None:
        """添加用户消息气泡：绿色背景，右对齐"""
        ...

    def add_assistant_bubble(self, text: str) -> None:
        """添加 AI 消息气泡：白色背景，左对齐，浅灰边框"""
        ...

    def add_system_message(self, text: str) -> None:
        """添加系统消息：灰色背景，居中，小字号"""
        ...

    def _add_bubble(self, bubble: MessageBubble) -> None:
        """将气泡添加到消息列表底部，自动滚动到底部"""
        ...

    def _clear_conversation(self) -> None:
        """清空对话历史，移除所有气泡"""
        ...
```

### message_bubble.py

```python
class MessageBubble(QWidget):
    """单条消息气泡，根据 role 渲染不同样式"""

    def __init__(self, text: str, role: str, parent=None):
        """
        role 取值:
          - "user":      绿色背景 #DCF8C6, 右对齐, 圆角 12px
          - "assistant": 白色背景, 左对齐, 圆角 12px, 浅灰边框
          - "system":    灰色背景 #F5F5F5, 居中, 字号 11px
        """
        ...
```

### quick_actions.py

```python
class QuickActionsPanel(QWidget):
    """左侧快捷指令面板，提供常用操作的快捷入口"""

    action_clicked = pyqtSignal(str)  # 点击按钮时发射指令文本

    def __init__(self, parent=None):
        """4 个按钮：翻译选中 / 质量检查 / 查询术语 / 导出JSON"""
        ...

    def _on_button_clicked(self, text: str) -> None:
        """按钮点击 → 发射 action_clicked 信号"""
        ...
```

## 实现步骤

### 步骤 1: 创建 MessageBubble

**涉及文件**: `src/transbridge/ui/tools/smart_assistant/message_bubble.py`（新建）

**实现要点**:
- 根据 `role` 参数渲染三种样式：user / assistant / system
- 使用 `QLabel` + `wordWrap=True` + 最大宽度 280px
- 用户/AI 气泡用 `QHBoxLayout` + `addStretch` 控制对齐方向
- 系统消息用居中对齐 `QHBoxLayout`

**边界条件**:
- text 为空字符串 → 仍渲染气泡（空消息不显示）
- text 含 HTML 标签 → 不做 HTML 渲染，使用 `setTextFormat(Qt.TextFormat.PlainText)`
- text 超长 → QLabel wordWrap 自动换行，最大宽度限制

**伪代码**:
```python
class MessageBubble(QWidget):
    STYLES = {
        "user": {
            "bg": "#DCF8C6", "align": "right",
            "border-radius": "12px", "padding": "8px 12px",
            "margin-left": "60px"
        },
        "assistant": {
            "bg": "#FFFFFF", "align": "left",
            "border": "1px solid #E0E0E0", "border-radius": "12px",
            "padding": "8px 12px", "margin-right": "60px"
        },
        "system": {
            "bg": "#F5F5F5", "align": "center",
            "font-size": "11px", "color": "#888888",
            "padding": "4px 8px", "border-radius": "4px"
        }
    }

    def __init__(self, text, role, parent=None):
        layout = QHBoxLayout(self)
        label = QLabel(text)
        label.setWordWrap(True)
        label.setMaximumWidth(280)
        label.setTextFormat(Qt.TextFormat.PlainText)
        style = self.STYLES[role]
        if role == "user":
            layout.addStretch()
            layout.addWidget(label)
        elif role == "assistant":
            layout.addWidget(label)
            layout.addStretch()
        else:  # system
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(label)
        label.setStyleSheet(_build_style(style))
```

**测试策略**:
- 单测：渲染 user/assistant/system 三种角色，验证样式类名和布局方向
- 单测：超长文本（500 字符）不溢出，wordWrap 生效

### 步骤 2: 创建 QuickActionsPanel

**涉及文件**: `src/transbridge/ui/tools/smart_assistant/quick_actions.py`（新建）

**实现要点**:
- 4 个按钮纵向排列（QVBoxLayout），等宽，文本居中
- 按钮样式：扁平化，hover 变色
- 点击发射 `action_clicked` 信号，携带指令文本

**边界条件**:
- 面板宽度 < 按钮最小宽度 → 按钮文字被截断，设置 tooltip 显示完整文字

**伪代码**:
```python
class QuickActionsPanel(QWidget):
    ACTIONS = [
        ("翻译选中", "请翻译当前选中的词条"),
        ("质量检查", "请检查当前集合的翻译质量"),
        ("查询术语", "请查询以下术语："),
        ("导出JSON", "请导出当前集合为 JSON"),
    ]

    def __init__(self, parent=None):
        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        for label, prompt in self.ACTIONS:
            btn = QPushButton(label)
            btn.setToolTip(prompt)
            btn.clicked.connect(lambda checked, p=prompt: self.action_clicked.emit(p))
            layout.addWidget(btn)
        layout.addStretch()
```

**测试策略**:
- 手动验证：点击每个按钮，确认 ChatWidget 输入框被填充对应文字

### 步骤 3: 创建 ChatWidget

**涉及文件**: `src/transbridge/ui/tools/smart_assistant/chat_widget.py`（新建）

**实现要点**:
- 消息滚动区：`QScrollArea` 内含 `QVBoxLayout`，消息气泡追加到底部
- 输入框：`QTextEdit`，最大高度 100px
- 按钮行：`[清空对话] [发送]`
- `eventFilter`：捕获 Ctrl+Enter 触发发送
- 方法预留 `add_tool_card()` 和 `add_plan_card()` 占位（Story-03 实现）

**边界条件**:
- 输入框为空或纯空白 → `_on_send` 直接返回
- 输入框粘贴多行文本 → 保留换行，Ctrl+Enter 发送
- 消息滚动区无消息 → 不崩溃（空布局）
- 消息数量 > 100 → 不做分页（Phase 1 内内存足够），后续 Story-05 可加虚拟滚动

**伪代码**:
```python
class ChatWidget(QWidget):
    message_sent = pyqtSignal(str)

    def __init__(self, ctx, parent=None):
        layout = QVBoxLayout(self)
        # 消息滚动区
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._msg_container = QWidget()
        self._msg_layout = QVBoxLayout(self._msg_container)
        self._msg_layout.addStretch()
        self._scroll.setWidget(self._msg_container)
        layout.addWidget(self._scroll, stretch=1)

        # 输入框
        self._input = QTextEdit()
        self._input.setMaximumHeight(100)
        self._input.installEventFilter(self)
        layout.addWidget(self._input)

        # 按钮行
        btn_row = QHBoxLayout()
        clear_btn = QPushButton("清空对话")
        clear_btn.clicked.connect(self._clear_conversation)
        send_btn = QPushButton("发送")
        send_btn.clicked.connect(self._on_send)
        btn_row.addWidget(clear_btn)
        btn_row.addStretch()
        btn_row.addWidget(send_btn)
        layout.addLayout(btn_row)

    def eventFilter(self, obj, event):
        if obj == self._input and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Return and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self._on_send()
                return True
        return super().eventFilter(obj, event)

    def _on_send(self):
        text = self._input.toPlainText().strip()
        if not text:
            return
        self.add_user_bubble(text)
        self._input.clear()
        self.message_sent.emit(text)

    def _clear_conversation(self):
        while self._msg_layout.count() > 1:  # 保留 stretch
            item = self._msg_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
```

**测试策略**:
- 手动验证：输入文本→Ctrl+Enter→确认气泡出现、输入清空
- 手动验证：空输入点发送→无反应
- 手动验证：纯空格输入→无反应
- 手动验证：点击清空对话→所有气泡移除

### 步骤 4: 创建 SmartAssistantPanel

**涉及文件**: `src/transbridge/ui/tools/smart_assistant/panel.py`（新建）

**实现要点**:
- 继承 QDockWidget，设置 `DockWidgetClosable | DockWidgetMovable | DockWidgetFloatable`
- 允许停靠区域：`LeftDockWidgetArea | RightDockWidgetArea`
- 水平布局：`[QuickActionsPanel | QFrame(VLine) | ChatWidget]`
- 最小宽度 360px

**边界条件**:
- 面板关闭（closeEvent）→ 不删除 ChatWidget（保留对话历史），下次 show 时恢复
- 面板浮动时 → 布局保持不变
- 父窗口 resize 到极小 → 面板最小宽度保证可用性

**伪代码**:
```python
class SmartAssistantPanel(QDockWidget):
    visibility_changed = pyqtSignal(bool)

    def __init__(self, ctx, parent=None):
        super().__init__("智能助手", parent)
        self.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea |
            Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable |
            QDockWidget.DockWidgetFeature.DockWidgetMovable |
            QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        self.setMinimumWidth(360)

        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        self._quick_actions = QuickActionsPanel()
        layout.addWidget(self._quick_actions)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.VLine)
        layout.addWidget(line)

        self._chat = ChatWidget(ctx)
        layout.addWidget(self._chat, stretch=1)

        self._quick_actions.action_clicked.connect(self._chat.set_input)

        self.setWidget(container)

    def showEvent(self, event):
        self.visibility_changed.emit(True)
        super().showEvent(event)

    def hideEvent(self, event):
        self.visibility_changed.emit(False)
        super().hideEvent(event)
```

**测试策略**:
- 手动验证：拖拽面板到不同边缘，确认停靠正常
- 手动验证：浮动面板、关闭面板、重新打开，状态正确
- 手动验证：缩小窗口到 < 360px，面板不挤压变形

### 步骤 5: 集成到 MainWindow

**涉及文件**: `src/transbridge/ui/main_window.py`（修改）

**实现要点**:
- `_init_central()` 末尾：创建 SmartAssistantPanel → `addDockWidget(RightDockWidgetArea, panel)` → `panel.hide()`
- `_init_menu()`：小工具菜单新增"智能助手" action → checkable, shortcut Ctrl+Shift+I
- View 菜单新增"智能助手面板" action → checkable
- `_init_shortcuts()`：新增 Ctrl+K 快捷键
- 三个入口统一调用 `_toggle_smart_assistant()`
- `_toggle_smart_assistant()`：切换 panel 可见性，同步两个 menu action 的 checked 状态
- 连接 `panel.visibility_changed` → 同步 menu action checked 状态

**边界条件**:
- panel 尚未初始化时 toggle 被调用 → 延迟初始化（lazy init，`_assistant_panel` 为 None 时先创建）
- 菜单 action checked 状态与面板可见性不同步 → 所有入口统一经过 `_toggle_smart_assistant`，严禁直接操作 panel.show/hide
- 两个快捷键 Ctrl+K 和 Ctrl+Shift+I 都绑定同一个方法 → toggle 语义保证幂等

**伪代码**:
```python
# MainWindow.__init__ 或 _init_central 中
self._assistant_panel = None  # lazy init

def _get_assistant_panel(self):
    if self._assistant_panel is None:
        from src.transbridge.ui.tools.smart_assistant import SmartAssistantPanel
        self._assistant_panel = SmartAssistantPanel(self._ctx, self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._assistant_panel)
        self._assistant_panel.visibility_changed.connect(self._on_assistant_visibility_changed)
        self._assistant_panel.hide()
    return self._assistant_panel

def _toggle_smart_assistant(self):
    panel = self._get_assistant_panel()
    if panel.isVisible():
        panel.hide()
    else:
        panel.show()
        panel.raise_()

def _on_assistant_visibility_changed(self, visible):
    self._smart_assistant_act.setChecked(visible)
    self._view_assistant_act.setChecked(visible)
```

**测试策略**:
- 手动验证：点击菜单"智能助手"→ 面板展开 → 再次点击 → 面板收起
- 手动验证：Ctrl+K → 面板展开 → Ctrl+K → 面板收起
- 手动验证：Ctrl+Shift+I → 面板展开 → 再次 → 面板收起
- 手动验证：面板展开时 View 菜单勾选同步
- 手动验证：关闭面板（点 X）→ View 菜单勾选自动取消
- 手动验证：面板关闭后重新打开 → 对话历史保留

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/ui/tools/smart_assistant/__init__.py` | 新建 | 导出 SmartAssistantPanel |
| `src/transbridge/ui/tools/smart_assistant/panel.py` | 新建 | SmartAssistantPanel (QDockWidget) |
| `src/transbridge/ui/tools/smart_assistant/chat_widget.py` | 新建 | ChatWidget (消息列表 + 输入框) |
| `src/transbridge/ui/tools/smart_assistant/message_bubble.py` | 新建 | MessageBubble (三种气泡样式) |
| `src/transbridge/ui/tools/smart_assistant/quick_actions.py` | 新建 | QuickActionsPanel (4 个快捷按钮) |
| `src/transbridge/ui/main_window.py` | 修改 | 集成 DockWidget + 菜单/快捷键 + lazy init |

## 风险与注意事项

- **QT 样式表继承**: MessageBubble 的 stylesheet 可能被父组件的全局样式覆盖 → 使用 `setObjectName()` + 选择器隔离
- **QTextEdit 高度自适应**: 输入多行时高度自动增长，但需限制最大高度 100px → 重写 `sizeHint()` 或使用 `document().size().height()` 动态调整
- **消息滚动区自动滚底**: 每次添加气泡后需滚动到底部 → `QScrollArea.verticalScrollBar().setValue(scrollbar.maximum())`
- **Ctrl+K 全局冲突**: 可能与系统或其他应用的全局快捷键冲突 → 仅在 MainWindow 获得焦点时生效（Qt 默认行为）
- **面板 X 关闭 vs hide**: 点击面板 X 关闭和调用 hide() 效果一致，对话历史保留在内存中
