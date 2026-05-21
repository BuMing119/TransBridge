# 002: 代码级修复 — Blocker/Major 缺陷修复

**日期**: 2026-05-21
**类型**: 改
**关联**: Epic: Agent 工具系统全面扩展 > Story 22: 工具描述全面重写

## 修改文件

### `src/transbridge/smart_assistant/tools/tool_paratranz.py` (改)
- **修改内容**: `_tool_export_artifact()` 函数重写。原实现调用 `client.export_artifact(pid)`，但 `ParatranzProjectAPI` 上不存在该方法（运行时报 `AttributeError`）。现改为导入 `ParatranzExportAPI`（继承自 `ParatranzClient`，独立于 `ParatranzProjectAPI`），调用 `trigger_export(pid)` 触发导出 → 每2秒轮询 `get_artifacts(pid)`（最长30秒）→ 返回最新 artifact 数据或超时时返回 pending 状态。`list_projects` 参数 schema 中 uid 描述修正：`"不传则查看全部"` → `"传 \"my\" 查看我的项目（默认），传 \"\" 查看全部项目"`（与函数默认值 `args.get("uid", "my")` 一致）
- **原因**: export_artifact 为 Blocker 级缺陷——方法不存在，运行必崩溃。list_projects uid 默认值文档与代码不一致

### `src/transbridge/smart_assistant/tools/tool_parser.py` (改)
- **修改内容**: 删除 `_tool_import_strings()` 函数（24行，含不可达代码块）。从 `_PARAM_SCHEMAS` 中移除 `import_strings` 条目。从 `_register_parser_tools()` 注册列表中移除 `import_strings` 工具。从 `_VALID_EXTENSIONS` 集合中移除 `".strings"`（无 parser 能处理该格式，且 `write_back` 使用 `check_extension=False` 不受影响）。Parser 工具数从 7 降为 6
- **原因**: `strings_importer` 模块全项目搜索不存在，工具为永久死桩（始终返回 `"import_strings 暂不可用：strings_importer 模块不存在"`），不应暴露给 LLM

### `src/transbridge/smart_assistant/tools/tool_translator.py` (改)
- **修改内容**: `_PARAM_SCHEMAS["start_polish"]` 中 `scope` 参数描述修正：`"passed(已通过检查,stage=1+)"` → `"passed(已通过检查,stage=1/3/4/5/6)"`。与源码 `_tool_start_polish` 第193行过滤条件 `e.stage in (1, 3, 4, 5, 6)` 一致
- **原因**: 文档描述 `1+` 暗示所有 ≥1 的 stage 值，但实际仅包含 1/3/4/5/6（不含 2/9/-1）

### `src/transbridge/smart_assistant/tools/tool_editor.py` (改)
- **修改内容**: `_tool_select_entries()` 返回数据新增 `selected_ids` 字段（`list[str]`）。通过 `ctx.selected_ids` 属性读取当前已选条目 ID 集合（`set` → `list` 转换），与原有 `selected_count` 并列返回。使用 `hasattr` 做防御性检查
- **原因**: QA 报告标记为 Major——选中状态对 LLM 是纯"只写"（可写入但无法读取已选条目列表）。`ctx.selected_ids` 属性已在 `context.py:165` 存在，仅需在工具层暴露
