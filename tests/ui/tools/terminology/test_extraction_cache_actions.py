from threading import Event
import time

from PyQt6.QtCore import QThreadPool
from PyQt6.QtWidgets import QApplication

from transbridge.application.contracts import RequestContext
from transbridge.ui.tools.terminology.presenter import TerminologyPresenter, TerminologyUiServices
from transbridge.ui.tools.terminology.window import TerminologyWindow

_APP = QApplication.instance() or QApplication([])


def wait(predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.002)
    assert predicate()


def make_window(commands):
    return TerminologyWindow(
        TerminologyPresenter(
            TerminologyUiServices(commands=commands),
            RequestContext("owner", project_id="project", variant_id="variant"),
        )
    )


def test_cleanup_is_background_disables_repeat_and_reports_count():
    started, release = Event(), Event()
    contexts = []

    class Commands:
        def clear_extraction_cache(self, context):
            contexts.append(context)
            started.set()
            assert release.wait(3)
            return 4

    window = make_window(Commands())
    actions = window._extraction_cache
    try:
        actions.button.click()
        assert started.wait(1)
        assert not actions.button.isEnabled()
        _APP.processEvents()
        assert "正在清理" in actions.status.text()
        actions.clear()
        assert len(contexts) == 1
        release.set()
        wait(lambda: actions.button.isEnabled())
        assert "已清理 4 条" in actions.status.text()
        assert contexts[0].project_id == "project"
    finally:
        release.set()
        window.close()
        QThreadPool.globalInstance().waitForDone(3000)


def test_cleanup_busy_guard_failure_retry_and_empty_result():
    class Commands:
        count = 0

        def clear_extraction_cache(self, context):
            self.count += 1
            if self.count == 1:
                raise OSError("database is locked")
            return 0

    commands = Commands()
    window = make_window(commands)
    actions = window._extraction_cache
    try:
        window._workflow.task_busy = True
        actions.button.click()
        assert commands.count == 0
        assert "等待" in actions.status.text()
        window._workflow.task_busy = False
        actions.button.click()
        wait(lambda: "清理失败" in actions.status.text())
        assert "database is locked" in actions.status.text()
        actions.button.click()
        wait(lambda: "没有可清理" in actions.status.text())
    finally:
        window.close()
        QThreadPool.globalInstance().waitForDone(3000)
