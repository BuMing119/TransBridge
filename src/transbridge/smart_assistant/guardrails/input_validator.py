import logging
from pathlib import Path
import re

from transbridge.application.security.paths import PathAuthorizationPolicy, PathGrant
from transbridge.application.tools.schema import validate_arguments

from ..tool_registry import ToolRegistry
from .base import GuardMiddleware, GuardResult

logger = logging.getLogger(__name__)

# M16: 放宽注入检测 — 移除常见翻译文本中的误伤模式，保留真正危险的模式
_SAFE_HTML_TAGS = {
    "font", "b", "i", "u", "br", "p", "h1", "h2", "h3", "h4", "h5", "h6",
    "div", "span", "img", "a", "table", "tr", "td", "th", "ul", "ol", "li",
    "em", "strong", "s", "sub", "sup", "hr", "pre", "code", "blockquote",
}

_INJECTION_PATTERNS = [
    # SQL注入 — M16: 仅保留明显的注入模式
    (re.compile(r"'\s*;\s*(DROP|ALTER|EXEC|UNION|TRUNCATE)\b", re.IGNORECASE), "SQL注入"),
    (re.compile(r"(\"|')\s+OR\s+(\"|'|\d)", re.IGNORECASE), "SQL OR注入"),
    (re.compile(r"(\"|')\s*--"), "SQL注释注入"),
    (re.compile(r"\bWAITFOR\s+DELAY\b", re.IGNORECASE), "SQL延时注入"),
    # XSS — 仅检测危险标签和事件处理器
    (re.compile(r"<script[\s>]", re.IGNORECASE), "XSS script标签"),
    (re.compile(r"<iframe[\s>]", re.IGNORECASE), "XSS iframe"),
    (re.compile(r"<embed[\s>]", re.IGNORECASE), "XSS embed"),
    (re.compile(r"<object[\s>]", re.IGNORECASE), "XSS object"),
    (re.compile(r"\bon(?:error|load|focus|click|mouseover|mouseout|submit|change|keydown|keyup)=\s*", re.IGNORECASE), "XSS事件处理器"),
    (re.compile(r"javascript\s*:", re.IGNORECASE), "XSS javascript协议"),
    # 命令注入
    (re.compile(r"(?:;|\||&&|`)\s*(?:python|perl|ruby|php|node|nc|ncat|ssh|scp|wget|curl|telnet|socat|powershell|cmd|bash|sh|wmic|cscript|mshta|regsvr32|bitsadmin)\b", re.IGNORECASE), "命令注入"),
    (re.compile(r"(?:Invoke-Expression|iex|Start-Process|EncodedCommand|IEX|Invoke-WebRequest)", re.IGNORECASE), "PowerShell注入"),
    (re.compile(r"`[^`]*`"), "反引号命令注入"),
]

_MAX_INPUT_SIZE = 102400  # 100KB
_MAX_RECURSION_DEPTH = 10  # M18: 限制嵌套递归深度，防止RecursionError


