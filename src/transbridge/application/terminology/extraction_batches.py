# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Bounded extraction batches with disposable successful-response reuse."""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, wait
from dataclasses import asdict
import json
from time import monotonic

from transbridge.application.translation.batch_execution import BatchExecutor

from .extraction import LlmTermProposal
from .extraction_execution import ExtractionBatchResult
from .identity import canonical_digest
from .workloads import TerminologyPhase, TerminologyProgress


class ConcurrentExtractionBatches:
    def __init__(self, llm, planner, *, cache, identity, progress, max_concurrent, force=False):
        self._llm = llm
        self._planner = planner
        self._cache = cache
        self._identity = identity
        self._progress = progress
        self._limit = max_concurrent
        self._force = force
        if type(max_concurrent) is not int or max_concurrent < 1:
            raise ValueError("terminology concurrency must be positive")

    def run(self, inputs, cancellation):
        batches = tuple(self._planner(inputs))
        started = monotonic()
        submitted = completed = reused = failed = 0
        pending = {}
        remaining = iter(enumerate(batches))
        exhausted = False
        pool = BatchExecutor(self._limit)

        def cancelled():
            return cancellation is not None and cancellation.is_cancelled

        def report():
            self._progress(
                TerminologyProgress(
                    TerminologyPhase.EXTRACT,
                    completed + reused,
                    len(batches),
                    current_object=f"LLM 抽取：复用 {reused} 批，失败 {failed} 批",
                    reused=reused,
                    recomputed=submitted,
                    llm_submitted=submitted,
                    llm_completed=completed,
                    llm_waiting=len(pending),
                    llm_elapsed_ms=int((monotonic() - started) * 1000),
                )
            )

        try:
            report()
            while not exhausted or pending:
                if cancelled():
                    return
                while not exhausted and len(pending) < self._limit:
                    if cancelled():
                        return
                    item = next(remaining, None)
                    if item is None:
                        exhausted = True
                        break
                    index, batch = item
                    key = canonical_digest(
                        {"configuration": self._identity, "inputs": batch},
                        namespace="terminology.llm-batch.v1",
                    )
                    cached = None if self._force else self._read(key)
                    if cached is not None:
                        reused += 1
                        report()
                        yield ExtractionBatchResult(batch, cached)
                        continue
                    if self._force:
                        self._cache.discard("extraction", key)
                    pending[pool.submit(self._llm.extract, batch)] = (index, batch, key)
                    submitted += 1
                    report()
                done, _ = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED) if pending else ((), ())
                for future in sorted(done, key=lambda value: pending[value][0]):
                    index, batch, key = pending.pop(future)
                    if cancelled():
                        return
                    completed += 1
                    try:
                        proposals = future.result()
                    except Exception as exc:  # provider boundary; retain batch/type without leaking request secrets
                        failed += 1
                        result = ExtractionBatchResult(batch, error=f"{index + 1}:{type(exc).__name__}")
                    else:
                        if cancelled():
                            return
                        self._cache.discard("extraction", key)
                        self._cache.put(
                            "extraction",
                            key,
                            json.dumps([asdict(item) for item in proposals], ensure_ascii=False).encode("utf-8"),
                        )
                        result = ExtractionBatchResult(batch, proposals)
                    report()
                    yield result
        finally:
            for future in pending:
                future.cancel()
            pool.shutdown(wait=False)
            try:
                if cancelled():
                    self._llm.cancel()
            finally:
                pool.shutdown(wait=True)

    def _read(self, key):
        entry = self._cache.get("extraction", key)
        if entry is None:
            return None
        try:
            values = json.loads(entry.payload)
            if not isinstance(values, list):
                return None
            proposals = tuple(LlmTermProposal(**value) for value in values)
            if any(
                not isinstance(value, str) or not value.strip()
                for proposal in proposals
                for value in (proposal.evidence_id, proposal.original, proposal.translation)
            ):
                return None
            return proposals
        except (TypeError, ValueError):
            return None
