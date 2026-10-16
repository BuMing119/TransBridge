"""Wait for debounced GUI search without relying on a particular machine speed."""

import time

from PyQt6.QtWidgets import QApplication


def wait_search(search):
    deadline = time.monotonic() + 3
    while search.is_pending and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.002)
    assert not search.is_pending
    assert search.last_error is None
