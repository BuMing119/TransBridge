"""Await observable completion of profile reads and derived preview rendering."""

import time

from PyQt6.QtWidgets import QApplication


def wait_until(predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.002)
    assert predicate()
    QApplication.processEvents()


def wait_profile(controller):
    wait_until(lambda: not controller.refresh_pending)


def wait_preview(widget):
    wait_until(lambda: not widget._terminology_preview.pending)
    assert not widget._terminology_preview.error
