"""Reflexion 自纠错处理器：工具失败 → LLM分析 → 调整参数 → 重试。"""

import json
import logging

logger = logging.getLogger("RetryHandler")


class RetryHandler:
    MAX_RETRIES = 3
    NON_RETRYABLE = [
        "timeout", "connection", "network", "refused",
        "unreachable", "401", "403", "429",
    ]

    def __init__(self, llm_client=None):
        self._llm = llm_client

    def should_retry(self, error: str) -> bool:
        err_lower = error.lower()
        return not any(kw in err_lower for kw in self.NON_RETRYABLE)

    def analyze_and_adjust(self, step: dict, error: str, attempt: int) -> dict | None:
        if self._llm is None:
            return None
        prompt = (
            f"工具执行失败 (第 {attempt + 1}/{self.MAX_RETRIES} 次):\n"
            f"工具: {step.get('tool', '?')}\n"
            f"参数: {json.dumps(step.get('args', {}), ensure_ascii=False)}\n"
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
        except (json.JSONDecodeError, Exception) as exc:
            logger.warning(f"RetryHandler LLM 响应解析失败: {exc}")
            # 降级：直接重试（不调整参数）
            return step if attempt < self.MAX_RETRIES else None

        if parsed.get("retry"):
            step["args"] = parsed.get("adjusted_args", step.get("args", {}))
            return step
        return None
