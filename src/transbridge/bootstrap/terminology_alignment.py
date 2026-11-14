# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Freeze existing AI settings for an explicitly requested position judgment."""

from contextlib import contextmanager
from dataclasses import dataclass, field

from transbridge.application.translation.ai_request_budget import AiRequestBudget
from transbridge.infra.limited_llm_client import LimitedLLMClient
from transbridge.infra.llm_reasoning import ReasoningIntent, with_reasoning_intent
from transbridge.infra.terminology_occurrence_aligner import TerminologyOccurrenceAligner
from transbridge.infra.token_counting import TiktokenContentTokenCounter


def prepare_alignment(*, config_loader=None, client_factory=None):
    from transbridge.config.llm import LLMConfig
    from transbridge.infra.llm_client import create_llm_client
    from transbridge.infra.llm_request_config import resolve_anthropic_endpoint

    config = (config_loader or LLMConfig.load_from_file)().copy_for_execution()
    if not config.model.strip() or not config.api_key.strip():
        raise ValueError("AI 定位需要模型名称和 API 密钥，请在设置 → AI 服务中配置。")
    if config.provider == "anthropic":
        config.base_url = resolve_anthropic_endpoint(config.base_url)
    elif not config.base_url.strip():
        raise ValueError("AI 定位需要服务地址，请在设置 → AI 服务中配置。")
    if (
        type(config.max_concurrent) is not int
        or config.max_concurrent < 1
        or type(config.max_tokens_per_batch) is not int
        or config.max_tokens_per_batch < 1
        or type(config.max_output_tokens) is not int
        or config.max_output_tokens < 0
        or type(config.llm_max_retries) is not int
        or config.llm_max_retries < 0
    ):
        raise ValueError("请检查 AI 服务中的并发数、输入/输出 Token 上限和重试次数。")
    return PreparedAlignment(config, client_factory or create_llm_client)


@dataclass(frozen=True, slots=True)
class PreparedAlignment:
    _config: object = field(repr=False)
    _client_factory: object = field(repr=False)

    @property
    def display_label(self):
        return f"{self._config.provider} / {self._config.model}"

    @contextmanager
    def open(self):
        raw = self._client_factory(self._config)
        try:
            client = LimitedLLMClient(
                with_reasoning_intent(raw, self._config, ReasoningIntent.PREFER_DIRECT),
                AiRequestBudget(self._config.max_concurrent),
            )
            yield TerminologyOccurrenceAligner(
                client,
                max_input_tokens=self._config.max_tokens_per_batch,
                max_output_tokens=self._config.max_output_tokens,
                token_counter=TiktokenContentTokenCounter(self._config.model),
                max_concurrent=self._config.max_concurrent,
            )
        finally:
            close = getattr(raw, "close", None)
            if close is not None:
                close()


__all__ = ["PreparedAlignment", "prepare_alignment"]
