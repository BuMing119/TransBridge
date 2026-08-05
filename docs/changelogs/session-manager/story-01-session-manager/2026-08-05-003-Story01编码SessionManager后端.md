# 003: Story 01 编码 — SessionManager 后端 + 序列化

**日期**: 2026-08-05
**类型**: 增/改
**关联**: Epic: Session 管理系统 > Story 01: SessionManager 后端 + 序列化

## 修改文件

### `src/transbridge/smart_assistant/session_manager.py` (增)
- **修改内容**: 新建 SessionManager 类（~140行）。实现 create_session(name, project_name)→session_id（UUID12，自动命名）、delete_session(session_id)→bool（清理文件+缓存）、list_sessions()→list[dict]（按 last_active_at+created_at 降序）、get_session(session_id)→dict|None（完整数据含消息列表，更新活跃时间）、save_session(session_id, messages)→bool（原子写入：tmp+os.replace）、get_last_active()→str|None。内部方法：_path_for/_atomic_write/_scan（启动时目录扫描，损坏JSON跳过+日志警告，缺失session_id用文件名回退）。零新依赖，纯 Python + json + os。
- **原因**: ADR-008 D13-D14：独立后端组件，JSON 文件存储 + 目录扫描 + 懒加载。全局 data/sessions/ 目录。

### `src/transbridge/smart_assistant/conversation_manager.py` (改)
- **修改内容**: 新增 to_dict()→dict（返回 {"messages": [...]}，调用 get_messages() 获取副本）和 from_dict(data)→None（替换 _messages 列表、清空 _turn_starts、遍历消息重建轮次索引）
- **原因**: ADR-008 D15：ConversationManager 负责消息列表的序列化，SessionManager 调用它获取/恢复消息数据。

### `src/transbridge/smart_assistant/__init__.py` (改)
- **修改内容**: __all__ 新增 "SessionManager"；_SYMBOL_MODULES 新增 "SessionManager": ".session_manager" 懒加载映射
- **原因**: 遵循 ADR-008 惰性加载模式。

### `tests/smart_assistant/test_session_manager.py` (增)
- **修改内容**: 新建测试文件（~180行，24 测试）。3 个测试类：TestSessionManagerCRUD（15 测试：create/auto_name/persist/sort/get/update_active/nonexistent/delete_file/delete_nonexistent/message_count/save_nonexistent/last_active/last_active_empty）+ TestSessionManagerPersistence（4 测试：scan_restore/lazy_load/corrupted_skip/missing_id_fallback）+ TestConversationManagerSerialization（5 测试：to_dict/preserve_roles/from_dict_replaces/from_dict_empty/rebuild_turn_starts/roundtrip）
- **原因**: 确保 CRUD、持久化恢复、异常处理、序列化往返全部覆盖。
