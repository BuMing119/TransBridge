# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""在当前工程登记翻译来源，或向现有来源导入译文。"""

from __future__ import annotations

import os

from .base import ToolResult
from .diagnostics import technical_error

_VALID_EXTENSIONS = {".esp", ".esm", ".esl", ".xml", ".eet", ".json", ".sst"}  # E1 + C4


def _sanitize_error(msg: str, path: str) -> str:
    """M17: 将错误信息中的完整路径替换为文件名，避免泄露路径信息。"""
    return msg.replace(path, os.path.basename(path)) if path and path in msg else msg


def _validate_path(path: str) -> ToolResult | None:
    """Check the authorized parser input against the supported file extensions."""
    if not path:
        return ToolResult.fail("文件路径为空")
    if not os.path.exists(path):
        return ToolResult.fail(f"文件不存在: {os.path.basename(path)}")  # M17: 仅显示文件名
    ext = os.path.splitext(path)[1].lower()
    if ext not in _VALID_EXTENSIONS:
        return ToolResult.fail(f"不支持的文件类型: {ext}，允许: {sorted(_VALID_EXTENSIONS)}")
    return None


def _parse_file(file_type: str, args: dict, ctx) -> ToolResult:
    from ._source_import import run_file_operation

    path = args.get("path", "")
    if not path:
        return ToolResult.fail("文件路径为空")
    try:
        from .file_input_access import authorized_input_path

        resolved = authorized_input_path(ctx, path, plugin=file_type == "esp")
        error = _validate_path(str(resolved))
        if error:
            return error
        return run_file_operation(file_type, str(resolved), args, ctx)
    except Exception as exc:
        return ToolResult.fail(f"文件操作失败：{_sanitize_error(technical_error(exc), path)}")


# ── Parser 工具函数 ──────────────────────────────────────────────


def _tool_parse_esp(args: dict, ctx) -> ToolResult:
    return _parse_file("esp", args, ctx)


def _tool_parse_eet(args: dict, ctx) -> ToolResult:
    return _parse_file("eet", args, ctx)


def _tool_parse_xt(args: dict, ctx) -> ToolResult:
    return _parse_file("xt", args, ctx)


def _tool_parse_sst(args: dict, ctx) -> ToolResult:
    return _parse_file("sst", args, ctx)


def _tool_import_json(args: dict, ctx) -> ToolResult:
    return _parse_file("json", args, ctx)


# ── 注册 ──────────────────────────────────────────────────────

_IMPORT_ACTION = {
    "type": "str",
    "required": False,
    "description": (
        "create_slot (default) registers the file as a source in the current project. "
        "append imports translation results for matching entries of the active source."
    ),
}
_IMPORT_RECEIPT = (
    "③Registration returns label, entry_count and activated. "
    "Import returns added_count (entries updated) and total_count."
)

_REPLACE_EXISTING = {
    "type": "bool",
    "required": False,
    "description": "For append only. Replace existing translations; default false fills empty untranslated entries.",
}

_PARAM_SCHEMAS = {
    "parse_esp": {
        "path": {"type": "str", "required": True, "description": "Path to the ESP/ESM/ESL plugin"},
        "skip_empty": {
            "type": "bool",
            "required": False,
            "description": "For plugin registration only. Skip empty originals (default true).",
        },
        "action": _IMPORT_ACTION,
        "replace_existing": _REPLACE_EXISTING,
    },
    "parse_eet": {
        "path": {"type": "str", "required": True, "description": "Path to EET v3 binary or ESP-ESM Translator XML"},
        "action": _IMPORT_ACTION,
        "replace_existing": _REPLACE_EXISTING,
    },
    "parse_xt": {
        "path": {"type": "str", "required": True, "description": "Path to the XT XML file"},
        "action": _IMPORT_ACTION,
        "replace_existing": _REPLACE_EXISTING,
    },
    "parse_sst": {
        "path": {"type": "str", "required": True, "description": "Path to the SST binary file"},
        "action": _IMPORT_ACTION,
        "replace_existing": _REPLACE_EXISTING,
    },
    "import_json": {
        "path": {"type": "str", "required": True, "description": "Path to the JSON file"},
        "format": {
            "type": "str",
            "required": False,
            "description": "auto/paratranz/transbridge/dsd; ambiguous JSON requires an explicit choice",
        },
        "action": _IMPORT_ACTION,
        "replace_existing": _REPLACE_EXISTING,
    },
}


def _register_parser_tools():
    from ..tool_registry import ToolRegistry

    ToolRegistry.register_tools(
        "parser",
        [
            {
                "name": "parse_esp",
                "display_name": "解析ESP",
                "description": (
                    "①Parse an ESP/ESM/ESL plugin as a project source or import matching translations. "
                    "②Pass an authorized path and action=create_slot/append. "
                    "Registered sources retain the snapshot for plugin or Strings write-back. " + _IMPORT_RECEIPT
                ),
                "execute": _tool_parse_esp,
                "parameters": _PARAM_SCHEMAS.get("parse_esp", {}),
                "permission": "write",
            },
            {
                "name": "parse_eet",
                "display_name": "解析 ESP-ESM Translator 文件",
                "description": (
                    "①Read experimental EET v3 or ESP-ESM Translator XML, detected by content. "
                    "②Pass an authorized path and action=create_slot/append. Registration preserves the native "
                    "write-back format. Binary translation imports require a unique record/type/original match. "
                    + _IMPORT_RECEIPT
                ),
                "execute": _tool_parse_eet,
                "parameters": _PARAM_SCHEMAS.get("parse_eet", {}),
                "permission": "write",
            },
            {
                "name": "parse_xt",
                "display_name": "解析 xTranslator XML",
                "description": (
                    "①Read xTranslator XML as a project source or import matching translations. "
                    "②Pass an authorized path and action=create_slot/append. Registered sources retain their "
                    "snapshot for native XML write-back. " + _IMPORT_RECEIPT
                ),
                "execute": _tool_parse_xt,
                "parameters": _PARAM_SCHEMAS.get("parse_xt", {}),
                "permission": "write",
            },
            {
                "name": "parse_sst",
                "display_name": "解析 xTranslator SST",
                "description": (
                    "①Read SSU8/SSU9 SST dictionaries with their original and translated text. "
                    "②Pass an authorized path and action=create_slot/append. Experimental native write-back "
                    "requires a current source snapshot. Translation imports require unique matches. " + _IMPORT_RECEIPT
                ),
                "execute": _tool_parse_sst,
                "parameters": _PARAM_SCHEMAS.get("parse_sst", {}),
                "permission": "write",
            },
            {
                "name": "import_json",
                "display_name": "导入JSON",
                "description": (
                    "①Register a ParaTranz or DSD JSON source in the current project. For translation import, "
                    "append also accepts TransBridge JSON. ②Pass an authorized path, action=create_slot/append "
                    "and format=auto/paratranz/transbridge/dsd. Specify format for ambiguous JSON. " + _IMPORT_RECEIPT
                ),
                "execute": _tool_import_json,
                "parameters": _PARAM_SCHEMAS.get("import_json", {}),
                "permission": "write",
            },
        ],
    )


_register_parser_tools()
