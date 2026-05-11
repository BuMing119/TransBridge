import logging
import re
import json

from .base import GuardMiddleware, GuardResult

logger = logging.getLogger(__name__)

_SENSITIVE_PATTERNS = [
    (re.compile(r'sk-[a-zA-Z0-9]{20,}'), "OpenAI API Key"),
    (re.compile(r'sk-ant-[a-zA-Z0-9_-]{20,}'), "Anthropic API Key"),
    (re.compile(r'Bearer\s+[a-zA-Z0-9._-]{20,}'), "Bearer Token"),
    # m11: 扩展脱敏覆盖
    (re.compile(r'AKIA[0-9A-Z]{16}'), "AWS Access Key"),
    (re.compile(r'(?:ghp|gho|github_pat)_[a-zA-Z0-9]{20,}'), "GitHub Token"),
    (re.compile(r'eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}'), "JWT Token"),
    (re.compile(r'(?:xoxb|xoxp|xoxt)-[0-9]+-[0-9]+-[a-zA-Z0-9]+'), "Slack Token"),
    (re.compile(r'(?:password|passwd|secret|token|api_key|apikey)\s*[:=]\s*["\']?[^\s"\'},]{8,}["\']?', re.IGNORECASE), "疑似凭据"),
]

_MAX_MESSAGE_LEN = 10240       # 10KB
_DEFAULT_MAX_OUTPUT = 102400   # 100KB


class OutputValidationGuard(GuardMiddleware):
    def __init__(self, max_output_size: int = _DEFAULT_MAX_OUTPUT):
        self._max_output_size = max_output_size

    def before_execute(self, step, ctx) -> GuardResult:
        return GuardResult(True)

    def after_execute(self, step, result, ctx) -> GuardResult:
        if not isinstance(result.data, dict) and result.data is not None:
            return GuardResult(False, f"输出类型错误: 期望 dict，实际 {type(result.data).__name__}")
        message = result.message or ""
        if len(message) > _MAX_MESSAGE_LEN:
            result.message = message[:_MAX_MESSAGE_LEN] + "...(截断)"
        if result.data is not None:
            try:
                serialized = json.dumps(result.data, default=str, ensure_ascii=False)
                if len(serialized.encode('utf-8', errors='replace')) > self._max_output_size:
                    result.data = {"warning": f"输出超过大小限制 ({self._max_output_size} bytes)，已截断"}
                    logger.warning("OutputValidation: data 超过大小限制，已截断")
            except Exception:
                pass
        result.message = self._redact_sensitive(result.message)
        if result.data and isinstance(result.data, dict):
            result.data = self._redact_dict(result.data)
        return GuardResult(True)

    def _redact_sensitive(self, text: str) -> str:
        for pattern, label in _SENSITIVE_PATTERNS:
            if pattern.search(text):
                logger.warning("OutputValidation: 脱敏 %s", label)
            text = pattern.sub("***REDACTED***", text)
        return text

    def _redact_dict(self, data: dict) -> dict:
        result = {}
        for k, v in data.items():
            if isinstance(v, str):
                for pattern, _label in _SENSITIVE_PATTERNS:
                    v = pattern.sub("***REDACTED***", v)
                result[k] = v
            elif isinstance(v, dict):
                result[k] = self._redact_dict(v)
            elif isinstance(v, list):
                # E12: 递归处理 list 内的字符串和字典
                result[k] = self._redact_list(v)
            elif isinstance(v, tuple):  # m3: 递归处理 tuple
                result[k] = tuple(self._redact_list(list(v)))
            else:
                result[k] = v
        return result

    def _redact_list(self, data: list) -> list:
        """E12: 递归处理 list 中的敏感信息。"""
        result = []
        for item in data:
            if isinstance(item, str):
                for pattern, _label in _SENSITIVE_PATTERNS:
                    item = pattern.sub("***REDACTED***", item)
                result.append(item)
            elif isinstance(item, dict):
                result.append(self._redact_dict(item))
            elif isinstance(item, list):
                result.append(self._redact_list(item))
            elif isinstance(item, tuple):  # m3: 递归处理 tuple
                result.append(tuple(self._redact_list(list(item))))
            else:
                result.append(item)
        return result
