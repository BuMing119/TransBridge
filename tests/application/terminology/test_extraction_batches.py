from dataclasses import replace
from threading import Barrier, Event, Lock, Thread

import pytest

from tests.application.terminology.test_extraction import _evidence
from transbridge.application.tasks import CancellationToken
from transbridge.application.terminology.extraction import (
    LlmEvidenceInput,
    LlmTermProposal,
    TerminologyExtractionService,
)
from transbridge.application.terminology.extraction_batches import ConcurrentExtractionBatches
from transbridge.application.terminology.identity import canonical_digest
from transbridge.persistence.terminology.repository import SqliteTerminologyRepository


@pytest.fixture
def cache(tmp_path):
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-batches")
    try:
        yield repository.cache
    finally:
        repository.close()


def _inputs(count=2):
    return tuple(LlmEvidenceInput(f"e-{index}", "The dragon", "这条龙", "BOOK:DESC") for index in range(count))


def _runner(llm, cache, *, identity="model-a", force=False, progress=None, limit=1):
    return ConcurrentExtractionBatches(
        llm,
        lambda inputs: tuple((item,) for item in inputs),
        cache=cache,
        identity=identity,
        progress=progress or (lambda _value: None),
        max_concurrent=limit,
        force=force,
    )


class _Llm:
    def __init__(self):
        self.calls = []
        self.fail = set()
        self.translation = "龙"
        self.empty = False

    def extract(self, batch):
        identity = batch[0].evidence_id
        self.calls.append(identity)
        if identity in self.fail:
            raise TimeoutError("private provider details")
        return () if self.empty else (LlmTermProposal(identity, "dragon", self.translation),)

    def cancel(self):
        pass


def test_successful_batches_are_reused_while_only_failed_batches_retry(cache):
    llm = _Llm()
    llm.fail.add("e-1")
    first = list(_runner(llm, cache).run(_inputs(), None))
    assert first[0].proposals[0].translation == "龙"
    assert first[1].error.endswith("TimeoutError")
    assert "private" not in first[1].error

    llm.fail.clear()
    progress = []
    second = list(_runner(llm, cache, progress=progress.append).run(_inputs(), None))
    assert all(item.error is None for item in second)
    assert llm.calls == ["e-0", "e-1", "e-1"]
    assert progress[-1].reused == 1
    assert progress[-1].llm_completed == 1
    assert progress[-1].completed == 2


def test_forced_refresh_replaces_the_reusable_successful_result(cache):
    llm = _Llm()
    inputs = _inputs(1)
    list(_runner(llm, cache).run(inputs, None))
    llm.translation = "巨龙"
    refreshed = list(_runner(llm, cache, force=True).run(inputs, None))
    cached = list(_runner(llm, cache).run(inputs, None))
    assert refreshed == cached
    assert cached[0].proposals[0].translation == "巨龙"
    assert llm.calls == ["e-0", "e-0"]


def test_failed_forced_refresh_retries_instead_of_reusing_the_superseded_result(cache):
    llm = _Llm()
    inputs = _inputs(1)
    list(_runner(llm, cache).run(inputs, None))
    llm.fail.add("e-0")
    failed = list(_runner(llm, cache, force=True).run(inputs, None))
    assert failed[0].error is not None
    llm.fail.clear()
    llm.translation = "巨龙"
    retried = list(_runner(llm, cache).run(inputs, None))
    assert retried[0].proposals[0].translation == "巨龙"
    assert llm.calls == ["e-0", "e-0", "e-0"]


def test_model_prompt_or_input_changes_invalidate_batch_reuse(cache):
    llm = _Llm()
    inputs = _inputs(1)
    list(_runner(llm, cache, identity={"model": "a", "prompt": "one"}).run(inputs, None))
    list(_runner(llm, cache, identity={"model": "b", "prompt": "one"}).run(inputs, None))
    list(_runner(llm, cache, identity={"model": "b", "prompt": "two"}).run(inputs, None))
    changed = (replace(inputs[0], translation="这条巨龙"),)
    list(_runner(llm, cache, identity={"model": "b", "prompt": "two"}).run(changed, None))
    assert len(llm.calls) == 4


def test_empty_success_is_reusable_and_not_treated_as_a_failure(cache):
    llm = _Llm()
    llm.empty = True
    first = list(_runner(llm, cache).run(_inputs(1), None))
    second = list(_runner(llm, cache).run(_inputs(1), None))
    assert first == second
    assert first[0].proposals == ()
    assert first[0].error is None
    assert llm.calls == ["e-0"]


@pytest.mark.parametrize("payload", [b"not json", b"{}", b"[{}]", b'[{"evidence_id": 2}]'])
def test_corrupt_cached_payload_is_recomputed(cache, payload):
    inputs = _inputs(1)
    key = canonical_digest({"configuration": "model-a", "inputs": inputs}, namespace="terminology.llm-batch.v1")
    cache.put("extraction", key, payload)
    llm = _Llm()
    result = list(_runner(llm, cache).run(inputs, None))
    assert result[0].proposals[0].original == "dragon"
    assert llm.calls == ["e-0"]
    assert list(_runner(llm, cache).run(inputs, None)) == result
    assert llm.calls == ["e-0"]


