# 004: @require_collection 批量替换 + require_collection 装饰器增强

**日期**: 2026-05-12
**类型**: 改
**关联**: Epic: Smart Assistant QA 修复 > Story 06: 代码清理与架构修复

## 修改文件

### `src/transbridge/smart_assistant/tools/base.py` (改)
- **修改内容**: `require_collection` 装饰器错误返回补全 `error_category="input"` 和 `error_code="COLLECTION_NOT_LOADED"`，与其他工具的前置条件检查统一错误格式
- **原因**: 原装饰器返回的错误消息缺少结构化错误分类，LLM 无法区分"无集合"与其他输入错误

### `src/transbridge/smart_assistant/tools/tool_writer.py` (改)
- **修改内容**: 4 个写回函数（`_tool_write_to_esp` / `_tool_write_to_eet` / `_tool_write_to_xt` / `_tool_write_to_strings`）统一添加 `@require_collection` 装饰器；函数签名从 `(args, ctx)` 改为 `(args, ctx, collection)`；移除函数体内的手动 `if not collection or len(collection) == 0: return ToolResult.fail(...)` 检查
- **原因**: 统一集合前置检查为声明式装饰器，消除 4 处重复的样板代码

### `src/transbridge/smart_assistant/tools/tool_paratranz.py` (改)
- **修改内容**: `_tool_compare_with_remote` 和 `_tool_upload_entries` 添加 `@require_collection` 装饰器；签名改为 `(args, ctx, collection)`；移除手动检查
- **原因**: 同上，统一为装饰器模式

### `src/transbridge/smart_assistant/tools/tool_translator.py` (改)
- **修改内容**: `_tool_start_translation` 和 `_tool_start_polish` 添加 `@require_collection` 装饰器；签名改为 `(args, ctx, collection)`；移除手动检查（`_tool_start_translation` 的 C3 API Key 前置检查保留在装饰器之后）
- **原因**: 统一集合检查模式，API Key 检查与集合检查逻辑分层

### 未替换文件说明
- `tool_v1.py`: 已标记 deprecated，LLM 不再调用，无需装饰器改造
- `tool_proofreader.py`: `_run_postprocess_phase` 为工厂函数（5 参数签名），不匹配 `@require_collection` 的 `(args, ctx)` 签名模式，保留原有手动检查
