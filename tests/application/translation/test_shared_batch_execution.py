from concurrent.futures import CancelledError
from threading import Event, Thread

import pytest

from transbridge.ai_translator.batch_execution import BatchExecutor as TranslationExecutor
from transbridge.application.translation.batch_execution import BatchExecutor


def test_translation_and_application_use_the_same_executor():
    assert TranslationExecutor is BatchExecutor


def test_shutdown_cancels_queue_but_retains_running_batch_until_finished():
    entered, release, drained = Event(), Event(), Event()
    executor = BatchExecutor(1)

    def blocked():
        entered.set()
        assert release.wait(3)
        return "finished"

    running = executor.submit(blocked)
    assert entered.wait(1)
    queued = executor.submit(lambda: pytest.fail("queued work must not start after shutdown"))
    executor.shutdown(wait=False)

    def shutdown():
        executor.shutdown()
        drained.set()

    thread = Thread(target=shutdown)
    thread.start()
    try:
        with pytest.raises(CancelledError):
            queued.result()
        with pytest.raises(RuntimeError):
            executor.submit(lambda: None)
        assert not drained.wait(0.1)
    finally:
        release.set()
        thread.join(2)
        executor.shutdown()
    assert drained.is_set() and running.result() == "finished"
    assert executor.snapshot().pending_runs == 0


def test_independent_failure_does_not_fail_another_submission():
    def fail():
        raise ValueError("batch failed")

    with BatchExecutor(2) as executor:
        failed = executor.submit(fail)
        succeeded = executor.submit(lambda: 42)
        with pytest.raises(ValueError, match="batch failed"):
            failed.result(1)
        assert succeeded.result(1) == 42
