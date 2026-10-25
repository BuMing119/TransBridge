"""Bounded parallel source lifecycles with stable result ownership."""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import logging

logger = logging.getLogger(__name__)


def run_sources(tasks, executor, *, stop_event, pause_event, started, max_concurrent, finished=None):
    from .source_execution import SourceOutcome

    tasks = tuple(tasks)
    results = {}
    next_index = 0
    # Workflow threads coordinate stages, never grant their own model quota.
    capacity = min(len(tasks), max(2, max_concurrent))
    if not capacity:
        return ()

    def execute(index):
        while not pause_event.wait(0.05):
            if stop_event.is_set():
                return None
        if stop_event.is_set():
            return None
        task = tasks[index]
        started(task.key)
        try:
            return executor.execute(task)
        except Exception as exc:
            from transbridge.application.translation.entry_alias import ai_entry_key

            logger.exception("AI source workflow failed: %s", task.key)
            return SourceOutcome(
                task,
                error=str(exc),
                failed_keys=tuple(ai_entry_key(e) for e in task.entries),
                cancelled=stop_event.is_set(),
            )

    with ThreadPoolExecutor(max_workers=capacity, thread_name_prefix="ai-source") as pool:
        pending = {}
        while pending or (next_index < len(tasks) and not stop_event.is_set()):
            while len(pending) < capacity and next_index < len(tasks) and not stop_event.is_set():
                pending[pool.submit(execute, next_index)] = next_index
                next_index += 1
            if not pending:
                break
            ready, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in ready:
                index = pending.pop(future)
                result = future.result()
                if result is not None:
                    results[index] = result
                    if finished is not None:
                        finished(tasks[index].key)
    return tuple(results[index] for index in sorted(results))
