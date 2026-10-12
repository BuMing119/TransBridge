"""Reflexion failure analysis for bounded tool retries."""

from collections.abc import Callable, Mapping
from copy import deepcopy
import json
import logging
import re
from typing import Any

from transbridge.application.security import (
    SecretRedactor,
    SecretValue,
    diagnostic_exception_text,
    project_business_data,
)
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

    @classmethod
    def _sanitize_args(cls, args, schema=None):
        """Project declared inputs, hiding only explicitly identified credentials."""
        if cls._is_credential(args, schema):
            return SecretRedactor.REDACTED
        if isinstance(args, dict):
            projected = {}
            for key, value in args.items():
                allowed, child_schema = cls._schema_property(schema, str(key))
                if allowed:
                    projected[key] = cls._sanitize_args(value, child_schema)
            return projected
        if isinstance(args, (list, tuple)):
            item_schema = schema.get("items") if isinstance(schema, Mapping) else None
            return [cls._sanitize_args(value, item_schema) for value in args]
        return project_business_data(args)

    @staticmethod
    def _is_credential(value, schema) -> bool:
        return isinstance(value, SecretValue) or (isinstance(schema, Mapping) and schema.get("writeOnly") is True)

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
        failure_payload = failure.to_dict() if isinstance(failure, ToolResult) else {"message": str(failure)}
        safe_failure = project_business_data(failure_payload)
        safe_args = self._sanitize_args(original_args, tool_schema)
        safe_schema = project_business_data(tool_schema or {})
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
            try:
                parsed = json.loads(response)
            except json.JSONDecodeError:
                # Accept explanatory prose/code fences, but validate complete JSON values as-is.
                start = response.find("{")
                end = response.rfind("}") + 1
                if start < 0 or end <= start:
                    raise
                parsed = json.loads(response[start:end])
        except Exception as exc:
            safe_error = diagnostic_exception_text(exc)
            logger.warning("RetryHandler LLM 响应解析失败: %s", safe_error)
            return None

        if not isinstance(parsed, dict) or type(parsed.get("retry")) is not bool:
            logger.warning("RetryHandler response must be an object with a boolean retry field")
            return None

        if parsed["retry"]:
            adjusted = parsed.get("adjusted_args")
            if isinstance(adjusted, dict):
                safe_step["args"] = self._restore_sensitive_values(original_args, adjusted, tool_schema)
            else:
                logger.warning(
                    "LLM 返回的 adjusted_args 不是 dict 类型 (got %s)，停止重试",
                    type(adjusted).__name__,
                )
                return None
            return safe_step
        return None

    @classmethod
    def _restore_sensitive_values(cls, original: Any, adjusted: Any, schema: Any = None) -> Any:
        """Preserve only schema-authorized credentials and reject model-created secrets."""
        if cls._is_credential(original, schema):
            return deepcopy(original)
        if isinstance(adjusted, dict):
            baseline = original if isinstance(original, dict) else {}
            restored = {}
            for key in baseline.keys() | adjusted.keys():
                allowed, child_schema = cls._schema_property(schema, str(key))
                if not allowed:
                    continue
                value = baseline.get(key)
                if cls._is_credential(value, child_schema):
                    if key in baseline:
                        restored[key] = deepcopy(value)
                elif key in adjusted:
                    restored[key] = cls._restore_sensitive_values(value, adjusted[key], child_schema)
            return restored
        if isinstance(adjusted, list):
            baseline = original if isinstance(original, (list, tuple)) else ()
            item_schema = schema.get("items") if isinstance(schema, Mapping) else None
            return [
                cls._restore_sensitive_values(baseline[i] if i < len(baseline) else None, value, item_schema)
                for i, value in enumerate(adjusted)
            ]
        return deepcopy(adjusted)

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
            safe_error = diagnostic_exception_text(exc)
            logger.warning("RetryHandler LLM client 获取失败: %s", safe_error)
            return None
