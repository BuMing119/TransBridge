"""Parallel HTTP reads retain complete, ordered and isolated source snapshots."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event, get_ident
from time import monotonic

import pytest

from tests.paratranz.concurrent_file_support import Backend
from tests.paratranz.test_batch_download_snapshot import scope
from transbridge.application.ports.paratranz import ExternalServiceError
from transbridge.paratranz.batch_download_snapshot import BatchDownloadSnapshotReader
from transbridge.paratranz.file_publication import RequestBudget
from transbridge.paratranz.file_transport import UploadCancellation
from transbridge.paratranz.parallel_file_reads import ParallelFileReads


def fetch(backend, scopes, *, cancel=None, progress=None, on_source=None, limit=100_000, budget=None):
    return BatchDownloadSnapshotReader(object(), gateway_factory=lambda _: backend.gateway(budget=budget)).fetch(
        7,
        scopes,
        cancellation=UploadCancellation(cancel or Event()),
        progress=progress,
        on_source=on_source,
        limit=limit,
    )


@pytest.mark.parametrize("single_source", [False, True])
def test_five_downloads_overlap_and_callbacks_and_results_keep_owner_order(single_source):
    release = Event()

    def handle(row):
        if row["kind"] == "download":
            assert release.wait(5)

    inventory = (
        {f"a.esp/file-{index}.json": index + 1 for index in range(8)}
        if single_source
        else {f"source-{index}.esp.json": index + 1 for index in range(8)}
    )
    scopes = (scope("a"),) if single_source else tuple(scope(f"source-{index}") for index in range(8))
    backend, callbacks = Backend(inventory, handler=handle), []

    def run():
        owner = get_ident()
        return fetch(
            backend,
            scopes,
            progress=lambda *_: callbacks.append((owner, get_ident())),
            on_source=lambda *_: callbacks.append((owner, get_ident())),
        )

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run)
        try:
            assert backend.wait_for(lambda calls: sum(row["kind"] == "download" for row in calls) == 5)
            assert backend.peak == 5
        finally:
            release.set()
        result = future.result(timeout=5)
    assert [row.source_id for row in result] == [item.source_id for item in scopes]
    assert sum(len(row.entries) for row in result) == 8
    assert all(not row.error for row in result)
    assert len([row for row in backend.calls if row["kind"] == "download"]) == 8
    assert all(owner == actual for owner, actual in callbacks)
    assert {id(session) for session in backend.sessions} == backend.closed


def test_failed_source_never_publishes_a_partial_prefix_and_next_source_still_succeeds():
    def handle(row):
        if row["kind"] == "download" and row["endpoint"].endswith("/2/translation"):
            return (500, {}, {})

    backend = Backend({"a.esp/one.json": 1, "a.esp/two.json": 2, "b.esp.json": 3}, handler=handle)
    observed = []
    result = fetch(backend, (scope("a"), scope("b")), on_source=lambda _scope, row: observed.append(row))
    assert result[0].error and not result[0].entries
    assert not result[1].error and len(result[1].entries) == 1
    assert all(row.error for row in observed if row.source_id == "a")
    assert {id(session) for session in backend.sessions} == backend.closed


def test_404_drains_old_generation_and_rebuilds_layout_reusing_consumed_file_ids():
    backend = None

    def handle(row):
        if row["kind"] == "download" and row["endpoint"].endswith("/2/translation"):
            with backend.condition:
                backend.inventory = {"a.esp_1.json": 1, "a.esp_3.json": 4, "b.esp.json": 3}
            return (404, {}, {})

    backend = Backend({"a.esp_1.json": 1, "a.esp_2.json": 2, "b.esp.json": 3}, handler=handle)
    result = fetch(backend, (scope("a"), scope("b")))
    assert result[0].paths == ("a.esp_1.json", "a.esp_3.json")
    assert [row.entry_key.local_key for row in result[0].entries] == ["1", "4"]
    assert not any(row.error for row in result)
    inventories = [row for row in backend.calls if row["kind"] == "inventory"]
    assert len(inventories) == 2
    first_file = [
        row for row in backend.calls if row["kind"] == "download" and row["endpoint"].endswith("/1/translation")
    ]
    assert len(first_file) == 1
    assert all(
        row["finished"] <= inventories[1]["started"] for row in backend.calls[: backend.calls.index(inventories[1])]
    )
    assert {id(session) for session in backend.sessions} == backend.closed


def test_global_permission_failure_stops_later_prefetch_and_returns_original_error():
    release, entered, forbidden = Event(), Event(), Event()

    def handle(row):
        if row["kind"] == "download":
            assert entered.wait(5)
            if row["endpoint"].endswith("/2/translation"):
                forbidden.set()
                return (403, {}, {})
            assert release.wait(5)

    backend = Backend({f"source-{index}.esp.json": index + 1 for index in range(8)}, handler=handle)
    scopes = tuple(scope(f"source-{index}") for index in range(8))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(fetch, backend, scopes)
        try:
            assert backend.wait_for(lambda calls: sum(row["kind"] == "download" for row in calls) == 5)
            entered.set()
            assert forbidden.wait(3)
        finally:
            release.set()
        with pytest.raises(ExternalServiceError) as caught:
            future.result(timeout=5)
    assert caught.value.status == 403
    assert len([row for row in backend.calls if row["kind"] == "download"]) == 5
    assert {id(session) for session in backend.sessions} == backend.closed


def test_cancelled_download_closes_in_flight_sessions_without_publishing_prefix():
    release, cancel = Event(), Event()

    def handle(row):
        if row["kind"] == "download":
            assert release.wait(5)

    backend = Backend({f"source-{index}.esp.json": index + 1 for index in range(8)}, handler=handle)
    observed = []
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            fetch,
            backend,
            tuple(scope(f"source-{index}") for index in range(8)),
            cancel=cancel,
            on_source=lambda *_: observed.append(True),
        )
        try:
            assert backend.wait_for(lambda calls: sum(row["kind"] == "download" for row in calls) == 5)
            cancel.set()
        finally:
            release.set()
        with pytest.raises(ExternalServiceError, match="取消"):
            future.result(timeout=5)
    assert not observed
    assert len([row for row in backend.calls if row["kind"] == "download"]) == 5
    assert {id(session) for session in backend.sessions} == backend.closed


def test_parallel_limit_selection_stays_deterministic_and_duplicate_remote_ids_are_excluded():
    backend = Backend({f"source-{index}.esp.json": index + 1 for index in range(5)})
    for file_id in range(1, 6):
        backend.rows[file_id] = [
            {
                "key": str(offset),
                "original": str(offset),
                "translation": "译文",
                "stage": 1,
                "id": file_id * 10 + offset,
            }
            for offset in range(2)
        ]
    scopes = tuple(scope(f"source-{index}") for index in range(5))
    result = fetch(backend, scopes, limit=3)
    assert [len(row.entries) for row in result] == [2, 0, 0, 0, 0]
    assert all(row.error for row in result[1:])
    backend = Backend({"a.esp.json": 1, "b.esp.json": 2})
    backend.rows = {
        file_id: [{"key": "same", "original": "same", "translation": "译文", "stage": 1, "id": 99}]
        for file_id in (1, 2)
    }
    result = fetch(backend, (scope("a"), scope("b")))
    assert all(row.error and not row.entries for row in result)


def test_closing_prefetch_interrupts_long_429_wait_but_keeps_account_cooldown():
    limited = Event()

    def handle(row):
        if row["kind"] == "download":
            limited.set()
            return (429, {}, {"Retry-After": "120"})

    backend, budget = Backend(handler=handle), RequestBudget(0)
    gateway = backend.gateway(budget=budget)
    reads = ParallelFileReads(gateway, 7, {"a.esp.json": 1}, lambda _inventory: (("a", 1),), limit=100)
    assert limited.wait(3)
    started = monotonic()
    reads.close()
    gateway.close()
    assert monotonic() - started < 3
    assert max(budget._next.values()) - monotonic() > 110
    assert {id(session) for session in backend.sessions} == backend.closed


def test_constructor_cancellation_after_first_submission_closes_executor(monkeypatch):
    from transbridge.paratranz import parallel_file_reads

    cancel = Event()
    submit = parallel_file_reads.ThreadPoolExecutor.submit

    def submit_and_cancel(*args, **kwargs):
        future = submit(*args, **kwargs)
        cancel.set()
        return future

    monkeypatch.setattr(parallel_file_reads.ThreadPoolExecutor, "submit", submit_and_cancel)
    backend = Backend({"a.esp/one.json": 1, "a.esp/two.json": 2})
    with pytest.raises(ExternalServiceError, match="取消"):
        fetch(backend, (scope("a"),), cancel=cancel)
    assert {id(session) for session in backend.sessions} == backend.closed