def test_parallel_batches_obey_the_configured_admission_bound(cache):
    gate = Barrier(2)
    lock = Lock()
    active = peak = 0

    class ParallelLlm(_Llm):
        def extract(self, batch):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            try:
                gate.wait(timeout=3)
                return super().extract(batch)
            finally:
                with lock:
                    active -= 1

    llm = ParallelLlm()
    result = list(_runner(llm, cache, limit=2).run(_inputs(4), None))
    assert len(result) == 4
    assert all(item.error is None for item in result)
    assert peak == 2
    assert active == 0


def test_cancel_stops_refill_and_discards_late_success_without_caching(cache):
    cancellation = CancellationToken()
    entered = Event()
    release = Event()
    finished = Event()

    class BlockingLlm(_Llm):
        def extract(self, batch):
            entered.set()
            try:
                assert release.wait(3)
                return super().extract(batch)
            finally:
                finished.set()

        def cancel(self):
            release.set()

    def progress(value):
        if value.llm_submitted == 1:
            assert entered.wait(3)
            cancellation._cancel("test cancellation")

    llm = BlockingLlm()
    try:
        assert list(_runner(llm, cache, progress=progress).run(_inputs(3), cancellation)) == []
    finally:
        release.set()
        assert finished.wait(3)
    assert llm.calls == ["e-0"]
    assert cache.gc("extraction", max_entries=0) == 0


@pytest.mark.parametrize("cancel_raises", [False, True])
def test_cancel_retains_extraction_owner_until_provider_returns(cache, cancel_raises):
    entered, release, interrupted, drained = Event(), Event(), Event(), Event()
    cancellation = CancellationToken()
    errors, results = [], []

    class BlockingLlm(_Llm):
        def extract(self, batch):
            entered.set()
            assert release.wait(3)
            return super().extract(batch)

        def cancel(self):
            interrupted.set()
            if cancel_raises:
                raise RuntimeError("provider cancel failed")

    def run():
        try:
            results.extend(_runner(BlockingLlm(), cache).run(_inputs(3), cancellation))
        except RuntimeError as exc:
            errors.append(exc)
        finally:
            drained.set()

    worker = Thread(target=run)
    worker.start()
    try:
        assert entered.wait(1)
        cancellation._cancel("test")
        assert interrupted.wait(1)
        assert not drained.wait(0.2)
    finally:
        release.set()
        worker.join(2)
    assert drained.is_set() and not worker.is_alive()
    assert bool(errors) is cancel_raises
    assert results == [] and cache.gc("extraction", max_entries=0) == 0


def test_already_cancelled_run_does_not_call_provider_or_populate_cache(cache):
    cancellation = CancellationToken()
    cancellation._cancel("before admission")
    llm = _Llm()
    assert list(_runner(llm, cache).run(_inputs(), cancellation)) == []
    assert llm.calls == []
    assert cache.gc("extraction", max_entries=0) == 0


def test_service_rejects_a_proposal_pointing_to_a_different_batch():
    class CrossBatchLlm:
        def extract(self, batch):
            if batch[0].evidence_id == "e-0":
                return (LlmTermProposal("e-1", "dragon", "龙"),)
            return ()

    evidence = tuple(_evidence(f"e-{index}", "The dragon", "这条龙", "BOOK:DESC") for index in range(2))
    result = TerminologyExtractionService(llm=CrossBatchLlm(), batch_size=1).extract(evidence, llm_enabled=True)
    assert result.candidates == ()
    assert result.diagnostics == ("LLM_EVIDENCE_UNKNOWN:e-1",)


def test_sequential_service_respects_cancellation_before_provider_call():
    cancellation = CancellationToken()
    cancellation._cancel("before extraction")
    llm = _Llm()
    evidence = (_evidence("e-0", "The dragon", "这条龙", "BOOK:DESC"),)
    result = TerminologyExtractionService(llm=llm).extract(evidence, llm_enabled=True, cancellation=cancellation)
    assert result.cancelled
    assert result.candidates == ()
    assert llm.calls == []


def test_cancellation_inside_a_batch_discards_it_and_prevents_the_next_call():
    cancellation = CancellationToken()

    class CancellingLlm(_Llm):
        def extract(self, batch):
            result = super().extract(batch)
            cancellation._cancel("during extraction")
            return result

    llm = CancellingLlm()
    evidence = tuple(_evidence(f"e-{index}", "The dragon", "这条龙", "BOOK:DESC") for index in range(2))
    result = TerminologyExtractionService(llm=llm, batch_size=1).extract(
        evidence, llm_enabled=True, cancellation=cancellation
    )
    assert result.cancelled
    assert result.candidates == ()
    assert llm.calls == ["e-0"]
