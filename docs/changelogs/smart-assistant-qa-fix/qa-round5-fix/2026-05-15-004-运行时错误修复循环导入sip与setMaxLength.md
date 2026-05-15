# 004: 运行时错误修复 — 循环导入/sip/setMaxLength/_worker 访问

**日期**: 2026-05-15
**类型**: 改
**关联**: Epic: Smart Assistant QA 修复 > Story-06: 代码清理与架构修复

## 修改文件

### `src/transbridge/smart_assistant/tools/__init__.py` (改)
- **修改内容**: 移除模块级 `from . import tool_editor` 等 7 行副作用导入，保留在 `register_all()` 函数内。原有导入会在 `tool_registry` 加载期间触发 `_register_*_tools()` → 循环导入 `tool_registry`
- **原因**: 应用启动崩溃 — `main.py` → `AgentRegistry` → `tool_registry` → `tools/__init__.py` → `tool_editor._register_editor_tools()` → `import tool_registry` 循环

### `src/transbridge/smart_assistant/tool_registry.py` (改)
- **修改内容**: v1 工具函数导入（`_tool_lookup_terms` 等）从模块级移入 `_register_v1_tools()` 方法内作为 local import
- **原因**: 配合 `tools/__init__.py` 消除循环导入源

### `src/transbridge/ui/app.py` (改)
- **修改内容**: 启动初始化新增 `register_all_tools()` 调用，显式注册所有内置工具（替代原先 `tools/__init__.py` 的模块级副作用导入）
- **原因**: 确保工具在应用使用前完成注册

### `src/transbridge/ui/tools/smart_assistant/thinking_indicator.py` (改)
- **修改内容**: `import sip` 改为 `from PyQt6 import sip`。原独立 `sip` 包未安装，但 PyQt6 内置 `sip` 子模块可用
- **原因**: `ModuleNotFoundError: No module named 'sip'` 导致面板打开即崩溃

### `src/transbridge/ui/main_window.py` (改)
- **修改内容**: `closeEvent` 中 `self._assistant_panel.chat._worker` 访问前加 `hasattr(self._assistant_panel.chat, '_worker')` 守卫
- **原因**: C18/C19 重构后 `ChatWidget._worker` 属性可能未初始化，`closeEvent` 直接访问导致 `AttributeError`

### `src/transbridge/ui/tools/smart_assistant/chat_widget.py` (改)
- **修改内容**: `_init_ui_stage4` 中 `self._input.setMaxLength(10000)` 改为 `self._input.document().setMaximumBlockCount(500)`。`QTextEdit` 没有 `setMaxLength()`（这是 `QLineEdit` 的方法）；4 阶段 `_init_ui` 均添加 stderr 诊断输出
- **原因**: `AttributeError: 'QTextEdit' object has no attribute 'setMaxLength'` 导致 Stage 4 整段跳过，输入框/发送按钮全部未创建
