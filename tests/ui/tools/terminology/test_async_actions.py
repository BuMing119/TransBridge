from PyQt6.QtWidgets import QApplication

from transbridge.ui.tools.terminology.async_actions import AsyncActionRunner

_APP = QApplication.instance() or QApplication([])


class _Pool:
    def __init__(self):
        self.calls = []

    def start(self, call):
        self.calls.append(call)


def test_replaced_reads_and_closed_windows_drop_late_delivery():
    pool, results, errors = _Pool(), [], []
    runner = AsyncActionRunner(on_error=errors.append, thread_pool=pool)
    runner.run(lambda: "old", results.append, key="assets")
    runner.run(lambda: "new", results.append, key="assets")
    for call in pool.calls:
        call.run()
    _APP.processEvents()
    assert results == ["new"]
    runner.run(lambda: "closed", results.append)
    pool.calls[-1].run()
    runner.close()
    _APP.processEvents()
    assert results == ["new"]
    assert errors == []


def test_call_failure_preserves_original_exception_and_skips_success():
    pool, results, errors = _Pool(), [], []
    cause = ValueError("draft revision changed")

    def fail():
        raise cause

    runner = AsyncActionRunner(on_error=errors.append, thread_pool=pool)
    runner.run(fail, results.append)
    pool.calls[0].run()
    _APP.processEvents()
    assert errors == [cause]
    assert results == []
    runner.close()
