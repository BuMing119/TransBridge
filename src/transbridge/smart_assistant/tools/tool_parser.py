"""P2 文件解析工具 — 将翻译文件加载到 AppContext (parser namespace)。

Story 12 v2: 权限 write→read(H6), 文件扩展名白名单(E1)。
Story 24: 副作用补全 — action 参数 (create_slot/append) + HITL 确认。
"""
from __future__ import annotations

import os
from pathlib import Path
from .base import ToolResult

_VALID_EXTENSIONS = {".esp", ".esm", ".esl", ".xml", ".json", ".sst"}  # E1 + C4


def _sanitize_error(msg: str, path: str) -> str:
    """M17: 将错误信息中的完整路径替换为文件名，避免泄露路径信息。"""
    return msg.replace(path, os.path.basename(path)) if path and path in msg else msg


def _validate_path(path: str, *, check_exists: bool = True, check_extension: bool = True) -> ToolResult | None:
    """E1/M29: 校验文件路径（扩展名白名单 + 基础安全检查）。

    共享校验逻辑，供 parser 和 writer 复用。
    check_exists=False 用于输出路径（文件可能尚不存在）。
    check_extension=False 用于写回工具（不限制输出扩展名）。
    """
    if not path:
        return ToolResult.fail("文件路径为空")
    if check_exists and not os.path.exists(path):
        return ToolResult.fail(f"文件不存在: {os.path.basename(path)}")  # M17: 仅显示文件名
    if check_extension:
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


# ── Story 24: 副作用辅助函数 ──────────────────────────────────────

def _to_collection(result) -> "TranslationEntryCollection":
    """将各类解析器返回值归一化为 TranslationEntryCollection。"""
    from src.transbridge.converter.translation_entry_collection import TranslationEntryCollection
    if isinstance(result, TranslationEntryCollection):
        return result
    if hasattr(result, 'entries'):
        # ESP PluginParser 返回 plugin 对象，entries 属性为条目列表
        return TranslationEntryCollection(result.entries)
    # 列表/可迭代对象（EET/XT/SST/Strings 解析结果）
    return TranslationEntryCollection(list(result) if result else [])


def _create_slot(path: str, label: str, collection: "TranslationEntryCollection", ctx) -> ToolResult:
    """创建新的 CollectionSlot 并激活。

    Args:
        path: 文件路径（作为 slot key）
        label: slot 显示名称（文件名不含扩展名）
        collection: 解析得到的翻译条目集合
        ctx: 执行上下文
    """
    from src.transbridge.ui.context import CollectionSlot

    # 检查同名 slot 是否已存在
    if path in ctx.slots:
        return ToolResult.fail(
            f"集合「{label}」已存在。如需覆盖请先在界面中手动移除，"
            f"或使用 action=append 将条目追加到当前活跃集合。"
        )

    slot = CollectionSlot(label=label, collection=collection)
    ctx.add_slot(path, slot)
    ctx.activate_slot(path)

    return ToolResult.ok(
        f"已创建并激活集合「{label}」，共 {len(collection)} 条条目",
        data={
            "action": "create_slot",
            "label": label,
            "entry_count": len(collection),
            "activated": True,
        },
    )


def _append_to_collection(collection: "TranslationEntryCollection", ctx) -> ToolResult:
    """将解析出的条目追加到当前活跃集合。

    Args:
        collection: 解析得到的翻译条目集合
        ctx: 执行上下文
    """
    active_slot = getattr(ctx, 'active_slot', None)
    if active_slot is None or active_slot.collection is None:
        return ToolResult.fail(
            "当前无活跃集合，无法追加。请先使用 action=create_slot 创建集合，"
            "或通过 switch_collection 切换到已有集合。"
        )

    existing = active_slot.collection
    added_count = 0
    for entry in collection:
        existing.add(entry, overwrite=True)
        added_count += 1

    return ToolResult.ok(
        f"已追加 {added_count} 条条目到当前集合「{active_slot.label}」，"
        f"集合总计 {len(existing)} 条",
        data={
            "action": "append",
            "added_count": added_count,
            "total_count": len(existing),
            "target_label": active_slot.label,
        },
    )


# ── Parser 工具函数 ──────────────────────────────────────────────

