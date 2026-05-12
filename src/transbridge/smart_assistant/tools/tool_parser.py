"""P2 文件解析工具 — 将翻译文件加载到 AppContext (parser namespace)。

Story 12 v2: 权限 write→read(H6), 文件扩展名白名单(E1)。
"""
from __future__ import annotations

import os
from .base import ToolResult

_VALID_EXTENSIONS = {".esp", ".esm", ".esl", ".xml", ".json", ".strings", ".sst"}  # E1 + C4


def _validate_path(path: str) -> ToolResult | None:
    """E1: 校验文件路径（扩展名白名单 + 基础安全检查）。"""
    if not path:
        return ToolResult.fail("文件路径为空")
    if not os.path.exists(path):
        return ToolResult.fail(f"文件不存在: {path}")
    ext = os.path.splitext(path)[1].lower()
    if ext not in _VALID_EXTENSIONS:
        return ToolResult.fail(f"不支持的文件类型: {ext}，允许: {sorted(_VALID_EXTENSIONS)}")
    # 拒绝路径遍历
    if ".." in path.replace("\\", "/").split("/"):
        return ToolResult.fail("拒绝路径遍历攻击")
    # M4: 拒绝绝对路径
    if os.path.isabs(path):
        return ToolResult.fail("不允许使用绝对路径")
    return None


def _tool_parse_esp(args: dict, ctx) -> ToolResult:
    """解析 ESP/ESM 插件文件。"""
    path = args.get("path", "")
    if not path:
        return ToolResult.fail("请提供 ESP 文件路径")
    err = _validate_path(path)
    if err: return err
    try:
        from src.transbridge.parser.plugin_parser import PluginParser
        parser = PluginParser()
        plugin = parser.parse(path)
        # 将解析结果加载到 ctx（通过新的 slot）
        return ToolResult.ok(f"已解析 ESP: {path}", data={"entry_count": len(plugin.entries) if hasattr(plugin, 'entries') else 0})
    except Exception as exc:
        return ToolResult.fail(f"解析 ESP 失败: {exc}")


def _tool_parse_eet(args: dict, ctx) -> ToolResult:
    """解析 EET XML 文件。"""
    path = args.get("path", "")
    if not path: return ToolResult.fail("请提供 EET 文件路径")
    err = _validate_path(path)
    if err: return err
    try:
        from src.transbridge.parser.eet_xml_parser import EET_XmlParser
        parser = EET_XmlParser()
        result = parser.parse(path)
        return ToolResult.ok(f"已解析 EET: {path}", data={"entry_count": len(result) if result else 0})
    except Exception as exc:
        return ToolResult.fail(f"解析 EET 失败: {exc}")


def _tool_parse_xt(args: dict, ctx) -> ToolResult:
    """解析 XT XML 文件。"""
    path = args.get("path", "")
    if not path: return ToolResult.fail("请提供 XT 文件路径")
    err = _validate_path(path)
    if err: return err
    try:
        from src.transbridge.parser.xt_xml_parser import XT_XmlParser
        parser = XT_XmlParser()
        result = parser.parse(path)
        return ToolResult.ok(f"已解析 XT: {path}", data={"entry_count": len(result) if result else 0})
    except Exception as exc:
        return ToolResult.fail(f"解析 XT 失败: {exc}")


def _tool_parse_sst(args: dict, ctx) -> ToolResult:
    """解析 SST 二进制文件。"""
    path = args.get("path", "")
    if not path: return ToolResult.fail("请提供 SST 文件路径")
    err = _validate_path(path)  # C4: SST 与其他解析器统一路径校验
    if err: return err
    try:
        from src.transbridge.parser.sst_parser import SST_Parser
        parser = SST_Parser()
        result = parser.parse(path)
        return ToolResult.ok(f"已解析 SST: {path}", data={"entry_count": len(result) if result else 0})
    except Exception as exc:
        return ToolResult.fail(f"解析 SST 失败: {exc}")


def _tool_import_json(args: dict, ctx) -> ToolResult:
    """从 JSON 文件导入翻译集合。"""
    path = args.get("path", "")
    if not path: return ToolResult.fail("请提供 JSON 文件路径")
    err = _validate_path(path)
    if err: return err
    try:
        from src.transbridge.converter.translation_entry_collection import TranslationEntryCollection
        col = TranslationEntryCollection.from_json_file(path)
        return ToolResult.ok(f"已从 JSON 导入 {len(col)} 条条目", data={"entry_count": len(col)})
    except Exception as exc:
        return ToolResult.fail(f"导入 JSON 失败: {exc}")


def _tool_import_strings(args: dict, ctx) -> ToolResult:
    """从 .strings 文件导入翻译。"""
    path = args.get("path", "")
    if not path: return ToolResult.fail("请提供 strings 文件路径")
    err = _validate_path(path)
    if err: return err
    try:
        from src.transbridge.parser.strings_importer import StringsImporter
        importer = StringsImporter()
        result = importer.import_file(path)
        return ToolResult.ok(f"已从 strings 导入 {len(result) if result else 0} 条", data={"entry_count": len(result) if result else 0})
    except Exception as exc:
        return ToolResult.fail(f"导入 strings 失败: {exc}")


# ── 注册 ──────────────────────────────────────────────────────

_PARAM_SCHEMAS = {
    "parse_esp": {
        "path": {"type": "str", "required": True, "description": "ESP/ESM 文件路径"},
    },
    "parse_eet": {
        "path": {"type": "str", "required": True, "description": "EET XML 文件路径"},
    },
    "parse_xt": {
        "path": {"type": "str", "required": True, "description": "XT XML 文件路径"},
    },
    "parse_sst": {
        "path": {"type": "str", "required": True, "description": "SST 二进制文件路径"},
    },
    "import_json": {
        "path": {"type": "str", "required": True, "description": "JSON 文件路径"},
    },
    "import_strings": {
        "path": {"type": "str", "required": True, "description": ".strings 文件路径"},
    },
}


def _register_parser_tools():
    from src.transbridge.smart_assistant.tool_registry import ToolRegistry, ToolSpec

    # m5: 统一 5 元组格式 (name, display_name, description, execute, permission)
    tools = [
        ("parse_esp", "解析ESP", "解析 ESP/ESM 插件文件，提取翻译条目", _tool_parse_esp, "read"),
        ("parse_eet", "解析EET", "解析 EET XML 翻译文件", _tool_parse_eet, "read"),
        ("parse_xt", "解析XT", "解析 XT XML 翻译文件", _tool_parse_xt, "read"),
        ("parse_sst", "解析SST", "解析 SST 二进制翻译文件", _tool_parse_sst, "read"),
        ("import_json", "导入JSON", "从 JSON 文件导入翻译集合", _tool_import_json, "read"),
        ("import_strings", "导入Strings", "从 .strings 文件导入翻译", _tool_import_strings, "read"),
    ]

    for name, display_name, description, execute, permission in tools:
        ToolRegistry.register(ToolSpec(
            name=name, display_name=display_name,
            description=description,
            parameters=_PARAM_SCHEMAS.get(name, {}),
            execute=execute, permission=permission,
        ), namespace="parser")


_register_parser_tools()
