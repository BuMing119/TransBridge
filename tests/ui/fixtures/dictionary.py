"""Wait for dictionary workers while delivering their queued GUI signals."""

from time import monotonic

from PyQt6.QtTest import QTest


def wait_for_dictionary(panel):
    deadline = monotonic() + 5
    while (panel._worker is not None or panel._filter.is_pending) and monotonic() < deadline:
        QTest.qWait(5)
    assert panel._worker is None
    assert not panel._filter.is_pending
