"""Shared bounded retry loop for Smart Assistant tool invocations."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from transbridge.application.security.redaction import SecretRedactor
from transbridge.smart_assistant.tools.base import ToolResult

from .retry_handler import RetryHandler


@dataclass(frozen=True)
class RetryOutcome:
    """Final normalized result and the invocation state that produced it."""

    result: ToolResult
    step: dict[str, Any]
    attempts: int


class ToolRetryExecutor:
    """Execute one tool with bounded, policy-gated Reflexion retries."""

    def __init__(self, retry_handler: RetryHandler | None = None) -> None:
        self._retry_handler = retry_handler

    def execute(
        self,
        step: dict[str, Any],
        invoke: Callable[[dict[str, Any]], Any],
        *,
        retry_allowed: bool,
        tool_schema: dict[str, Any] | None = None,
        cancelled: Callable[[], bool] | None = None,
        on_retry: Callable[[int, int, ToolResult], None] | None = None,
    ) -> RetryOutcome:
        current_step = deepcopy(step)
        max_retries = self._retry_handler.MAX_RETRIES if self._retry_handler is not None else 0
        max_attempts = max_retries + 1

        for attempt in range(1, max_attempts + 1):
            if cancelled is not None and cancelled():
                result = ToolResult.fail(
                    "工具调用已取消",
                    error_category="cancelled",
                    error_code="TOOL_CALL_CANCELLED",
                )
                return RetryOutcome(self._with_attempts(result, attempt), current_step, attempt)

            result = self._invoke(invoke, current_step)
            if result.success:
                return RetryOutcome(self._with_attempts(result, attempt), current_step, attempt)

            if (
                not retry_allowed
                or self._retry_handler is None
                or attempt >= max_attempts
                or not self._retry_handler.should_retry(
                    result.message,
                    error_category=result.error_category,
                    error_code=result.error_code,
                )
            ):
                return RetryOutcome(self._with_attempts(result, attempt), current_step, attempt)

            if self._retry_handler.should_retry_same_args(
                result.message,
                error_category=result.error_category,
                error_code=result.error_code,
            ):
                adjusted_step = deepcopy(current_step)
            else:
                adjusted_step = self._retry_handler.analyze_and_adjust(
                    current_step,
                    result,
                    attempt - 1,
                    tool_schema=tool_schema,
                )

            if not self._is_valid_adjustment(current_step, adjusted_step):
                return RetryOutcome(self._with_attempts(result, attempt), current_step, attempt)

            if on_retry is not None:
                on_retry(attempt + 1, max_attempts, result)
            current_step = adjusted_step

        raise RuntimeError("unreachable retry loop state")

    @staticmethod
    def _invoke(invoke: Callable[[dict[str, Any]], Any], step: dict[str, Any]) -> ToolResult:
        try:
            raw_result = invoke(dict(step.get("args", {})))
        except Exception as exc:
            safe_message = SecretRedactor.default().redact_text(str(exc))
            return ToolResult.fail(
                f"执行异常: {safe_message}",
                error_category="internal",
                error_code=type(exc).__name__,
            )
        return ToolRetryExecutor._normalize_result(raw_result)

    @staticmethod
    def _normalize_result(raw_result: Any) -> ToolResult:
        if isinstance(raw_result, ToolResult):
            return raw_result
        if isinstance(raw_result, dict):
            return ToolResult(
                success=bool(raw_result.get("success", True)),
                message=str(raw_result.get("message", raw_result.get("error", ""))),
                data=raw_result.get("data"),
                failed_items=raw_result.get("failed_items"),
                truncated=bool(raw_result.get("truncated", False)),
                partial=bool(raw_result.get("partial", False)),
                error_category=raw_result.get("error_category"),
                error_code=raw_result.get("error_code"),
                recovery_action=raw_result.get("recovery_action"),
                warnings=raw_result.get("warnings"),
                pagination=raw_result.get("pagination"),
                execution_meta=raw_result.get("execution_meta"),
                tool_suggestions=raw_result.get("tool_suggestions"),
            )
        return ToolResult.fail(str(raw_result), error_category="internal", error_code="INVALID_TOOL_RESULT")

    @staticmethod
    def _is_valid_adjustment(original: dict[str, Any], adjusted: dict[str, Any] | None) -> bool:
        if not isinstance(adjusted, dict) or adjusted.get("tool") != original.get("tool"):
            return False
        return isinstance(adjusted.get("args"), dict)

    @staticmethod
    def _with_attempts(result: ToolResult, attempts: int) -> ToolResult:
        if attempts == 1 and result.success:
            return result
        execution_meta = dict(result.execution_meta or {})
        execution_meta.update({"attempt": attempts, "retry_count": attempts - 1})
        result.execution_meta = execution_meta
        return result
