import logging
import re

from .base import GuardMiddleware, GuardResult

logger = logging.getLogger(__name__)

_INJECTION_PATTERNS = [
    (re.compile(r"'\s*;\s*(DROP|DELETE|INSERT|UPDATE|SELECT)\s", re.IGNORECASE), "SQL注入"),
    (re.compile(r"<script[>\s]", re.IGNORECASE), "XSS"),
    (re.compile(r"onerror\s*=", re.IGNORECASE), "XSS事件注入"),
    (re.compile(r";\s*(rm|cat|wget|curl|bash|sh|cmd|powershell)\s", re.IGNORECASE), "命令注入"),
    (re.compile(r"\|\s*(cat|rm|bash)\b", re.IGNORECASE), "管道命令注入"),
    (re.compile(r"`[^`]*`"), "反引号命令注入"),
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
        return GuardResult(True)

    def after_execute(self, step, result, ctx) -> GuardResult:
        return GuardResult(True)

    def _check_value(self, key: str, value) -> GuardResult:
        if isinstance(value, str):
            if len(value.encode('utf-8', errors='replace')) > self._max_size:
                return GuardResult(False, f"参数 '{key}' 超过大小限制 ({self._max_size} bytes)")
            for pattern, label in _INJECTION_PATTERNS:
                if pattern.search(value):
                    logger.warning("InputValidation: 检测到%d模式在参数 '%s'", label, key)
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
