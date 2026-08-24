from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QWidget

from transbridge.ui.tools.smart_assistant.chat_widget import ChatWidget

_APP = QApplication.instance() or QApplication([])


def test_shutdown_is_idempotent_before_deferred_ui_initialization() -> None:
    widget = ChatWidget.__new__(ChatWidget)
    QWidget.__init__(widget)

    widget.shutdown(wait_for_worker=False)
    widget.shutdown(wait_for_worker=False)
    _APP.processEvents()

    assert widget._shutdown_complete is True
    assert not hasattr(widget, "_main_layout")
    widget.close()
