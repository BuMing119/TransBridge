"""P2 文件写回工具 — 将译文写回到源文件 (writer namespace)。

Story 12: 4个writer工具，permission=admin, require_confirmation=true。
Story 19: write_back 合并 4→1，dispatch 表路由，4 实现改为 _impl。
"""

from __future__ import annotations

from .base import ToolResult, require_collection
from .tool_parser import _validate_path  # M29: 复用共享路径校验器


def _create_plugin_writer(slot):
    """共享工厂：从槽位创建 PluginWriter 实例。

    返回 (writer, None) 或 (None, ToolResult) 表示失败。
    """
    from transbridge.writer.plugin_writer import PluginWriter

    plugin = slot.plugin
    if plugin is None:
        return None, ToolResult.fail("当前槽位无已解析的插件")
    writer = PluginWriter(
        plugin,
        strings_lookup=slot.strings_lookup,
        language=slot.strings_lang or "english",
    )
    return writer, None


# ── Story 19: 写回实现 (_impl) ──────────────────────────────────


def _write_to_esp_impl(slot, collection, path):
    """写回译文到 ESP/ESM 文件。"""
    try:
        writer, err = _create_plugin_writer(slot)
        if err:
            return err
        count = writer.apply_collection(collection)
        if path:
            writer.write(path)
        return ToolResult.ok(f"已写回 {count} 条译文到 ESP", data={"written_count": count, "path": path})
    except Exception as exc:
        return ToolResult.fail(f"ESP 写回失败: {exc}")


def _write_to_eet_impl(collection, path):
    """写回译文到 EET XML 文件。"""
    try:
        from transbridge.writer.eet_xml_writer import EETWriter

        writer = EETWriter()
        writer.write(collection, path)
        return ToolResult.ok("已写回译文到 EET XML", data={"path": path})
    except Exception as exc:
        return ToolResult.fail(f"EET 写回失败: {exc}")


def _write_to_xt_impl(collection, path):
    """写回译文到 XT XML 文件。"""
    try:
        from transbridge.writer.xt_xml_writer import XTWriter

        writer = XTWriter()
        writer.write(collection, path)
        return ToolResult.ok("已写回译文到 XT XML", data={"path": path})
    except Exception as exc:
        return ToolResult.fail(f"XT 写回失败: {exc}")


def _write_to_strings_impl(slot, collection, path):
    """写回译文到 .strings 文件。"""
    try:
        writer, err = _create_plugin_writer(slot)
        if err:
            return err
        count = writer.apply_collection(collection)
        result = writer.write(path)
        strings_written = result.get("strings_written", []) if isinstance(result, dict) else []
        return ToolResult.ok(
            f"已写回 {count} 条译文到 {len(strings_written)} 个 strings 文件",
            data={"written_count": count, "strings_files": len(strings_written)},
        )
    except Exception as exc:
        return ToolResult.fail(f"strings 写回失败: {exc}")


# ── Story 19: Dispatch 表与统一入口 ─────────────────────────────

_WRITE_HANDLERS = {
    "esp": _write_to_esp_impl,
    "eet": _write_to_eet_impl,
    "xt": _write_to_xt_impl,
    "strings": _write_to_strings_impl,
}

_TARGET_INFERENCE = {
    "esp": "有已解析的 ESP 插件",
    "eet": "有已解析的 EET 文件 (ctx.eet_path 非空)",
    "xt": "有已解析的 XT 文件 (ctx.xt_path 非空)",
    "strings": "仅需导出 .strings 本地化文件",
}


@require_collection
def _tool_write_back(args: dict, ctx, collection) -> ToolResult:
    """Story 19: 统一写回入口，dispatch 表路由 target → 对应 _impl。"""
    target = args.get("target", "").lower()
    if target not in _WRITE_HANDLERS:
        valid = ", ".join(_WRITE_HANDLERS.keys())
        return ToolResult.fail(f"无效的 target: {target}，可选: {valid}")

    path = args.get("path")
    slot = ctx.active_slot
    if slot is None and target in ("esp", "strings"):
        return ToolResult.fail("没有活跃的集合槽位")

    # target 推断与默认路径
    if target == "esp":
        path = path or getattr(ctx, "esp_path", None)
    elif target == "eet":
        path = path or getattr(ctx, "eet_path", None)
        if not path:
            return ToolResult.fail("请提供 EET 输出路径或先解析 EET 源文件")
    elif target == "xt":
        path = path or getattr(ctx, "xt_path", None)
        if not path:
            return ToolResult.fail("请提供 XT 输出路径或先解析 XT 源文件")
    elif target == "strings":
        path = path or args.get("output_dir")
        if not path:
            return ToolResult.fail("请提供输出路径 (path 或 output_dir)")

    if path:
        err = _validate_path(path, check_exists=False, check_extension=False)
        if err:
            return err

    handler = _WRITE_HANDLERS[target]
    if target in ("esp", "strings"):
        return handler(slot, collection, path)
    else:
        return handler(collection, path)


# ── 参数 Schema ────────────────────────────────────────────────

_PARAM_SCHEMAS = {
    "write_back": {
        "target": {
            "type": "str",
            "required": True,
            "description": "Write-back target: esp/eet/xt/strings. Inference: ESP→esp, EET→eet, XT→xt",
        },
        "path": {
            "type": "str",
            "required": False,
            "description": "Output path; defaults to the currently parsed source path",
        },
    },
}

# ── 注册 ──────────────────────────────────────────────────────


def _register_writer_tools():
    from ..tool_registry import ToolRegistry

    ToolRegistry.register_tools(
        "writer",
        [
            {
                "name": "write_back",
                "display_name": "写回译文",
                "description": (
                    "①Write translations back to a source file. ②Arguments: target (required: esp/eet/xt/strings), "
                    "optional path (defaults to the parsed source path), and output_dir for strings only. ③Returns "
                    "esp→{written_count,path}, strings→{written_count,strings_files}, "
                    "eet/xt→{path}. Rules: requires user "
                    "confirmation and admin permission; long-running; esp/strings require "
                    "parse_esp first; eet/xt require "
                    "parse_eet/parse_xt first; path must be normalized within a "
                    "RuntimeContext authorized root; inspect "
                    "esp_file/eet_file/xt_file with get_app_state for target inference."
                ),
                "execute": _tool_write_back,
                "permission": "admin",
                "require_confirmation": True,
                "is_long_running": True,
                "parameters": _PARAM_SCHEMAS.get("write_back", {}),
            },
        ],
    )


_register_writer_tools()
