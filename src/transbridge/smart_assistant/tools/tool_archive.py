"""通用文件操作工具 — 归档解包/打包、目录 diff、资源过滤 (archive namespace)。

FR16.1-16.3 的 Agent 工具。纯文件操作，不依赖 collection。
"""
from __future__ import annotations

from .base import ToolResult


# ── 参数 schema ────────────────────────────────────────────────

_PARAM_SCHEMAS = {
    "extract_archive": {
        "archive_path": {"type": "str", "required": True, "description": "归档文件路径（.7z/.zip/.rar）"},
        "dest_dir": {"type": "str", "required": True, "description": "解包目标目录"},
        "files": {"type": "list", "required": False, "description": "仅提取的相对路径列表（分层提取，可选）"},
    },
    "pack_archive": {
        "src_dir": {"type": "str", "required": True, "description": "要打包的源目录"},
        "archive_path": {"type": "str", "required": True, "description": "输出归档路径"},
        "fmt": {"type": "str", "required": False, "description": "打包格式 zip(默认)/7z"},
    },
    "diff_directories": {
        "old_dir": {"type": "str", "required": True, "description": "旧版目录"},
        "new_dir": {"type": "str", "required": True, "description": "新版目录"},
        "skip_hash_exts": {"type": "list", "required": False, "description": "跳过哈希的扩展名（如 [.bsa]）"},
    },
    "filter_files": {
        "files": {"type": "list", "required": True, "description": "文件相对路径列表"},
        "rules_json": {"type": "str", "required": False, "description": "过滤规则 JSON 文件路径（可选，默认内置规则）"},
    },
}


# ── 工具实现 ───────────────────────────────────────────────────

def _tool_extract_archive(args: dict, ctx) -> ToolResult:
    try:
        from transbridge.fileops.archive import extract
        result = extract(
            args["archive_path"], args["dest_dir"],
            files=args.get("files"),
        )
        return ToolResult.ok(
            f"已解包 {result['extracted_count']} 个文件到 {args['dest_dir']}",
            data=result,
        )
    except Exception as exc:
        return ToolResult.fail(f"解包失败: {exc}", error_category="internal")


def _tool_pack_archive(args: dict, ctx) -> ToolResult:
    try:
        from transbridge.fileops.archive import pack
        out = pack(
            args["src_dir"], args["archive_path"],
            fmt=args.get("fmt", "zip"),
        )
        return ToolResult.ok(f"已打包为 {out}", data={"archive_path": out})
    except Exception as exc:
        return ToolResult.fail(f"打包失败: {exc}", error_category="internal")


def _tool_diff_directories(args: dict, ctx) -> ToolResult:
    try:
        from transbridge.fileops.differ import diff_directories
        skip = set(args.get("skip_hash_exts") or [])
        result = diff_directories(args["old_dir"], args["new_dir"], skip_hash_exts=skip)
        data = result.to_dict()
        s = data["summary"]
        return ToolResult.ok(
            f"diff 完成: 增{s['added']}/删{s['removed']}/变{s['changed']}/不变{s['unchanged']}",
            data=data,
        )
    except Exception as exc:
        return ToolResult.fail(f"diff 失败: {exc}", error_category="internal")


def _tool_filter_files(args: dict, ctx) -> ToolResult:
    try:
        from transbridge.fileops.filter_rules import FilterRules, filter_files
        rules = FilterRules.from_json(args["rules_json"]) if "rules_json" in args else FilterRules()
        kept, stripped = filter_files(args["files"], rules)
        return ToolResult.ok(
            f"过滤完成: 保留{len(kept)}/剔除{len(stripped)}",
            data={"kept": kept, "stripped": stripped},
        )
    except Exception as exc:
        return ToolResult.fail(f"过滤失败: {exc}", error_category="internal")


# ── 注册 ───────────────────────────────────────────────────────

def _register_archive_tools():
    from ..tool_registry import ToolRegistry
    ToolRegistry.register_tools("archive", [
        {"name": "extract_archive", "display_name": "解包归档",
         "description": "①解包7z/zip/rar归档到目标目录。②参数: archive_path, dest_dir, files(可选,仅提取列表内文件)。③返回{dest_dir, extracted_count}。④规则: 不依赖用户环境7-Zip。",
         "execute": _tool_extract_archive, "permission": "write",
         "parameters": _PARAM_SCHEMAS["extract_archive"]},
        {"name": "pack_archive", "display_name": "打包目录",
         "description": "①将目录打包为zip/7z归档。②参数: src_dir, archive_path, fmt=zip(默认)/7z。③返回{archive_path}。④规则: 不产rar。",
         "execute": _tool_pack_archive, "permission": "write",
         "parameters": _PARAM_SCHEMAS["pack_archive"]},
        {"name": "diff_directories", "display_name": "目录差异对比",
         "description": "①对比新旧目录清单。②参数: old_dir, new_dir, skip_hash_exts(可选)。③返回{added,removed,changed,unchanged}。④规则: 按相对路径对齐+内容哈希。",
         "execute": _tool_diff_directories, "permission": "read",
         "parameters": _PARAM_SCHEMAS["diff_directories"]},
        {"name": "filter_files", "display_name": "资源过滤",
         "description": "①按扩展名规则分类文件为保留/剔除。②参数: files(相对路径列表), rules_json(可选规则文件)。③返回{kept,stripped}。④规则: 目录级规则优先于全局。",
         "execute": _tool_filter_files, "permission": "read",
         "parameters": _PARAM_SCHEMAS["filter_files"]},
    ])


_register_archive_tools()