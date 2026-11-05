from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox, QPushButton, QWidget
import pytest

from transbridge.application.terminology_profiles import TerminologyProfileImportPolicy
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


@pytest.mark.parametrize("action", ["cancel", "context_changed", "disposed"])
def test_import_preview_abort_never_creates_a_profile(monkeypatch, action):
    class _Profiles:
        identity = ("project", "variant")

        def preview_source_import(self, _source, *, policy):
            return object()

        def create_from_source_import(self, *_args, **_kwargs):
            raise AssertionError("aborted import must not create a profile")

    profiles = _Profiles()
    parent = QWidget()
    controller = TerminologySourceImportController(parent, QPushButton(parent), profiles, lambda: _Reader())
    controller._expected_identity = profiles.identity
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: warnings.append(_args[2]))

    class _Dialog:
        def __init__(self, *_args, **_kwargs):
            pass

        def exec(self):
            if action == "cancel":
                return QDialog.DialogCode.Rejected
            if action == "context_changed":
                profiles.identity = ("project", "other-variant")
            else:
                controller.dispose()
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr("transbridge.ui.tools.terminology_profiles.TerminologySourceImportDialog", _Dialog)
    controller._source_ready("snapshot")
    assert bool(warnings) == (action == "context_changed")
    parent.close()
    _APP.processEvents()


@pytest.mark.parametrize("policy", tuple(TerminologyProfileImportPolicy))
def test_selected_batch_policy_is_frozen_before_read_and_used_for_preview_and_creation(monkeypatch, policy):
    preview = SimpleNamespace(
        source_only_term_count=1, adopted_mapping_count=1, kept_mapping_count=1, overwritten_mapping_count=0
    )
    calls = []

    class _Profiles:
        identity = ("project", "variant")

        def preview_source_import(self, source, *, policy):
            calls.append(("preview", source, policy))
            return preview

        def create_from_source_import(self, name, chosen_preview, *, select):
            calls.append(("create", name, chosen_preview, select))
            return SimpleNamespace(profile=SimpleNamespace(name=name), selection=None)

    class _Dialog:
        def __init__(self, chosen_preview, _default_name, _parent, *, select_after_create):
            assert chosen_preview is preview
            self.profile_name = "导入方案"
            self.select_after_create = select_after_create

        def exec(self):
            return QDialog.DialogCode.Accepted

    tasks = []
    monkeypatch.setattr(
        "transbridge.ui.tools.terminology_profiles.source_import_controller.QThreadPool.globalInstance",
        lambda: SimpleNamespace(start=tasks.append),
    )
    monkeypatch.setattr("transbridge.ui.tools.terminology_profiles.TerminologySourceImportDialog", _Dialog)
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: messages.append(_args[2]))
    parent = QWidget()
    controller = TerminologySourceImportController(parent, QPushButton(parent), _Profiles(), lambda: _Reader("source"))
    busy = []
    controller.busy_changed.connect(busy.append)
    kwargs = {"policy": policy} if policy is TerminologyProfileImportPolicy.USE_IMPORTED else {}
    controller.start_with_reader("request", default_name="预览方案", **kwargs)
    assert controller._policy is policy
    assert len(tasks) == 1 and not calls
    tasks[0].run()
    assert calls == [("preview", "source", policy), ("create", "导入方案", preview, False)]
    assert len(messages) == 1 and "新增 1 条术语" in messages[0]
    assert busy == [True, False] and not controller.busy
    parent.close()
