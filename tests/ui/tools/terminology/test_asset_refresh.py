from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication, QWidget
import pytest

from transbridge.application.contracts import RequestContext
from transbridge.application.terminology.query_control import QueryCancellation, QueryCancelled
from transbridge.ui.tools.terminology.asset_refresh import AssetRefreshCoordinator
from transbridge.ui.tools.terminology.asset_snapshot import read_assets

_APP = QApplication.instance() or QApplication([])
_CONTEXT = RequestContext("owner", project_id="project", variant_id="variant")


class _Pool:
    def __init__(self):
        self.calls = []

    def start(self, call):
        self.calls.append(call)


def test_history_completions_share_one_actual_asset_read_and_keep_each_workload():
    owner, pool, delivered, reads, errors = QWidget(), _Pool(), [], [], []
    commands = SimpleNamespace(
        latest_build_ref=lambda *_args: reads.append("assets"),
        task_result=lambda run, _context: reads.append(run) or run,
    )
    coordinator = AssetRefreshCoordinator(
        owner, commands, _CONTEXT, on_ready=delivered.append, on_error=errors.append, thread_pool=pool
    )
    workloads = ("build", "publish", "compare", "report", "changelog")
    for index in range(100):
        coordinator.request(workloads[index % 5], f"run-{index}")
    _APP.processEvents()
    assert len(pool.calls) == 1
    pool.calls[0].run()
    _APP.processEvents()
    assert reads == ["assets", *(f"run-{index}" for index in range(95, 100))]
    assert dict(delivered[0].completions) == dict(zip(workloads, (f"run-{i}" for i in range(95, 100)), strict=True))
    coordinator.request("build", "run-95")
    _APP.processEvents()
    assert len(pool.calls) == 1
    assert errors == []
    coordinator.close()
    owner.close()


def test_superseded_read_stops_between_stages_and_retains_earlier_completion():
    owner, pool, delivered, reads = QWidget(), _Pool(), [], []

    def first_stage(*_args):
        reads.append("first")
        if len(reads) == 1:
            coordinator.request("report", "report-run")

    commands = SimpleNamespace(
        latest_build_ref=first_stage,
        latest_build_result=lambda *_args: reads.append("second"),
        task_result=lambda run, _context: run,
    )
    coordinator = AssetRefreshCoordinator(
        owner, commands, _CONTEXT, on_ready=delivered.append, on_error=pytest.fail, thread_pool=pool
    )
    coordinator.request("build", "build-run")
    _APP.processEvents()
    pool.calls[0].run()
    assert reads == ["first"]
    _APP.processEvents()
    _APP.processEvents()
    assert len(pool.calls) == 2
    pool.calls[1].run()
    _APP.processEvents()
    assert reads == ["first", "first", "second"]
    assert len(delivered) == 1
    assert dict(delivered[0].completions) == {"build": "build-run", "report": "report-run"}
    coordinator.close()
    owner.close()


def test_asset_read_cancellation_stops_before_the_next_port_and_passes_token_to_browse():
    token, reads = QueryCancellation(), []
    commands = SimpleNamespace(
        latest_build_ref=lambda *_args: token.cancel(),
        latest_build_result=lambda *_args: reads.append("must not run"),
    )
    with pytest.raises(QueryCancelled):
        read_assets(commands, _CONTEXT, cancellation=token)
    assert not reads
    token = QueryCancellation()
    browsing = SimpleNamespace(describe=lambda query, *, cancellation: reads.append(cancellation))
    read_assets(SimpleNamespace(), _CONTEXT, browsing=browsing, cancellation=token)
    assert reads == [token]


def test_closed_coordinator_rejects_queued_results_and_reports_original_failure():
    owner, pool, delivered, errors = QWidget(), _Pool(), [], []
    cause = ValueError("snapshot cannot be read")

    def fail(*_args):
        raise cause

    coordinator = AssetRefreshCoordinator(
        owner,
        SimpleNamespace(latest_build_ref=fail),
        _CONTEXT,
        on_ready=delivered.append,
        on_error=errors.append,
        thread_pool=pool,
    )
    coordinator.request()
    _APP.processEvents()
    pool.calls[0].run()
    _APP.processEvents()
    assert errors == [cause]
    coordinator.request()
    _APP.processEvents()
    pool.calls[1].run()
    coordinator.close()
    _APP.processEvents()
    assert errors == [cause] and delivered == []
    owner.close()
