# 004: Story 02 编码 — SessionListWidget UI

**日期**: 2026-08-05
**类型**: 增
**关联**: Epic: Session 管理系统 > Story 02: SessionListWidget UI

## 修改文件

### `src/transbridge/ui/tools/smart_assistant/session_list_widget.py` (增)
- **修改内容**: 新建 SessionListWidget(QWidget) + _SessionRow(QFrame) 两个类（~220行）。_SessionRow：单行会话条目，显示名称(粗体)+消息数+时间(今天/日期格式)，hover 时显示"×"删除按钮(QPushButton, 22px圆形)，mousePressEvent 发射 clicked(sid) 信号，当前活跃高亮 #E3F2FD 背景+ #90CAF9 左边框。SessionListWidget：头部栏(标题"会话" + "+"新建按钮 + "◀/▶"折叠按钮)，QScrollArea 会话列表容器，set_sessions(list[dict]) 重建全部行，set_active(sid) 更新高亮，set_collapsed(bool) 折叠切换(40px↔280px)。3 个 pyqtSignal：create_session(name)/switch_session(sid)/delete_session(sid)。配色与 ChatWidget 颜色面板一致（#fafafa/#e0e0e0/#333/#888/#E3F2FD/#D32F2F）。
- **原因**: ADR-008 D17：纯 UI 组件，通过回调与 Panel 通信，不直接依赖 SessionManager。数据通过 set_sessions() 注入。
