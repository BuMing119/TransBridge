"""Bounded scheduling for independent terminology refinement batches."""

from collections.abc import Callable, Iterator
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from .ai_request_budget import AiRequestCancelledError
from .postprocess import PostProcessCandidate
from .token_batching import ContentBatch

RefinementBatch = ContentBatch[PostProcessCandidate]


def iter_refinement_results(
    batches: tuple[RefinementBatch, ...],
    refine: Callable[[RefinementBatch], object],
    *,
    max_workers: int,
    is_cancelled: Callable[[], bool],
) -> Iterator[tuple[RefinementBatch, object]]:
    """Yield completed batches; closing the iterator drains active work safely.

    At most ``max_workers`` futures exist at a time. Callers validate a yielded
    result before advancing, so cancellation results cannot enqueue more work.
    Provider admission, including pause and the shared budget, remains in the
    configured LLM client.
    """

    def execute(batch: RefinementBatch) -> object:
        try:
            if is_cancelled():
                raise AiRequestCancelledError("Terminology refinement cancelled before batch")
            result = refine(batch)
            if is_cancelled():
                raise AiRequestCancelledError("Terminology refinement cancelled during batch")
            return result
        except Exception as exc:
            return exc

    if max_workers == 1:
        for batch in batches:
            yield batch, execute(batch)
            if is_cancelled():
                return
        return

    pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="terminology-refinement")
    pending = {}
    remaining = iter(batches)
    exhausted = False
    try:
        while pending or not exhausted:
            if is_cancelled():
                batch = next(iter(pending.values()), None) or next(remaining, None)
                if batch is not None:
                    yield batch, AiRequestCancelledError("Terminology refinement cancelled while scheduling")
                return
            while len(pending) < max_workers and not exhausted and not is_cancelled():
                batch = next(remaining, None)
                if batch is None:
                    exhausted = True
                else:
                    pending[pool.submit(execute, batch)] = batch
            if not pending:
                continue
            done, _ = wait(pending, timeout=0.05, return_when=FIRST_COMPLETED)
            for future in sorted(done, key=lambda item: pending[item].index):
                batch = pending.pop(future)
                yield batch, future.result()
    finally:
        for future in pending:
            future.cancel()
        pool.shutdown(wait=True, cancel_futures=True)
