import gc
import threading
import weakref

from PyQt6 import sip
from PyQt6.QtCore import QThreadPool
from PyQt6.QtWidgets import QApplication, QWidget
import pytest

from transbridge.application.terminology import Page
from transbridge.ui.tools.terminology.async_actions import AsyncActionRunner
from transbridge.ui.tools.terminology.paged_models import KeysetPagedTableModel, PagedColumn

_APP = QApplication.instance() or QApplication([])


@pytest.mark.parametrize("close", [False, True])
def test_read_replacement_or_close_stops_work_after_current_stage(close):
    pool, entered, release = QThreadPool(), threading.Event(), threading.Event()
    delivered, visited, errors = [], [], []
    runner = AsyncActionRunner(on_error=errors.append, thread_pool=pool)

    def read(token):
        visited.append(0)
        entered.set()
        assert release.wait(3)
        for index in range(1, 1000):
            token.raise_if_cancelled()
            visited.append(index)
        return "old"

    runner.run_read(read, delivered.append, key="query")
    assert entered.wait(3)
    if close:
        runner.close()
    else:
        runner.run_read(lambda token: "new", delivered.append, key="query")
    release.set()
    assert pool.waitForDone(3000)
    _APP.processEvents()
    assert visited == [0]
    assert delivered == ([] if close else ["new"])
    assert errors == []
    runner.close()


@pytest.mark.parametrize("close", [False, True])
def test_page_loader_receives_token_and_stops_obsolete_scan(close):
    pool, entered, release = QThreadPool(), threading.Event(), threading.Event()
    visited = []

    def read(ref, request, token):
        if ref == "old":
            entered.set()
            assert release.wait(3)
            for index in range(1000):
                token.raise_if_cancelled()
                visited.append(index)
        return Page((ref,), ref, total=1)

    model = KeysetPagedTableModel(
        lambda *_args: pytest.fail("legacy loader used"),
        (PagedColumn("value", "Value", str),),
        read_loader=read,
        thread_pool=pool,
    )
    model.set_query("old")
    assert entered.wait(3)
    if close:
        model.close()
    else:
        model.set_query("new")
    release.set()
    assert pool.waitForDone(3000)
    _APP.processEvents()
    assert visited == []
    assert model.last_error is None
    assert model.rowCount() == (0 if close else 1)
    if not close:
        assert model.index(0, 0).data() == "new"
    model.close()


@pytest.mark.parametrize("disposal", ["delete", "gc"])
def test_destroyed_runner_owner_cancels_active_read_without_touching_deleted_qt(disposal):
    pool, entered, release = QThreadPool(), threading.Event(), threading.Event()
    delivered, visited = [], []
    owner = QWidget()
    owner.cycle = owner
    owner_ref = weakref.ref(owner)
    runner = AsyncActionRunner(owner, on_error=pytest.fail, thread_pool=pool)

    def read(token):
        entered.set()
        assert release.wait(3)
        token.raise_if_cancelled()
        visited.append("should not run")

    token = runner.run_read(read, delivered.append)
    assert entered.wait(3)
    if disposal == "delete":
        sip.delete(owner)
    else:
        del runner, owner
        gc.collect()
        assert owner_ref() is None
    assert token.is_cancelled
    release.set()
    assert pool.waitForDone(3000)
    _APP.processEvents()
    assert delivered == visited == []


def test_destroyed_page_owner_cancels_active_query_and_drops_queued_delivery():
    pool, entered, release = QThreadPool(), threading.Event(), threading.Event()
    tokens = []
    owner = QWidget()

    def read(ref, request, token):
        tokens.append(token)
        entered.set()
        assert release.wait(3)
        token.raise_if_cancelled()
        return Page(("old",), "old", total=1)

    model = KeysetPagedTableModel(
        lambda *_args: None,
        (PagedColumn("value", "Value", str),),
        owner,
        read_loader=read,
        thread_pool=pool,
    )
    model.set_query("old")
    assert entered.wait(3)
    sip.delete(owner)
    assert tokens[0].is_cancelled
    release.set()
    assert pool.waitForDone(3000)
    _APP.processEvents()
