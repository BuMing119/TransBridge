"""Wait for asynchronous read projections while continuing GUI event delivery."""

from time import monotonic

from PyQt6.QtTest import QTest


def wait_for(predicate, timeout=3):
    deadline = monotonic() + timeout
    while not predicate() and monotonic() < deadline:
        QTest.qWait(5)
    assert predicate()
