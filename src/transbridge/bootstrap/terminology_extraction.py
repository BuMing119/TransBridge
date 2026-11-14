# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Freeze optional model execution at the project terminology capture boundary."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field, replace

from transbridge.application.contracts import DomainError, ErrorCategory, OperationResult


class ProductionTerminologyBuildInputs:
    def __init__(self, delegate, *, config_loader=None, client_factory=None, prompt_factory=None):
        self._delegate = delegate
        self._config_loader = config_loader
        self._client_factory = client_factory
        self._prompt_factory = prompt_factory

    def capture_build_input(self, context, *, config):
        options = dict(config)
        enabled = options.get("llm_enabled", False)
        force = options.pop("force_llm", False)
        if type(enabled) is not bool or type(force) is not bool:
            raise ValueError("术语抽取模式必须为布尔值。")
        prepared = None
        if not enabled:
            options.pop("llm_enabled", None)
            options.pop("terminology_extraction", None)
            options.pop("terminology_conflict_judgment", None)
        if enabled:
            try:
                prepared = self._prepare(force)
                options["terminology_extraction"] = prepared.identity
                options["terminology_conflict_judgment"] = prepared.judgment_identity
            except (ValueError, OSError) as exc:
                return OperationResult.failed(
                    DomainError(ErrorCategory.INPUT, "TERMINOLOGY_LLM_CONFIG_INVALID", str(exc), cause=exc),
                    run_id=context.run_id,
                )
        result = self._delegate.capture_build_input(context, config=options)
        if result.value is None:
            return result
        return replace(result, value=replace(result.value, extraction=prepared))

    def _prepare(self, force):
        from transbridge.ai_translator.prompt_builder import PromptBuilder
        from transbridge.config.llm import LLMConfig
        from transbridge.infra.llm_client import create_llm_client
        from transbridge.infra.llm_request_config import resolve_anthropic_endpoint
        from transbridge.infra.project_terminology_llm import ProjectTerminologyPrompt

        config = (self._config_loader or LLMConfig.load_from_file)().copy_for_execution()
        if not config.model.strip() or not config.api_key.strip():
            raise ValueError("LLM 术语抽取需要模型名称和 API 密钥。请在设置 → AI 服务中配置，或选择仅规则整理。")
        if config.provider == "anthropic":
            config.base_url = resolve_anthropic_endpoint(config.base_url)
        if config.max_concurrent < 1 or config.max_tokens_per_batch < 1 or config.llm_max_retries < 0:
            raise ValueError("请检查 AI 服务中的并发数、批次 Token 上限和重试次数。")
        prompt = ProjectTerminologyPrompt(
            (self._prompt_factory or PromptBuilder)(config.game_profile, config.target_lang)
        )
        return PreparedProjectExtraction(config, prompt, self._client_factory or create_llm_client, force)


@dataclass(frozen=True, slots=True)
class PreparedProjectExtraction:
    _config: object = field(repr=False)
    _prompt: object = field(repr=False)
    _client_factory: object = field(repr=False)
    force: bool = False

    @property
    def display_label(self):
        return f"{self._config.provider} / {self._config.model}"

    @property
    def identity(self):
        return {
            "version": "project-terminology-llm.v1",
            "settings": {
                name: getattr(self._config, name)
                for name in (
                    "provider",
                    "base_url",
                    "model",
                    "game_profile",
                    "target_lang",
                    "max_tokens_per_batch",
                    "max_output_tokens",
                    "temperature",
                    "llm_max_retries",
                )
            },
            "prompt": self._prompt.identity(),
        }

    @property
    def judgment_identity(self):
        from transbridge.infra.terminology_conflict_judge import JUDGMENT_PROMPT, JUDGMENT_SCHEMA

        return {"version": "v1", "prompt": JUDGMENT_PROMPT, "schema": JUDGMENT_SCHEMA.schema}

    def conflict_judge(self, *, progress):
        from .terminology_conflict_judgment import ProductionConflictJudge

        return ProductionConflictJudge(self._config, self._client_factory, progress)

    @contextmanager
    def open(self, *, cancellation, progress, cache):
        from transbridge.application.io.identity import EntryKey, SourceNamespace
        from transbridge.application.terminology.extraction import TerminologyExtractionService
        from transbridge.application.terminology.extraction_batches import ConcurrentExtractionBatches
        from transbridge.application.translation.ai_request_budget import AiRequestBudget
        from transbridge.application.translation.token_batching import StableContentBatcher
        from transbridge.infra.limited_llm_client import LimitedLLMClient
        from transbridge.infra.llm_reasoning import ReasoningIntent, with_reasoning_intent
        from transbridge.infra.project_terminology_llm import ProjectTerminologyLlm
        from transbridge.infra.token_counting import TiktokenContentTokenCounter

        config = self._config
        raw = self._client_factory(config)
        try:
            client = LimitedLLMClient(
                with_reasoning_intent(raw, config, ReasoningIntent.PREFER_DIRECT),
                AiRequestBudget(config.max_concurrent),
                cancel_event=_CancellationEvent(cancellation),
            )
            llm = ProjectTerminologyLlm(client, self._prompt, max_output_tokens=config.max_output_tokens)
            batcher = StableContentBatcher(
                TiktokenContentTokenCounter(config.model), config.max_tokens_per_batch, max_items=50
            )

            def plan(inputs):
                planned = batcher.plan(
                    inputs,
                    key=lambda item: EntryKey(SourceNamespace("terminology-evidence"), item.evidence_id),
                    content=lambda item: (item.original, item.translation, item.context, item.evidence_id),
                )
                return tuple(batch.items for batch in planned.batches)

            runner = ConcurrentExtractionBatches(
                llm,
                plan,
                cache=cache,
                identity=self.identity,
                progress=progress,
                max_concurrent=config.max_concurrent,
                force=self.force,
            )
            yield TerminologyExtractionService(batch_runner=runner)
        finally:
            close = getattr(raw, "close", None)
            if close is not None:
                close()


class _CancellationEvent:
    def __init__(self, cancellation):
        self._cancellation = cancellation

    def is_set(self):
        return self._cancellation is not None and self._cancellation.is_cancelled
