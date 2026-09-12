from __future__ import annotations

from dataclasses import replace
import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QObject, QThread, pyqtSignal
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget
import pytest

from transbridge.ai_translator.project_terminology_runtime import freeze_project_terminology
from transbridge.application.terminology.effective import EffectiveSnapshotStatus, EffectiveTerminologySnapshot
from transbridge.application.terminology.models import DecisionStatus, TermDecision
from transbridge.ui.tools.ai_translator.project_terminology_controller import ProjectTerminologyController
from transbridge.ui.tools.ai_translator.project_terminology_view import ProjectTerminologyPanel


class _Context(QObject):
    project_changed = pyqtSignal()
    variant_changed = pyqtSignal(str)
    state_changed = pyqtSignal(object)

    def __init__(self, factory):
        super().__init__()
        self.active_version_identity = ("project-a", "variant-a")
        self.project_name = "测试工程"
        self.active_variant = "中文"
        self.effective_terminology_factory = factory


class _Factory:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.threads = []

    def effective_adapter(self, project_id, variant_id):
        factory = self

        class Adapter:
            def effective_snapshot(self, context):
                assert (context.local_project_id, context.local_variant_id) == (project_id, variant_id)
                factory.threads.append(QThread.currentThread())
                if isinstance(factory.snapshot, Exception):
                    raise factory.snapshot
                return factory.snapshot

        return Adapter()


def _snapshot(*, version="published-1", identity=("project-a", "variant-a"), count=2):
    decisions = tuple(
        TermDecision(
            f"term-{index}", *identity, f"Sword {index}", f"sword {index}", f"剑 {index}", status=DecisionStatus.ADOPTED
        )
        for index in range(count)
    )
    return EffectiveTerminologySnapshot(
        *identity, EffectiveSnapshotStatus.READY, version, "digest", decisions=decisions
    )


def _wait(predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(10)
    assert predicate()


@pytest.fixture
def ui():
    app = QApplication.instance() or QApplication([])
    windows = []

    def create(context, *, can_open=True):
        window = QWidget()
        panel = ProjectTerminologyPanel(window)
        QVBoxLayout(window).addWidget(panel)
        controller = ProjectTerminologyController(window, context, panel, can_open=can_open, profile_controller=context)
        windows.append((window, controller))
        return window, panel, controller

    yield create
    for window, controller in windows:
        if sip.isdeleted(window):
            continue
        controller.close()
        window.close()
        window.deleteLater()
    app.processEvents()


def test_published_summary_uses_run_snapshot_counts_and_does_not_read_on_ui_thread(ui):
    snapshot = _snapshot()
    suppressed = replace(snapshot.decisions[0], term_id="suppressed", suppressed=True)
    pending = replace(snapshot.decisions[0], term_id="pending", status=DecisionStatus.REVIEW_REQUIRED)
    factory = _Factory(replace(snapshot, decisions=(*snapshot.decisions, suppressed, pending)))
    context = _Context(factory)
    _window, panel, _controller = ui(context)
    _wait(lambda: "有效术语 2 条" in panel.status_label.text())

    assert all(thread != QApplication.instance().thread() for thread in factory.threads)
    expected = freeze_project_terminology(context).snapshot_ref
    assert panel.version_label.full_text == f"术语版本：{expected.version_id}"
    assert panel.context_label.full_text == "测试工程 · 中文"
    assert panel.open_button.isEnabled()


@pytest.mark.parametrize(
    ("snapshot", "expected"),
    [
        (_snapshot(count=0), "有效术语 0 条"),
        (
            EffectiveTerminologySnapshot("project-a", "variant-a", EffectiveSnapshotStatus.NO_PROJECT_VERSION),
            "尚未发布",
        ),
        (
            EffectiveTerminologySnapshot(
                "project-a", "variant-a", EffectiveSnapshotStatus.CORRUPT, diagnostics=("内容校验失败",)
            ),
            "读取失败",
        ),
        (
            EffectiveTerminologySnapshot(
                "project-a", "variant-a", EffectiveSnapshotStatus.UNAVAILABLE, diagnostics=("术语存储不可用",)
            ),
            "读取失败",
        ),
        (OSError("存储无法读取"), "读取失败"),
        (_snapshot(identity=("wrong-project", "variant-a")), "读取失败"),
    ],
)
def test_empty_unpublished_and_failed_states_are_distinct(ui, snapshot, expected):
    _window, panel, _controller = ui(_Context(_Factory(snapshot)))
    _wait(lambda: expected in panel.status_label.text())
    if expected != "有效术语 0 条":
        assert panel.version_label.full_text == ""
    assert panel.open_button.isEnabled()


@pytest.mark.parametrize("no_project", [True, False])
def test_missing_project_or_service_does_not_claim_terms_are_loaded(ui, no_project):
    context = _Context(None)
    if no_project:
        context.active_version_identity = None
    _window, panel, _controller = ui(context)
    assert ("未关联" if no_project else "未接入") in panel.status_label.text()
    assert panel.open_button.isEnabled() is not no_project


@pytest.mark.parametrize("trigger", ["manual", "activate", "project", "variant", "profile"])
def test_changes_and_returning_from_workbench_refresh_published_version(ui, trigger):
    factory = _Factory(_snapshot())
    context = _Context(factory)
    window, panel, _controller = ui(context, can_open=False)
    _wait(lambda: "published-1" in panel.version_label.full_text)
    factory.snapshot = _snapshot(version="published-2", count=3)
    if trigger == "manual":
        panel.refresh_button.click()
    elif trigger == "activate":
        QApplication.sendEvent(window, QEvent(QEvent.Type.WindowActivate))
    elif trigger == "project":
        context.project_changed.emit()
    elif trigger == "variant":
        context.variant_changed.emit("中文")
    else:
        context.state_changed.emit(object())
    _wait(lambda: "published-2" in panel.version_label.full_text)
    assert "有效术语 3 条" in panel.status_label.text()
    assert not panel.open_button.isEnabled()


@pytest.mark.parametrize("close_during_read", [False, True])
def test_late_read_cannot_overwrite_changed_project_or_closed_window(ui, close_during_read):
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    original = _snapshot()

    class Factory(_Factory):
        def effective_adapter(self, project_id, variant_id):
            if project_id != "project-a":
                return super().effective_adapter(project_id, variant_id)

            class Adapter:
                def effective_snapshot(self, context):
                    started.set()
                    assert release.wait(5)
                    finished.set()
                    return original

            return Adapter()

    context = _Context(Factory(_snapshot(version="new-project-version", identity=("project-b", "variant-b"), count=3)))
    window, panel, controller = ui(context)
    try:
        _wait(started.is_set)
        if close_during_read:
            controller.close()
            window.deleteLater()
            QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        else:
            context.active_version_identity = ("project-b", "variant-b")
            context.project_name = "另一个工程"
            context.project_changed.emit()
            assert panel.version_label.full_text == ""
        release.set()
        _wait(finished.is_set)
        if not close_during_read:
            _wait(lambda: "new-project-version" in panel.version_label.full_text)
            assert "有效术语 3 条" in panel.status_label.text()
            assert "另一个工程" in panel.context_label.full_text
    finally:
        release.set()
