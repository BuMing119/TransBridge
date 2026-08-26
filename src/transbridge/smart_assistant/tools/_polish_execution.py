"""Strategy adapter for Smart Assistant polish tasks."""

from __future__ import annotations

import copy
from dataclasses import dataclass, replace
from typing import Literal

PolishStrategy = Literal["combined", "strict"]


@dataclass(frozen=True, slots=True)
class PolishExecutionSummary:
    results: dict[str, object]
    polished_count: int
    failed_count: int


def execute_polish(
    *,
    strategy: PolishStrategy,
    intensity: str,
    llm_config: object,
    llm_client: object,
    term_manager: object,
    targets: list[object],
    collection: object,
    stop_event: object,
    progress_callback=None,
    log_callback=None,
) -> PolishExecutionSummary:
    """Run the shared proofread pipeline and commit only accepted candidates."""

    from transbridge.ai_translator.post_processor.proofread_pipeline import ProofreadPipeline
    from transbridge.application.translation.ai_execution_profile import (
        AiExecutionProfile,
        apply_profile_settings,
    )
    from transbridge.converter.translation_entry import TranslationEntry

    effective_config = copy.copy(llm_config)
    apply_profile_settings(effective_config, "polish")
    profile = AiExecutionProfile.from_config("polish", effective_config)
    level_map = {"light": "light", "medium": "moderate", "heavy": "aggressive"}
    profile = replace(
        profile,
        enable_post_process=True,
        postprocess_strategy=strategy,
        polish_level=level_map[intensity],
    )
    pipeline = ProofreadPipeline.create(
        profile=profile,
        llm_client=llm_client,
        term_manager=term_manager,
        model=str(getattr(llm_config, "model", "")),
        max_tokens_per_batch=int(getattr(llm_config, "max_tokens_per_batch", 2000)),
        max_output_tokens=int(getattr(llm_config, "max_output_tokens", 0)),
    )
    results = pipeline.process(
        targets,
        progress_callback=progress_callback,
        log_callback=log_callback,
        stop_event=stop_event,
        max_workers=max(1, int(getattr(llm_config, "max_concurrent", 1))),
    )
    polished_count = 0
    failed_count = 0
    for entry in targets:
        result = results.get(str(entry.id))
        if result is None or not result.accepted:
            failed_count += 1
            continue
        if result.polished_translation != (entry.translation or ""):
            updated = TranslationEntry(
                id=entry.id,
                key=entry.key,
                original=entry.original,
                translation=result.polished_translation,
                stage=entry.stage,
                context=entry.context,
            )
            collection.add(updated, overwrite=True)
            polished_count += 1
    return PolishExecutionSummary(results, polished_count, failed_count)


__all__ = ["PolishExecutionSummary", "PolishStrategy", "execute_polish"]
