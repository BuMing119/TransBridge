"""Reflexion 自纠错处理器：工具失败 → LLM分析 → 调整参数 → 重试。"""

import json
import logging

logger = logging.getLogger(__name__)


class RetryHandler:
    MAX_RETRIES = 3
    NON_RETRYABLE = [
        "timeout", "connection", "network", "refused",
        "unreachable", "401", "403", "429",
        "permission", "not found", "invalid", "unknown",
    ]

    def __init__(self, llm_client=None):
        self._llm = llm_client

    _SENSITIVE_KEYS = {"api_key", "token", "password", "secret", "key", "auth"}

    @classmethod
    def _sanitize_args(cls, args: dict) -> dict:
        """Redact sensitive values before sending to LLM."""
        if not isinstance(args, dict):
            return args
        sanitized = {}
        for k, v in args.items():
            if any(sk in k.lower() for sk in cls._SENSITIVE_KEYS):
                sanitized[k] = "[REDACTED]"
            elif isinstance(v, dict):
                sanitized[k] = cls._sanitize_args(v)
            else:
                sanitized[k] = v
        return sanitized

    def should_retry(self, error: str) -> bool:
        err_lower = error.lower()
        return not any(kw in err_lower for kw in self.NON_RETRYABLE)

    def analyze_and_adjust(self, step: dict, error: str, attempt: int) -> dict | None:
        if self._llm is None:
            return None
        prompt = (
            f"工具执行失败 (第 {attempt + 1}/{self.MAX_RETRIES} 次):\n"
            f"工具: {step.get('tool', '?')}\n"
            f"参数: {json.dumps(self._sanitize_args(step.get('args', {})), ensure_ascii=False)}\n"
            f"错误: {error}\n\n"
            f"请分析失败原因，决定是否调整参数重试。\n"
            f'返回 JSON: {{"retry": true/false, "adjusted_args": {{...}}, "reason": "..."}}'
        )
        try:
            response = self._llm.chat([{"role": "user", "content": prompt}], max_tokens=256)
            # 容错提取 JSON
            start = response.find("{")
            end = response.rfind("}") + 1
            if start >= 0 and end > start:
                parsed = json.loads(response[start:end])
            else:
                parsed = json.loads(response)
        except Exception as exc:
            logger.warning(f"RetryHandler LLM 响应解析失败: {exc}")
            # 降级：直接重试（不调整参数）
            return step if attempt < self.MAX_RETRIES else None

        if parsed.get("retry"):
            adjusted = parsed.get("adjusted_args")
            if isinstance(adjusted, dict):
                step["args"] = adjusted
            else:
                logger.warning(
                    "LLM 返回的 adjusted_args 不是 dict 类型 (got %s)，使用原参数重试",
                    type(adjusted).__name__,
                )
            return step
        return None
