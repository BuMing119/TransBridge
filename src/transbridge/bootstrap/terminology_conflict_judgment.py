# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Run conflict judgment with frozen model settings after extraction reuse."""

import logging

from transbridge.application.terminology.conflict_judgment import (
    ConflictJudgment,
    check_judgment_cancelled,
)
from transbridge.application.terminology.workloads import TerminologyPhase, TerminologyProgress
from transbridge.application.translation.ai_request_budget import AiRequestBudget
from transbridge.infra.limited_llm_client import LimitedLLMClient
from transbridge.infra.llm_reasoning import ReasoningIntent, with_reasoning_intent
from transbridge.infra.terminology_conflict_judge import CONFLICT_MAX_CONCURRENT, TerminologyConflictJudge
from transbridge.infra.token_counting import TiktokenContentTokenCounter

logger = logging.getLogger(__name__)


class ProductionConflictJudge:
    def __init__(self, config, client_factory, progress):
        self._config = config
        self._client_factory = client_factory
        self._progress = progress

    def judge(self, conflicts, evidence, *, cancellation=None):
        check_judgment_cancelled(cancellation)
        if not conflicts:
            return ()
        self._report(0, len(conflicts))
        try:
            raw = self._client_factory(self._config)
        except Exception as exc:  # noqa: BLE001 - preserve usable extraction on provider initialization failure
            logger.warning("Cannot initialize terminology conflict model", exc_info=True)
            check_judgment_cancelled(cancellation)
            return tuple(
                ConflictJudgment(
                    group.conflict_group_id,
                    "uncertain",
                    f"冲突判断模型初始化失败（{type(exc).__name__}），请检查 AI 服务后重新提取。",
                    execution_status="not_run",
                    request_count=0,
                )
                for group in conflicts
            )
        try:
            client = LimitedLLMClient(
                with_reasoning_intent(raw, self._config, ReasoningIntent.PREFER_DIRECT),
                AiRequestBudget(CONFLICT_MAX_CONCURRENT),
            )
            return TerminologyConflictJudge(
                client,
                max_input_tokens=self._config.max_tokens_per_batch,
                max_output_tokens=self._config.max_output_tokens,
                token_counter=TiktokenContentTokenCounter(self._config.model),
                progress=self._report,
            ).judge(conflicts, evidence, cancellation=cancellation)
        finally:
            close = getattr(raw, "close", None)
            if close is not None:
                close()

    def _report(self, completed, total):
        if self._progress is not None:
            self._progress(
                TerminologyProgress(
                    TerminologyPhase.REDUCE,
                    completed,
                    total,
                    current_object=f"LLM 冲突判断：{completed}/{total} 组（独立于抽取缓存）",
                )
            )
