from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QMessageBox, QPushButton, QWidget

from transbridge.ui.tools.ai_translator.terminology_source_import_controller import (
    TerminologySourceImportController,
    _ReadTask,
)

_APP = QApplication.instance() or QApplication([])


class _Reader:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error

    def read(self, _request):
        if self.error is not None:
            raise self.error
        return self.result


def test_read_task_returns_success_and_actionable_failure_through_signals() -> None:
    succeeded = []
    task = _ReadTask(lambda: _Reader(result="snapshot"), object())
    task.signals.succeeded.connect(succeeded.append)
    task.run()
    assert succeeded == ["snapshot"]

    failed = []
    task = _ReadTask(lambda: _Reader(error=ValueError("bad source")), object())
    task.signals.failed.connect(failed.append)
    task.run()
    assert failed == ["bad source"]


def test_completed_read_is_rejected_after_project_identity_changes(monkeypatch) -> None:
    class _Profiles:
        identity = ("project-a", "variant-b")

        def preview_source_import(self, _source):
            raise AssertionError("stale source must not be previewed")

    parent = QWidget()
    button = QPushButton(parent)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    controller = TerminologySourceImportController(parent, button, _Profiles(), lambda: _Reader())
    controller._expected_identity = ("project-a", "variant-a")
    controller._active_task = object()

    controller._source_ready("snapshot")

    assert warnings == ["读取期间工程或翻译版本发生了变化，请重新操作。"]
    assert button.isEnabled()
    parent.close()
    _APP.processEvents()
