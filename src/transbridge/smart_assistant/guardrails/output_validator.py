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
        # M13: 放宽类型检查 — 允许 dict | list | None
        if not isinstance(result.data, (dict, list)) and result.data is not None:
            return GuardResult(False, f"输出类型错误: 期望 dict/list，实际 {type(result.data).__name__}")
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
        elif result.data and isinstance(result.data, list):
            result.data = self._redact_list(result.data)
        return GuardResult(True)

    def _redact_sensitive(self, text: str) -> str:
        for pattern, label in _SENSITIVE_PATTERNS:
            if pattern.search(text):
                logger.warning("OutputValidation: 脱敏 %s", label)
            text = pattern.sub("***REDACTED***", text)
        return text

    def _redact_dict(self, data: dict) -> dict:
        """M19: 就地变异（mutate in place），避免递归深拷贝。
        对简单值直接修改原对象，对嵌套结构递归进入后原地修改。"""
        for k, v in data.items():
            if isinstance(v, str):
                for pattern, _label in _SENSITIVE_PATTERNS:
                    v = pattern.sub("***REDACTED***", v)
                data[k] = v
            elif isinstance(v, dict):
                self._redact_dict(v)
            elif isinstance(v, list):
                # E12: 递归处理 list 内的字符串和字典
                self._redact_list(v)
            elif isinstance(v, tuple):  # m3: 递归处理 tuple
                lst = list(v)
                self._redact_list(lst)
                data[k] = tuple(lst)
            # 非字符串简单值无需修改
        return data

    def _redact_list(self, data: list) -> list:
        """E12: 就地变异 list 中的敏感信息（M19: 避免全量复制）。"""
        for i, item in enumerate(data):
            if isinstance(item, str):
                for pattern, _label in _SENSITIVE_PATTERNS:
                    item = pattern.sub("***REDACTED***", item)
                data[i] = item
            elif isinstance(item, dict):
                self._redact_dict(item)
            elif isinstance(item, list):
                self._redact_list(item)
            elif isinstance(item, tuple):  # m3: 递归处理 tuple
                lst = list(item)
                self._redact_list(lst)
                data[i] = tuple(lst)
            # 非字符串简单值无需修改
        return data
