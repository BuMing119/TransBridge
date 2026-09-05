"""Reflexion failure analysis for bounded tool retries."""

from collections.abc import Callable, Mapping
from copy import deepcopy
import json
import logging
import re
from typing import Any

from transbridge.application.security.redaction import SecretRedactor
from transbridge.smart_assistant.tools.base import ToolResult

logger = logging.getLogger(__name__)


class RetryHandler:
    MAX_RETRIES = 3
    NON_RETRYABLE_CATEGORIES = {"auth", "cancelled", "config", "permission"}
    NON_RETRYABLE_CODES = {
        "API_KEY_MISSING",
        "CAPABILITY_UNAVAILABLE",
        "CONFIRMATION_CHANNEL_UNAVAILABLE",
        "CONFIRMATION_DENIED",
        "CONFIRMATION_EXPIRED",
        "TOOL_CALL_CANCELLED",
    }
    NON_RETRYABLE_PHRASES = (
        "401",
        "403",
        "permission",
        "unauthorized",
        "unknown tool",
        "capability unavailable",
        "confirmation denied",
        "用户拒绝",
        "工具调用已取消",
        "缺少确认通道",
        "认证失败",
        "未配置",
    )
    SAME_ARGS_CATEGORIES = {"network"}
    SAME_ARGS_CODES = {"CONNECTION_RESET", "NETWORK_ERROR", "RATE_LIMIT", "TIMEOUT"}
    SAME_ARGS_PHRASES = ("connection", "network", "rate limit", "timeout", "unreachable", "429")

    def __init__(self, llm_client=None, *, llm_client_provider: Callable[[], Any] | None = None):
        self._llm = llm_client
        self._llm_client_provider = llm_client_provider

    _SENSITIVE_KEYS = {
        "access_token",
        "api_key",
        "apikey",
        "auth",
        "authorization",
        "bearer_token",
        "credential",
        "password",
        "refresh_token",
        "secret",
        "token",
    }

    @classmethod
    def _sanitize_args(cls, args):
        """Redact sensitive values before sending to LLM."""
        if isinstance(args, dict):
            return {
                key: "[REDACTED]" if cls._is_sensitive_key(str(key)) else cls._sanitize_args(value)
                for key, value in args.items()
            }
        if isinstance(args, list):
            return [cls._sanitize_args(value) for value in args]
        if isinstance(args, tuple):
            return tuple(cls._sanitize_args(value) for value in args)
        return args

    @classmethod
    def _is_sensitive_key(cls, key: str) -> bool:
        normalized = key.strip().lower().replace("-", "_")
        return normalized in cls._SENSITIVE_KEYS or normalized.endswith((
            "_api_key",
            "_credential",
            "_password",
            "_secret",
            "_token",
        ))

    def should_retry(
        self,
        error: str,
        *,
        error_category: str | None = None,
        error_code: str | None = None,
    ) -> bool:
        category = str(error_category or "").lower()
        code = str(error_code or "").upper()
        if category in self.NON_RETRYABLE_CATEGORIES or code in self.NON_RETRYABLE_CODES:
            return False
        err_lower = error.lower()
        return not any(phrase in err_lower for phrase in self.NON_RETRYABLE_PHRASES)

    def should_retry_same_args(
        self,
        error: str,
        *,
        error_category: str | None = None,
        error_code: str | None = None,
    ) -> bool:
        category = str(error_category or "").lower()
        code = str(error_code or "").upper()
        if category in self.SAME_ARGS_CATEGORIES or code in self.SAME_ARGS_CODES:
            return True
        err_lower = error.lower()
        return any(phrase in err_lower for phrase in self.SAME_ARGS_PHRASES)

    def analyze_and_adjust(
        self,
        step: dict,
        failure: ToolResult | str,
        attempt: int,
        *,
        tool_schema: dict[str, Any] | None = None,
    ) -> dict | None:
        client = self._resolve_client()
        if client is None:
            return None
        safe_step = deepcopy(step)
        original_args = deepcopy(safe_step.get("args", {}))
        redactor = SecretRedactor.default()
        failure_payload = failure.to_dict() if isinstance(failure, ToolResult) else {"message": str(failure)}
        safe_failure = redactor.redact(failure_payload)
        safe_args = redactor.redact(self._sanitize_args(original_args))
        safe_schema = redactor.redact(self._sanitize_args(tool_schema or {}))
        prompt = (
            f"Tool execution failed (attempt {attempt + 1}/{self.MAX_RETRIES}):\n"
            f"Tool: {safe_step.get('tool', '?')}\n"
            f"Current arguments: {json.dumps(safe_args, ensure_ascii=False)}\n"
            f"Argument schema: {json.dumps(safe_schema, ensure_ascii=False)}\n"
            f"Structured failure: {json.dumps(safe_failure, ensure_ascii=False)}\n\n"
            "Analyze the cause and decide whether to retry with adjusted arguments.\n"
            "If retrying, adjusted_args must be the complete arguments object and conform to the schema. "
            "Keep existing redacted credentials only for fields allowed by the schema; remove schema-rejected "
            "unknown fields. Never invent or change credentials.\n"
            'Return JSON only: {"retry": true/false, "adjusted_args": {...}, "reason": "..."}'
        )
        try:
            response = client.chat([{"role": "user", "content": prompt}], max_tokens=256)
            # 容错提取 JSON
            start = response.find("{")
            end = response.rfind("}") + 1
            if start >= 0 and end > start:
                parsed = json.loads(response[start:end])
            else:
                parsed = json.loads(response)
        except Exception as exc:
            safe_error = SecretRedactor.default().redact_text(str(exc))
            logger.warning("RetryHandler LLM 响应解析失败: %s", safe_error)
            return None

        if parsed.get("retry"):
            adjusted = parsed.get("adjusted_args")
            if isinstance(adjusted, dict):
                safe_step["args"] = self._restore_sensitive_values(original_args, adjusted, tool_schema)
            else:
                logger.warning(
                    "LLM 返回的 adjusted_args 不是 dict 类型 (got %s)，使用原参数重试",
                    type(adjusted).__name__,
                )
                return None
            return safe_step
        return None

    @classmethod
    def _restore_sensitive_values(cls, original: Any, adjusted: Any, schema: Any = None) -> Any:
        """Preserve only schema-authorized credentials and reject model-created secrets."""
        if not isinstance(original, dict) or not isinstance(adjusted, dict):
            return adjusted
        restored = deepcopy(adjusted)
        for key in tuple(restored):
            if key not in original and cls._is_sensitive_key(str(key)):
                restored.pop(key)
        for key, value in original.items():
            allowed, child_schema = cls._schema_property(schema, str(key))
            if cls._is_sensitive_key(str(key)):
                if allowed:
                    restored[key] = deepcopy(value)
                else:
                    restored.pop(key, None)
            elif key in restored and allowed:
                restored[key] = cls._restore_sensitive_values(value, restored[key], child_schema)
        return restored

    @staticmethod
    def _schema_property(schema: Any, key: str) -> tuple[bool, Any]:
        """Return whether a property is allowed and the schema governing its value."""
        if not isinstance(schema, Mapping):
            return True, None
        properties = schema.get("properties")
        if isinstance(properties, Mapping) and key in properties:
            return True, properties[key]
        pattern_properties = schema.get("patternProperties")
        if isinstance(pattern_properties, Mapping):
            for pattern, child_schema in pattern_properties.items():
                if re.search(str(pattern), key):
                    return True, child_schema
        additional = schema.get("additionalProperties", True)
        if additional is False:
            return False, None
        return True, additional if isinstance(additional, Mapping) else None

    def _resolve_client(self):
        if self._llm_client_provider is None:
            return self._llm
        try:
            return self._llm_client_provider()
        except Exception as exc:
            safe_error = SecretRedactor.default().redact_text(str(exc))
            logger.warning("RetryHandler LLM client 获取失败: %s", safe_error)
            return None
