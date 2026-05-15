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

# C5: 文件路径模式 — 用于存储前脱敏，防止泄露用户系统目录结构
_FILE_PATH_PATTERNS = [
    (re.compile(r'[A-Za-z]:[\\/][^\s\[\]\(\){}<>:;"\']+'), "Windows File Path"),
    (re.compile(r'/(?:home|etc|opt|var|tmp|usr|root|Users|Applications|Library)(?:/[^\s\[\]\(\){}<>:;"\']+)+'), "Unix File Path"),
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
                logger.warning("OutputValidation: 序列化失败，跳过大小校验", exc_info=True)
        result.message = self._redact_sensitive(result.message)
        # QA-007: 使用 "is not None" 而非真值检查，避免 falsy 数据（空 dict/list/0）
        # 绕过脱敏分支
        if result.data is not None and isinstance(result.data, dict):
            result.data = self._redact_dict(result.data)
        elif result.data is not None and isinstance(result.data, list):
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
                    if pattern.search(v):
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
                    if pattern.search(item):
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


# ── C5: 存储前脱敏 ─────────────────────────────────────────

def sanitize_for_storage(data: dict) -> dict:
    """递归脱敏 dict/list 中的敏感信息，用于持久化到磁盘前的安全处理。

    脱敏范围：
    - API 密钥 / Token / 凭据 (复用 _SENSITIVE_PATTERNS)
    - 文件系统路径 (Windows 绝对路径、Unix 常见目录路径)

    就地变异并返回同一对象。调用方应在 JSON 序列化之前调用此函数。
    """
    _ALL_STORAGE_PATTERNS = list(_SENSITIVE_PATTERNS) + _FILE_PATH_PATTERNS
    _redact_storage_recursive(data, _ALL_STORAGE_PATTERNS)
    return data


def _redact_str_for_storage(text: str, patterns: list) -> str:
    """对单个字符串应用全部脱敏模式。"""
    for pattern, label in patterns:
        if pattern.search(text):
            logger.debug("sanitize_for_storage: 脱敏 %s", label)
            text = pattern.sub("***REDACTED***", text)
    return text


def _redact_storage_recursive(obj, patterns: list) -> None:
    """递归遍历 dict/list/tuple，对所有字符串值执行脱敏。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str):
                obj[k] = _redact_str_for_storage(v, patterns)
            elif isinstance(v, (dict, list)):
                _redact_storage_recursive(v, patterns)
            elif isinstance(v, tuple):
                lst = list(v)
                _redact_storage_recursive(lst, patterns)
                obj[k] = tuple(lst)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            if isinstance(item, str):
                obj[i] = _redact_str_for_storage(item, patterns)
            elif isinstance(item, (dict, list)):
                _redact_storage_recursive(item, patterns)
            elif isinstance(item, tuple):
                lst = list(item)
                _redact_storage_recursive(lst, patterns)
                obj[i] = tuple(lst)
