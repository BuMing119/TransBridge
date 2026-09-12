"""Background list reads never display a previous Session or flood the input queue."""

from concurrent.futures import Future
from types import SimpleNamespace

from transbridge.ui.tools.smart_assistant.request_view_refresh import RequestViewRefresh


def _binding():
    jobs, callbacks, shown = [], [], []

    def submit(fn, *args):
        future = Future()
        jobs.append((future, fn, args))
        return future

    binding = SimpleNamespace(
        context="a",
        _closed=False,
        _queue=SimpleNamespace(submit=submit),
        delivered=SimpleNamespace(emit=callbacks.append),
        service=SimpleNamespace(
            state=lambda context: {"requests": (context,)}, requests=lambda state: state["requests"]
        ),
        view=SimpleNamespace(hide=lambda: None, show=lambda: None, display=shown.append, set_pending=lambda *_: None),
    )
    return binding, jobs, callbacks, shown


def test_changed_session_discards_old_read_and_coalesces_refreshes():
    binding, jobs, callbacks, shown = _binding()
    refresh = RequestViewRefresh(binding)
    refresh.request()
    binding.context = "b"
    for _ in range(20):
        refresh.request()
    assert len(jobs) == 1
    future, fn, args = jobs[0]
    future.set_result(fn(*args))
    callbacks.pop(0)()
    assert shown == []
    assert len(jobs) == 2
    future, fn, args = jobs[1]
    future.set_result(fn(*args))
    callbacks.pop(0)()
    assert shown == [("b",)]


def test_closed_view_discards_background_completion():
    binding, jobs, callbacks, shown = _binding()
    refresh = RequestViewRefresh(binding)
    refresh.request()
    binding._closed = True
    future, fn, args = jobs[0]
    future.set_result(fn(*args))
    callbacks.pop(0)()
    assert shown == []
    assert len(jobs) == 1