class InputValidationGuard(GuardMiddleware):
    def __init__(self, max_input_size: int = _MAX_INPUT_SIZE, max_depth: int = _MAX_RECURSION_DEPTH):
        self._max_size = max_input_size
        self._max_depth = max_depth  # M18: 递归深度上限

    def before_execute(self, step, ctx) -> GuardResult:
        args = step.get("args", {})
        if not isinstance(args, dict):
            return GuardResult(False, f"参数类型错误: 期望 dict，实际 {type(args).__name__}")
        spec = ToolRegistry.get(step.get("tool", ""))
        if spec is not None:
            if not spec.available:
                return GuardResult(
                    False,
                    f"工具能力不可用: {spec.unavailable_reason}",
                    code="CAPABILITY_UNAVAILABLE",
                )
            schema_errors = validate_arguments(spec.parameters, args)
            if schema_errors:
                error = schema_errors[0]
                return GuardResult(
                    False,
                    f"参数校验失败 {error.pointer}: {error.message}",
                    code="ARGUMENT_SCHEMA_INVALID",
                    json_pointer=error.pointer,
                )
        for key, value in args.items():
            result = self._check_value(key, value)
            if not result.allowed:
                return result
        # E1: 路径遍历检测
        path_result = self._authorize_paths(args, ctx)
        if not path_result.allowed:
            return path_result
        return GuardResult(True)

    def after_execute(self, step, result, ctx) -> GuardResult:
        return GuardResult(True)

    def _authorize_paths(self, args: dict, ctx) -> GuardResult:
        """E1: 检测路径参数中的路径遍历攻击和绝对路径注入。

        M10: 使用启发式检测替代硬编码白名单。任何参数名含 path/file/dir/
        output/dest/save 子串（不区分大小写）均触发路径遍历检查。
        """
        path_values = tuple(self._iter_path_values(args))
        if not path_values:
            return GuardResult(True)
        request_context = getattr(ctx, "request_context", None)
        roots = tuple(getattr(request_context, "authorized_roots", ()) or ())
        if not roots:
            roots = tuple(getattr(ctx, "authorized_roots", ()) or ())
        metadata = dict(getattr(request_context, "metadata", ()) or ())
        working_directory = metadata.get("working_directory")
        if roots:
            try:
                grants = [PathGrant(Path(root), allow_create=True) for root in roots]
                policy = PathAuthorizationPolicy(grants)
            except (OSError, RuntimeError):
                return GuardResult(
                    False,
                    "授权根无法安全解析",
                    code="PATH_GRANT_INVALID",
                )
            base = Path(working_directory) if working_directory else Path(roots[0])
        else:
            return GuardResult(
                False,
                "路径参数缺少授权根（拒绝路径遍历或隐式工作目录）",
                code="PATH_GRANT_REQUIRED",
            )

        for key, value, for_creation in path_values:
            decision = policy.authorize(
                value,
                working_directory=base,
                for_creation=for_creation,
            )
            if not decision.allowed:
                logger.warning("InputValidation: 路径参数 '%s' 被拒绝: %s", key, decision.code)
                return GuardResult(False, decision.reason, code=decision.code)

        return GuardResult(True)

    @classmethod
    def _iter_path_values(cls, value, prefix: str = "", path_hint: bool = False):
        if isinstance(value, dict):
            for key, item in value.items():
                key_text = str(key)
                child_prefix = f"{prefix}.{key_text}" if prefix else key_text
                child_hint = path_hint or cls._is_path_key(key_text)
                yield from cls._iter_path_values(item, child_prefix, child_hint)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                yield from cls._iter_path_values(item, f"{prefix}[{index}]", path_hint)
        elif isinstance(value, str) and path_hint:
            leaf = prefix.rsplit(".", 1)[-1].split("[", 1)[0].lower()
            for_creation = any(
                marker in leaf for marker in ("output", "destination", "dest", "save")
            )
            yield prefix, value, for_creation

    @staticmethod
    def _is_path_key(key: str) -> bool:
        normalized = re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower()
        exact = {
            "path",
            "file",
            "filename",
            "dir",
            "directory",
            "output",
            "destination",
            "dest",
            "save",
        }
        suffixes = tuple(f"_{name}" for name in exact)
        return normalized in exact or normalized.endswith(suffixes)

    def _check_value(self, key: str, value, depth: int = 0) -> GuardResult:
        # M18: 限制嵌套递归深度，防止深层嵌套导致RecursionError
        if depth > self._max_depth:
            logger.warning("InputValidation: 参数 '%s' 嵌套深度 (%d) 超过上限 (%d)", key, depth, self._max_depth)
            return GuardResult(False, f"参数嵌套深度超过上限 ({self._max_depth})")
        if isinstance(value, str):
            if len(value.encode('utf-8', errors='replace')) > self._max_size:
                return GuardResult(False, f"参数 '{key}' 超过大小限制 ({self._max_size} bytes)")
            for pattern, label in _INJECTION_PATTERNS:
                if pattern.search(value):
                    logger.warning("InputValidation: 检测到%s模式在参数 '%s'", label, key)
                    return GuardResult(False, f"检测到{label}模式")
        elif isinstance(value, dict):
            for k, v in value.items():
                result = self._check_value(f"{key}.{k}", v, depth + 1)
                if not result.allowed:
                    return result
        elif isinstance(value, list):
            for i, item in enumerate(value):
                result = self._check_value(f"{key}[{i}]", item, depth + 1)
                if not result.allowed:
                    return result
        return GuardResult(True)
