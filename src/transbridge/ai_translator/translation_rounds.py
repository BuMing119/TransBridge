"""Run existing translation rounds through bounded, dependency-aware workers."""

from __future__ import annotations

from contextlib import nullcontext
import time

from transbridge.application.translation.entry_alias import ai_entry_key

from .batch_execution import BatchExecutor, quest_batch_chains


def run_translation_rounds(
    plan,
    *,
    completed_fingerprints,
    operation,
    max_workers,
    executor=None,
    lane_key=None,
    stop_event,
    pause_event,
    log,
):
    """Preserve per-source round barriers and release Quest successors per batch."""
    with nullcontext(executor) if executor is not None else BatchExecutor(max_workers) as workers:
        for name, batches, description, ordered in (
            ("第一轮", plan.round1, "专有名词", False),
            ("第二轮", plan.round2, "对话", True),
            ("第三轮", plan.round3, "长文本", False),
        ):
            if stop_event.is_set():
                break
            pending = [
                batch
                for batch in batches
                if frozenset(ai_entry_key(entry) for entry in batch.entries) not in completed_fingerprints
            ]
            if not pending:
                continue
            started = time.perf_counter()
            log(f"\n── {name}开始（{len(pending)} 批，{description}）──")
            chains = quest_batch_chains(pending) if ordered else ([batch] for batch in pending)
            workers.run(
                chains,
                lambda batch, round_name=name: operation(batch, round_name),
                lane_key=lane_key,
                stop_event=stop_event,
                pause_event=pause_event,
            )
            state = "已停止" if stop_event.is_set() else "完成"
            log(f"── {name}{state}: {time.perf_counter() - started:.2f}s ──\n")
