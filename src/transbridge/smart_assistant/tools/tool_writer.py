"""P2 文件写回工具 — 将译文写回到源文件 (writer namespace)。

Story 12: 4个writer工具，permission=admin, require_confirmation=true。
"""
from __future__ import annotations

import os
from .base import ToolResult, require_collection


def _validate_output_path(path: str) -> ToolResult | None:
    """C6: 写回路径安全校验 — 拒绝遍历路径和绝对路径。"""
    if not path:
        return ToolResult.fail("输出路径为空")
    if ".." in path.replace("\\", "/").split("/"):
        return ToolResult.fail("拒绝路径遍历攻击")
    if os.path.isabs(path):
        return ToolResult.fail("拒绝绝对路径，请使用相对路径")
    return None


@require_collection
def _tool_write_to_esp(args: dict, ctx, collection) -> ToolResult:
    """写回译文到 ESP/ESM 文件。"""
    slot = ctx.active_slot
    if slot is None:
        return ToolResult.fail("没有活跃的集合槽位")
    path = args.get("path") or ctx.esp_path
    if path:
        err = _validate_output_path(path)
        if err: return err
    try:
        from src.transbridge.writer.plugin_writer import PluginWriter
        plugin = slot.plugin
        if plugin is None:
            return ToolResult.fail("当前槽位无已解析的插件")
        writer = PluginWriter(plugin, strings_lookup=slot.strings_lookup, language=slot.strings_lang or "english")
        count = writer.apply_collection(collection)
        if path:
            writer.write(path)
        return ToolResult.ok(f"已写回 {count} 条译文到 ESP", data={"written_count": count, "path": path})
    except Exception as exc:
        return ToolResult.fail(f"ESP 写回失败: {exc}")


@require_collection
def _tool_write_to_eet(args: dict, ctx, collection) -> ToolResult:
    """写回译文到 EET XML 文件。M8: 独立 EET 写回路径。"""
    path = args.get("path") or getattr(ctx, 'eet_path', None)
    if not path:
        return ToolResult.fail("请提供 EET 输出路径或先解析 EET 源文件")
    err = _validate_output_path(path)
    if err: return err
    try:
        from src.transbridge.writer.eet_xml_writer import EETWriter
        writer = EETWriter()
        writer.write(collection, path)
        return ToolResult.ok(f"已写回译文到 EET XML", data={"path": path})
    except Exception as exc:
        return ToolResult.fail(f"EET 写回失败: {exc}")


@require_collection
def _tool_write_to_xt(args: dict, ctx, collection) -> ToolResult:
    """写回译文到 XT XML 文件。M8: 独立 XT 写回路径。"""
    path = args.get("path") or getattr(ctx, 'xt_path', None)
    if not path:
        return ToolResult.fail("请提供 XT 输出路径或先解析 XT 源文件")
    err = _validate_output_path(path)
    if err: return err
    try:
        from src.transbridge.writer.xt_xml_writer import XTWriter
        writer = XTWriter()
        writer.write(collection, path)
        return ToolResult.ok(f"已写回译文到 XT XML", data={"path": path})
    except Exception as exc:
        return ToolResult.fail(f"XT 写回失败: {exc}")


@require_collection
def _tool_write_to_strings(args: dict, ctx, collection) -> ToolResult:
    """写回译文到 .strings 文件。"""
    slot = ctx.active_slot
    if slot is None:
        return ToolResult.fail("没有活跃的集合槽位")
    path = args.get("path") or args.get("output_dir")
    if path:
        err = _validate_output_path(path)
        if err: return err
    try:
        from src.transbridge.writer.plugin_writer import PluginWriter
        plugin = slot.plugin
        if plugin is None:
            return ToolResult.fail("当前槽位无已解析的插件")
        writer = PluginWriter(plugin, strings_lookup=slot.strings_lookup, language=slot.strings_lang or "english")
        count = writer.apply_collection(collection)
        result = writer.write(None)
        strings_written = result.get("strings_written", []) if isinstance(result, dict) else []
        return ToolResult.ok(
            f"已写回 {count} 条译文到 {len(strings_written)} 个 strings 文件",
            data={"written_count": count, "strings_files": len(strings_written)},
        )
    except Exception as exc:
        return ToolResult.fail(f"strings 写回失败: {exc}")


# ── 注册 ──────────────────────────────────────────────────────

def _register_writer_tools():
    from src.transbridge.smart_assistant.tool_registry import ToolRegistry, ToolSpec

    # m10: 统一使用 5 元组格式 (name, display_name, description, execute, permission)
    tools = [
        ("write_to_esp", "写回ESP", "将译文写回到ESP/ESM插件文件", _tool_write_to_esp, "admin"),
        ("write_to_eet", "写回EET", "将译文写回到EET XML文件", _tool_write_to_eet, "admin"),
        ("write_to_xt", "写回XT", "将译文写回到XT XML文件", _tool_write_to_xt, "admin"),
        ("write_to_strings", "写回Strings", "将译文写回到.strings本地化文件", _tool_write_to_strings, "admin"),
    ]

    for name, display_name, description, execute, permission in tools:
        ToolRegistry.register(ToolSpec(
            name=name, display_name=display_name, description=description,
            parameters={}, execute=execute, permission=permission,
            require_confirmation=True,  # admin 级操作必须确认
            is_long_running=True,
        ), namespace="writer")


_register_writer_tools()