def _tool_parse_esp(args: dict, ctx) -> ToolResult:
    """解析 ESP/ESM 插件文件。"""
    path = args.get("path", "")
    action = args.get("action", "create_slot")
    if action not in ("create_slot", "append"):
        return ToolResult.fail(f"无效的 action 值: {action}，有效值: create_slot, append")
    if not path:
        return ToolResult.fail("请提供 ESP 文件路径")
    err = _validate_path(path)
    if err: return err
    try:
        from src.transbridge.parser.plugin_parser import PluginParser
        parser = PluginParser()
        plugin = parser.parse(path)
        collection = _to_collection(plugin)
    except Exception as exc:
        return ToolResult.fail(f"解析 ESP 失败: {_sanitize_error(str(exc), path)}")

    label = Path(path).stem
    if action == "create_slot":
        return _create_slot(path, label, collection, ctx)
    else:
        return _append_to_collection(collection, ctx)


def _tool_parse_eet(args: dict, ctx) -> ToolResult:
    """解析 EET XML 文件。"""
    path = args.get("path", "")
    action = args.get("action", "create_slot")
    if action not in ("create_slot", "append"):
        return ToolResult.fail(f"无效的 action 值: {action}，有效值: create_slot, append")
    if not path: return ToolResult.fail("请提供 EET 文件路径")
    err = _validate_path(path)
    if err: return err
    try:
        from src.transbridge.parser.eet_xml_parser import EET_XmlParser
        parser = EET_XmlParser()
        result = parser.parse(path)
        collection = _to_collection(result)
    except Exception as exc:
        return ToolResult.fail(f"解析 EET 失败: {_sanitize_error(str(exc), path)}")

    label = Path(path).stem
    if action == "create_slot":
        return _create_slot(path, label, collection, ctx)
    else:
        return _append_to_collection(collection, ctx)


def _tool_parse_xt(args: dict, ctx) -> ToolResult:
    """解析 XT XML 文件。"""
    path = args.get("path", "")
    action = args.get("action", "create_slot")
    if action not in ("create_slot", "append"):
        return ToolResult.fail(f"无效的 action 值: {action}，有效值: create_slot, append")
    if not path: return ToolResult.fail("请提供 XT 文件路径")
    err = _validate_path(path)
    if err: return err
    try:
        from src.transbridge.parser.xt_xml_parser import XT_XmlParser
        parser = XT_XmlParser()
        result = parser.parse(path)
        collection = _to_collection(result)
    except Exception as exc:
        return ToolResult.fail(f"解析 XT 失败: {_sanitize_error(str(exc), path)}")

    label = Path(path).stem
    if action == "create_slot":
        return _create_slot(path, label, collection, ctx)
    else:
        return _append_to_collection(collection, ctx)


def _tool_parse_sst(args: dict, ctx) -> ToolResult:
    """解析 SST 二进制文件。"""
    path = args.get("path", "")
    action = args.get("action", "create_slot")
    if action not in ("create_slot", "append"):
        return ToolResult.fail(f"无效的 action 值: {action}，有效值: create_slot, append")
    if not path: return ToolResult.fail("请提供 SST 文件路径")
    err = _validate_path(path)  # C4: SST 与其他解析器统一路径校验
    if err: return err
    try:
        from src.transbridge.parser.xt.sst_parser import SST_Parser
        parser = SST_Parser()
        result = parser.parse(path)
        collection = _to_collection(result)
    except Exception as exc:
        return ToolResult.fail(f"解析 SST 失败: {_sanitize_error(str(exc), path)}")

    label = Path(path).stem
    if action == "create_slot":
        return _create_slot(path, label, collection, ctx)
    else:
        return _append_to_collection(collection, ctx)


def _tool_import_json(args: dict, ctx) -> ToolResult:
    """从 JSON 文件导入翻译集合。"""
    path = args.get("path", "")
    action = args.get("action", "create_slot")
    if action not in ("create_slot", "append"):
        return ToolResult.fail(f"无效的 action 值: {action}，有效值: create_slot, append")
    if not path: return ToolResult.fail("请提供 JSON 文件路径")
    err = _validate_path(path)
    if err: return err
    try:
        from src.transbridge.converter.translation_entry_collection import TranslationEntryCollection
        collection = TranslationEntryCollection.from_json_file(path)
    except Exception as exc:
        return ToolResult.fail(f"导入 JSON 失败: {_sanitize_error(str(exc), path)}")

    label = Path(path).stem
    if action == "create_slot":
        return _create_slot(path, label, collection, ctx)
    else:
        return _append_to_collection(collection, ctx)


# ── 注册 ──────────────────────────────────────────────────────

