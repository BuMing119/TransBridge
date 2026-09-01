"""P2 文件解析工具 — 将翻译文件加载到 AppContext (parser namespace)。

Story 12 v2: 权限 write→read(H6), 文件扩展名白名单(E1)。
Story 24: 副作用补全 — action 参数 (create_slot/append) + HITL 确认。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from .base import ToolResult

if TYPE_CHECKING:
    from transbridge.converter.translation_entry_collection import TranslationEntryCollection

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
    # Security authorization is intentionally not duplicated here. The shared
    # InputValidationGuard resolves the canonical path (including symlinks and
    # junctions) and checks it against RuntimeContext.authorized_roots before
    # this business-level existence/extension validation runs.
    return None


# ── Story 24: 副作用辅助函数 ──────────────────────────────────────


def _to_collection(result) -> TranslationEntryCollection:
    """将各类解析器返回值归一化为 TranslationEntryCollection。"""
    TranslationEntryCollection = _collection_type()
    if isinstance(result, TranslationEntryCollection):
        return result
    if hasattr(result, "entries"):
        # ESP PluginParser 返回 plugin 对象，entries 属性为条目列表
        return TranslationEntryCollection(result.entries)
    # 列表/可迭代对象（EET/XT/SST/Strings 解析结果）
    return TranslationEntryCollection(list(result) if result else [])


def _collection_type():
    """延迟加载集合类型，避免 parser 工具注册阶段形成 I/O/legacy 循环导入。"""
    from transbridge.converter.translation_entry_collection import TranslationEntryCollection

    return TranslationEntryCollection


# ── Parser 工具函数工厂 ─────────────────────────────────────────
# M1: 消除 5 个 parser 函数中 95% 相同的代码（action校验/path校验/懒加载导入/解析/slot操作）

_PARSER_DISPATCH = {
    "esp": {
        "format_id": "plugin.sse",
        "label": "ESP 插件",
    },
    "eet": {
        "format_id": "xml.eet",
        "label": "EET XML",
    },
    "xt": {
        "format_id": "xml.xt",
        "label": "XT XML",
    },
    "sst": {
        "module": "transbridge.parser.xt.sst_parser",
        "class": "SST_Parser",
        "parse_fn": lambda m, path: m().parse(path),
        "label": "SST 二进制",
    },
    "json": {
        "label": "JSON 导入",
    },
}


def _parse_file(file_type: str, args: dict, ctx) -> ToolResult:
    """统一的文件解析入口。根据 file_type 分派到对应的解析器。

    file_type: "esp" | "eet" | "xt" | "sst" | "json"
    """
    from ._project_tool_mutations import ProjectToolTarget
    from ._source_import import publish_import

    target = ProjectToolTarget.capture(ctx)
    path = args.get("path", "")
    action = args.get("action", "create_slot")
    if action not in ("create_slot", "append"):
        return ToolResult.fail(f"无效的 action 值: {action}，有效值: create_slot, append")
    if not path:
        return ToolResult.fail(f"请提供 {_PARSER_DISPATCH[file_type]['label']} 文件路径")
    err = _validate_path(path)
    if err:
        return err

    dispatch = _PARSER_DISPATCH[file_type]
    source_snapshot = None
    format_id = None
    options = ()
    try:
        if file_type == "json":
            from ._json_import import parse_json_source

            collection, source_snapshot, format_id, options = parse_json_source(path, args)
        elif "format_id" in dispatch:
            from transbridge.application.contracts import OperationOutcome, RequestContext
            from transbridge.application.io import (
                FormatId,
                ParseRequest,
                SourceDescriptor,
                TranslationIoUseCase,
            )
            from transbridge.entrypoints.agent import parse_translation_source

            source = Path(path)
            format_id = FormatId(dispatch["format_id"])
            parsed = parse_translation_source(
                TranslationIoUseCase(),
                ParseRequest(
                    SourceDescriptor(str(source), source.name, source.stat().st_size),
                    RequestContext("smart-assistant-parser"),
                    format_id,
                ),
            )
            if parsed.outcome not in {OperationOutcome.COMPLETED, OperationOutcome.PARTIAL}:
                message = "; ".join(diagnostic.message for diagnostic in parsed.diagnostics)
                return ToolResult.fail(f"解析 {dispatch['label']} 失败: {message}")
            collection = _to_collection(parsed.entries)
            source_snapshot = parsed.source_snapshot
        else:
            import importlib

            mod = importlib.import_module(dispatch["module"])
            cls = getattr(mod, dispatch["class"])
            result = cls().parse(path)
            collection = _to_collection(result)
        return publish_import(
            target,
            path,
            Path(path).stem,
            collection,
            source_snapshot,
            format_id,
            action,
            options,
        )
    except Exception as exc:
        return ToolResult.fail(f"解析 {dispatch['label']} 失败: {_sanitize_error(str(exc), path)}")


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

_PARAM_SCHEMAS = {
    "parse_esp": {
        "path": {"type": "str", "required": True, "description": "Path to the ESP/ESM file"},
        "action": {
            "type": "str",
            "required": False,
            "description": (
                "Action after parsing: create_slot (create and activate a new slot; default) or append "
                "(append to the active collection)"
            ),
        },
    },
    "parse_eet": {
        "path": {"type": "str", "required": True, "description": "Path to the EET XML file"},
        "action": {
            "type": "str",
            "required": False,
            "description": (
                "Action after parsing: create_slot (create and activate a new slot; default) or append "
                "(append to the active collection)"
            ),
        },
    },
    "parse_xt": {
        "path": {"type": "str", "required": True, "description": "Path to the XT XML file"},
        "action": {
            "type": "str",
            "required": False,
            "description": (
                "Action after parsing: create_slot (create and activate a new slot; default) or append "
                "(append to the active collection)"
            ),
        },
    },
    "parse_sst": {
        "path": {"type": "str", "required": True, "description": "Path to the SST binary file"},
        "action": {
            "type": "str",
            "required": False,
            "description": (
                "Action after parsing: create_slot (create and activate a new slot; default) or append "
                "(append to the active collection)"
            ),
        },
    },
    "import_json": {
        "path": {"type": "str", "required": True, "description": "Path to the JSON file"},
        "format": {
            "type": "str",
            "required": False,
            "description": "auto/paratranz/transbridge/dsd; ambiguous JSON requires an explicit choice",
        },
        "project_id": {
            "type": "int",
            "required": False,
            "description": "Known ParaTranz project ID for remote references; otherwise IDs remain offline-scoped",
        },
        "action": {
            "type": "str",
            "required": False,
            "description": (
                "Action after import: create_slot (create and activate a new slot; default) or append "
                "(append to the active collection)"
            ),
        },
    },
}


# M9: Parser 工具权限为 write 而非 Plan 原定的 read。
# Story 24 增加了 create_slot/append 副作用（修改 AppContext 全局状态），
# 因此权限从 read 提升为 write 是合理的设计变更。
# 参见: plans/agent-tool-expansion/stories/story-24-parser-side-effects.md
def _register_parser_tools():
    from ..tool_registry import ToolRegistry

    ToolRegistry.register_tools(
        "parser",
        [
            {
                "name": "parse_esp",
                "display_name": "解析ESP",
                "description": "①Parse an ESP/ESM/ESL plugin file and extract translatable strings. ②Parameters: path (required, .esp/.esm/.esl), action (optional; create_slot creates and activates a new slot by default, or append adds to the active collection). ③Returns: create_slot→{action,label,entry_count,activated}, append→{action,added_count,total_count,target_label}. Rules: create_slot supports later write_back target=esp/strings inference; append requires has_active_collection; path must be a normalized path within a root authorized by RuntimeContext; use get_app_state to inspect esp_file.",  # noqa: E501
                "execute": _tool_parse_esp,
                "parameters": _PARAM_SCHEMAS.get("parse_esp", {}),
                "permission": "write",
            },
            {
                "name": "parse_eet",
                "display_name": "解析EET",
                "description": "①Parse an EET XML translation file (Elder Scrolls Translation format with an <EET> root element). ②Parameters: path (required, EET XML), action (optional; create_slot by default, or append). ③Returns: create_slot→{action,label,entry_count,activated}, append→{action,added_count,total_count,target_label}. Rules: create_slot supports later write_back target=eet inference; append requires has_active_collection; path must be a normalized path within a root authorized by RuntimeContext; use get_app_state to inspect eet_file.",  # noqa: E501
                "execute": _tool_parse_eet,
                "parameters": _PARAM_SCHEMAS.get("parse_eet", {}),
                "permission": "write",
            },
            {
                "name": "parse_xt",
                "display_name": "解析XT",
                "description": "①Parse an XT XML translation file (xTranslator format for Skyrim mod translation, with an <XT> root element). ②Parameters: path (required, XT XML), action (optional; create_slot by default, or append). ③Returns: create_slot→{action,label,entry_count,activated}, append→{action,added_count,total_count,target_label}. Rules: create_slot supports later write_back target=xt inference; append requires has_active_collection; path must be a normalized path within a root authorized by RuntimeContext; use get_app_state to inspect xt_file.",  # noqa: E501
                "execute": _tool_parse_xt,
                "parameters": _PARAM_SCHEMAS.get("parse_xt", {}),
                "permission": "write",
            },
            {
                "name": "parse_sst",
                "display_name": "解析SST",
                "description": "①Parse an SST binary translation file. SSU8 contains single-language records; SSU9 contains bilingual multi-string records with a plugin-name header. ②Parameters: path (required, .sst), action (optional; create_slot by default, or append). ③Returns: create_slot→{action,label,entry_count,activated}, append→{action,added_count,total_count,target_label}. Rules: write_back is unsupported because SST serialization is disabled; data is available only for browsing, filtering, and statistics. append requires has_active_collection; path must be a normalized path within a root authorized by RuntimeContext; sst_file is tracked through get_app_state.",  # noqa: E501
                "execute": _tool_parse_sst,
                "parameters": _PARAM_SCHEMAS.get("parse_sst", {}),
                "permission": "write",
            },
            {
                "name": "import_json",
                "display_name": "导入JSON",
                "description": (
                    "①Import JSON using its explicit identity contract, retaining remote references and extensions. "
                    "②Arguments: path, action=create_slot/append, format=auto/paratranz/transbridge/dsd, "
                    "optional project_id for ParaTranz remote references. Ambiguous JSON requires format. "
                    "③create_slot returns {action,label,entry_count,activated}; append returns counts. "
                    "In V2, create_slot requires a formal source adapter (currently ParaTranz JSON); "
                    "append updates only existing full EntryKeys and cannot add an unregistered source. "
                    "Paths must be within an authorized root."
                ),
                "execute": _tool_import_json,
                "parameters": _PARAM_SCHEMAS.get("import_json", {}),
                "permission": "write",
            },
        ],
    )


_register_parser_tools()
