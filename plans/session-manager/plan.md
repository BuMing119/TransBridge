# Session 管理系统 — 多会话支持

**对应需求**: FR13
**技术模块**: backend (smart_assistant) + UI (smart_assistant panel)
**业务域**: AI 助手会话管理
**状态**: ✅ 全部完成（3/3）
**创建日期**: 2026-08-05

## 功能边界

### 范围内
- SessionManager 后端组件：会话 CRUD + JSON 持久化 + 目录扫描 + 懒加载
- ConversationManager 序列化：`to_dict()` / `from_dict()`
- SessionListWidget UI：左侧可折叠会话列表栏（新建/切换/删除/高亮）
- Panel 协调会话切换：保存当前 → 清空 → 加载目标 → 渲染历史
- 自动保存：每轮 LLM 对话后保存当前会话
- 启动恢复：自动打开上次活跃会话并恢复消息列表
- `data/sessions/` 目录自动创建

### 范围外
- 会话导出/导入、重命名、搜索/过滤、归档/收藏
- 云端同步
- 与 FR12 SessionController 的深度集成（FR13 仅做基本的状态重置）

## Story 清单

### Story 01: SessionManager 后端 + 序列化

**归属**: session-manager（新建）

**验收标准**:
- [ ] `SessionManager` 类实现：`create_session(name, project_name) → session_id` / `delete_session(session_id)` / `list_sessions() → list[SessionMeta]` / `get_session(session_id) → dict` / `save_session(session_id, messages)` / `get_last_active() → str | None`
- [ ] 存储目录 `data/sessions/` 自动创建（不存在时）
- [ ] 启动时目录扫描加载全部会话元数据（name, created_at, last_active_at, project_name, message_count）到内存缓存
- [ ] 消息列表懒加载：仅在 `get_session()` 时读取完整 JSON
- [ ] `save_session()` 使用原子写入（先写临时文件，再 `os.replace`）
- [ ] `ConversationManager.to_dict()` 返回 `{"messages": [{"role":..., "content":...}, ...]}`
- [ ] `ConversationManager.from_dict(data)` 替换消息列表并重置轮次索引
- [ ] 异常处理：损坏 JSON 文件跳过 + 日志警告；磁盘不足时静默失败不崩溃
- [ ] `__init__.py` 新增 `SessionManager` 懒加载映射
- [ ] 35 个新增单元测试通过

> 详细实现指南见 `plans/session-manager/stories/story-01-backend.md`（由 `/bm-story` 展开后生成）

### Story 02: SessionListWidget UI

**归属**: session-manager（新建）

**验收标准**:
- [ ] `SessionListWidget(QWidget)` 类实现：左侧可折叠会话列表栏
- [ ] 会话行渲染：名称（粗体）+ 消息数 + 最后活跃时间（灰色小字），当前会话高亮（`#E3F2FD` 背景）
- [ ] 顶部"+"新建按钮：弹出 `QInputDialog.getText` 命名 → 发出 `on_create_session(name)` 信号
- [ ] 每行悬停显示"×"删除按钮：`QMessageBox.question` 确认 → 发出 `on_delete_session(session_id)` 信号
- [ ] 点击行切换：发出 `on_switch_session(session_id)` 信号
- [ ] 折叠/展开切换按钮（`◀` / `▶` 箭头图标）
- [ ] 使用 QScrollArea 支持长列表滚动
- [ ] 样式与 ChatWidget 配色一致（引用 ChatWidget 顶部颜色面板注释中的色值）

> 详细实现指南见 `plans/session-manager/stories/story-02-ui.md`（由 `/bm-story` 展开后生成）

### Story 03: Panel 集成 + ChatWidget 会话切换

**归属**: session-manager（新建）

**验收标准**:
- [ ] Panel 创建 SessionManager 实例，传入 `get_data_dir()` 作为存储路径
- [ ] Panel 布局改为水平分割：左侧 SessionListWidget + 右侧 ChatWidget（QSplitter）
- [ ] 切换会话流程完整实现：
  - 保存当前会话 → `chat_widget.save_current_session()` → `conv.to_dict()` → `session_manager.save_session()`
  - 加载目标会话 → `session_manager.get_session()` → `conv.clear()` → `conv.from_dict()` → 渲染历史 MessageBubble → `session_controller.handle_abort()`
- [ ] `ChatWidget.save_current_session()` 方法：获取当前消息列表并委托 SessionManager 保存
- [ ] `ChatWidget.load_session(session_id)` 方法：从 SessionManager 加载并重建 UI
- [ ] `ChatWidget.render_history(messages)` 方法：遍历消息列表，按 role 渲染 user/assistant bubble
- [ ] 自动保存：在 `ConversationOrchestrator._on_finished()` 末尾触发保存
- [ ] 启动恢复：Panel 初始化时调用 `session_manager.get_last_active()`，若存在则加载
- [ ] 首个会话自动创建：若 `list_sessions()` 为空，自动创建默认会话
- [ ] 161 现有测试零回归 + 新增集成测试通过

> 详细实现指南见 `plans/session-manager/stories/story-03-integration.md`（由 `/bm-story` 展开后生成）

## 架构依赖

- **ADR-008** (D13-D17): SessionManager 分层/JSON 存储/序列化接口/切换流程/UI 契约
- **ADR-006**: JSON 持久化惯例参考（current.json、memory_metadata.json）

## 风险与回退方案

| 风险 | 缓解 | 回退 |
|------|------|------|
| 会话切换时消息渲染性能（长对话 100+ 轮） | S03 渲染时限制最多显示最近 50 轮，超出部分折叠 | 删除 `data/sessions/` 目录即可回到单会话模式 |
| 原子写入失败导致数据丢失 | 保留内存中的 ConversationManager 副本，仅磁盘写入失败时日志警告 | 手动备份 `data/sessions/` |
| Panel 布局改动影响现有 UI | QSplitter 默认比例 1:3（列表:聊天），用户可拖拽调整 | 恢复 Panel 原布局只需删除 SessionListWidget |