_PARAM_SCHEMAS = {
    "parse_esp": {
        "path": {"type": "str", "required": True, "description": "ESP/ESM 文件路径"},
        "action": {"type": "str", "required": False, "description": "解析后操作: create_slot（创建新槽位并激活，默认）或 append（追加到当前活跃集合）"},
    },
    "parse_eet": {
        "path": {"type": "str", "required": True, "description": "EET XML 文件路径"},
        "action": {"type": "str", "required": False, "description": "解析后操作: create_slot（创建新槽位并激活，默认）或 append（追加到当前活跃集合）"},
    },
    "parse_xt": {
        "path": {"type": "str", "required": True, "description": "XT XML 文件路径"},
        "action": {"type": "str", "required": False, "description": "解析后操作: create_slot（创建新槽位并激活，默认）或 append（追加到当前活跃集合）"},
    },
    "parse_sst": {
        "path": {"type": "str", "required": True, "description": "SST 二进制文件路径"},
        "action": {"type": "str", "required": False, "description": "解析后操作: create_slot（创建新槽位并激活，默认）或 append（追加到当前活跃集合）"},
    },
    "import_json": {
        "path": {"type": "str", "required": True, "description": "JSON 文件路径"},
        "action": {"type": "str", "required": False, "description": "导入后操作: create_slot（创建新槽位并激活，默认）或 append（追加到当前活跃集合）"},
    },
}


def _register_parser_tools():
    from src.transbridge.smart_assistant.tool_registry import ToolRegistry
    ToolRegistry.register_tools("parser", [
        {"name": "parse_esp", "display_name": "解析ESP", "description": "①解析ESP/ESM/ESL插件文件提取可翻译字符串。②参数: path(必填, .esp/.esm/.esl), action(可选, create_slot默认创建新槽位并激活/append追加到当前活跃集合)。③返回: create_slot→{action,label,entry_count,activated}, append→{action,added_count,total_count,target_label}。规则: create_slot支持后续write_back target=esp/strings推断, append前需确认has_active_collection, path拒绝../和绝对路径, 通过get_app_state查看esp_file",
         "execute": _tool_parse_esp, "parameters": _PARAM_SCHEMAS.get("parse_esp", {}), "permission": "write"},
        {"name": "parse_eet", "display_name": "解析EET", "description": "①解析EET XML翻译文件(Elder Scrolls Translation格式, 根元素<EET>)。②参数: path(必填, EET XML), action(可选, create_slot默认/append)。③返回: create_slot→{action,label,entry_count,activated}, append→{action,added_count,total_count,target_label}。规则: create_slot支持后续write_back target=eet推断, append前需确认has_active_collection, path拒绝../和绝对路径, 通过get_app_state查看eet_file",
         "execute": _tool_parse_eet, "parameters": _PARAM_SCHEMAS.get("parse_eet", {}), "permission": "write"},
        {"name": "parse_xt", "display_name": "解析XT", "description": "①解析XT XML翻译文件(xTranslator格式, Skyrim MOD翻译工具, 根元素<XT>)。②参数: path(必填, XT XML), action(可选, create_slot默认/append)。③返回: create_slot→{action,label,entry_count,activated}, append→{action,added_count,total_count,target_label}。规则: create_slot支持后续write_back target=xt推断, append前需确认has_active_collection, path拒绝../和绝对路径, 通过get_app_state查看xt_file",
         "execute": _tool_parse_xt, "parameters": _PARAM_SCHEMAS.get("parse_xt", {}), "permission": "write"},
        {"name": "parse_sst", "display_name": "解析SST", "description": "①解析SST二进制翻译文件。SSU8=单语言记录, SSU9=双语言多字符串(含插件名头部)。②参数: path(必填, .sst), action(可选, create_slot默认/append)。③返回: create_slot→{action,label,entry_count,activated}, append→{action,added_count,total_count,target_label}。规则: 不支持write_back(SST序列化被屏蔽, 仅可浏览/筛选/统计), append前需确认has_active_collection, path拒绝../和绝对路径, sst_file通过get_app_state追踪",
         "execute": _tool_parse_sst, "parameters": _PARAM_SCHEMAS.get("parse_sst", {}), "permission": "write"},
        {"name": "import_json", "display_name": "导入JSON", "description": "①从JSON文件导入翻译条目(支持标准格式[{key,original,translation,stage,context}]和DSD格式)。②参数: path(必填, .json), action(可选, create_slot默认/append)。③返回: create_slot→{action,label,entry_count,activated}, append→{action,added_count,total_count,target_label}。规则: 不记录文件路径供write_back推断, append前需确认has_active_collection, path拒绝../和绝对路径",
         "execute": _tool_import_json, "parameters": _PARAM_SCHEMAS.get("import_json", {}), "permission": "write"},
    ])


_register_parser_tools()
