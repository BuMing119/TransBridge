"""Output validation backed by the application-wide SecretRedactor."""

from __future__ import annotations

import json
import logging

from transbridge.application.security.redaction import SecretRedactor

from .base import GuardMiddleware, GuardResult

logger = logging.getLogger(__name__)

_MAX_MESSAGE_LEN = 10240
_DEFAULT_MAX_OUTPUT = 102400


class OutputValidationGuard(GuardMiddleware):
    def __init__(
        self,
        max_output_size: int = _DEFAULT_MAX_OUTPUT,
        redactor: SecretRedactor | None = None,
    ) -> None:
        self._max_output_size = max_output_size
        self._redactor = redactor or SecretRedactor.default()

    def before_execute(self, step, ctx) -> GuardResult:
        return GuardResult(True)

    def after_execute(self, step, result, ctx) -> GuardResult:
        if not isinstance(result.data, (dict, list, str)) and result.data is not None:
            return GuardResult(
                False,
                f"输出类型错误: 期望 dict/list/str，实际 {type(result.data).__name__}",
                code="OUTPUT_TYPE_INVALID",
            )

        message = self._redactor.redact_text(result.message or "")
        if len(message) > _MAX_MESSAGE_LEN:
            message = message[:_MAX_MESSAGE_LEN] + "...(截断)"
        result.message = message
        result.data = self._redactor.redact(result.data)

        if result.data is not None:
            try:
                serialized = json.dumps(result.data, default=str, ensure_ascii=False)
                if len(serialized.encode("utf-8", errors="replace")) > self._max_output_size:
                    # Preserve the authoritative structure; only the display projection
                    # is bounded later by ToolResult.to_structured_observation().
                    setattr(result, "display_truncated", True)
                    logger.info("OutputValidation: display projection will be truncated")
            except (TypeError, ValueError):
                logger.warning("OutputValidation: 序列化失败，跳过大小校验", exc_info=True)
        return GuardResult(True)


def sanitize_for_storage(data: dict) -> dict:
    """Return a storage-safe copy using the same redaction implementation."""

    return SecretRedactor(redact_paths=True).redact(data)
