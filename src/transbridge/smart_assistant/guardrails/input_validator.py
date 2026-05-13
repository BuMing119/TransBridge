import logging
import os
import re

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

_PATH_TRAVERSAL_PATTERNS = [
    (re.compile(r"\.\./"), "Unix路径遍历 ../"),
    (re.compile(r"\.\.\\"), "Windows路径遍历 ..\\"),
]

_MAX_INPUT_SIZE = 102400  # 100KB


class InputValidationGuard(GuardMiddleware):
    def __init__(self, max_input_size: int = _MAX_INPUT_SIZE):
        self._max_size = max_input_size

    def before_execute(self, step, ctx) -> GuardResult:
        args = step.get("args", {})
        if not isinstance(args, dict):
            return GuardResult(False, f"参数类型错误: 期望 dict，实际 {type(args).__name__}")
        for key, value in args.items():
            result = self._check_value(key, value)
            if not result.allowed:
                return result
        # E1: 路径遍历检测
        path_result = self._detect_path_traversal(args)
        if not path_result.allowed:
            return path_result
        return GuardResult(True)

    def after_execute(self, step, result, ctx) -> GuardResult:
        return GuardResult(True)

    def _detect_path_traversal(self, args: dict) -> GuardResult:
        """E1: 检测路径参数中的路径遍历攻击和绝对路径注入。

        M10: 使用启发式检测替代硬编码白名单。任何参数名含 path/file/dir/
        output/dest/save 子串（不区分大小写）均触发路径遍历检查。
        """
        _PATH_KEY_SUBSTRINGS = ("path", "file", "dir", "output", "dest", "save")
        for key, value in args.items():
            if not isinstance(value, str):
                continue
            # M10: 启发式检测 — 参数名含路径相关子串即检查
            key_lower = key.lower()
            if not any(sub in key_lower for sub in _PATH_KEY_SUBSTRINGS):
                continue
            # 检测 ../ 和 ..\\
            for pattern, label in _PATH_TRAVERSAL_PATTERNS:
                if pattern.search(value):
                    logger.warning("InputValidation: 检测到%s在参数 '%s'", label, key)
                    return GuardResult(False, f"检测到{label}攻击")
            # 检测绝对路径 (Unix /etc/ 和 Windows C:\)
            if os.path.isabs(value):
                logger.warning("InputValidation: 检测到绝对路径在参数 '%s': %s", key, value)
                return GuardResult(False, f"不允许使用绝对路径: {key}")

        return GuardResult(True)

    def _check_value(self, key: str, value) -> GuardResult:
        if isinstance(value, str):
            if len(value.encode('utf-8', errors='replace')) > self._max_size:
                return GuardResult(False, f"参数 '{key}' 超过大小限制 ({self._max_size} bytes)")
            for pattern, label in _INJECTION_PATTERNS:
                if pattern.search(value):
                    logger.warning("InputValidation: 检测到%s模式在参数 '%s'", label, key)
                    return GuardResult(False, f"检测到{label}模式")
        elif isinstance(value, dict):
            for k, v in value.items():
                result = self._check_value(f"{key}.{k}", v)
                if not result.allowed:
                    return result
        elif isinstance(value, list):
            for i, item in enumerate(value):
                result = self._check_value(f"{key}[{i}]", item)
                if not result.allowed:
                    return result
        return GuardResult(True)
