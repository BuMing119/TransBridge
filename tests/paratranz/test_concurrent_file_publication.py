"""Real overlapping file requests with synthetic, independently owned sessions."""

from concurrent.futures import ThreadPoolExecutor
import json
from threading import Event, get_ident
from time import monotonic

import pytest
import requests

from tests.paratranz.concurrent_file_support import Backend
from tests.paratranz.test_file_publication import artifact
from transbridge.application.file_upload import execute_file_upload
from transbridge.application.file_upload_parallel import UploadInventory
from transbridge.application.ports.paratranz import ExternalServiceError
from transbridge.paratranz.file_publication import RequestBudget


def upload(backend, files, *, budget=None, cancel=None, mode="both", callbacks=None):
    gateway = backend.gateway(budget=budget, cancel=cancel)
    try:
        owner = get_ident()

        def callback(*_args):
            if callbacks is not None:
                callbacks.append((owner, get_ident()))

        return execute_file_upload(
            7,
            files,
            gateway,
            mode=mode,
            cancelled=cancel.is_set if cancel else lambda: False,
            progress=callback,
            check_current=callback,
        )
    finally:
        gateway.close()


def test_upload_runs_five_independent_pipelines_but_each_create_precedes_translation():
    release = Event()

    def handle(row):
        if row["kind"] == "source":
            assert release.wait(5)

    backend, callbacks = Backend(handler=handle), []
    files = tuple(artifact(path=f"Plugin/file-{index}.json") for index in range(8))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(upload, backend, files, callbacks=callbacks)
        try:
            assert backend.wait_for(lambda calls: sum(row["kind"] == "source" for row in calls) == 5)
            assert backend.peak == 5
            assert not any(row["kind"] == "translation" for row in backend.calls)
        finally:
            release.set()
        receipt = future.result(timeout=5)
    assert receipt.succeeded == 8 and receipt.failed == 0
    assert [row.path for row in receipt.files] == [file.path for file in files]
    sources = {row["created_id"]: row for row in backend.calls if row["kind"] == "source"}
    for row in backend.calls:
        if row["kind"] == "translation":
            file_id = int(row["endpoint"].split("/")[-2])
            assert sources[file_id]["finished"] <= row["started"]
            assert json.loads(row["request"].body)["force"] is False
    assert all(owner == actual for owner, actual in callbacks)
    assert {id(session) for session in backend.sessions} == backend.closed


def test_shared_account_five_slot_limit_across_gateways_and_accounts_are_independent():
    release = Event()
    backend = Backend(handler=lambda row: release.wait(5) and None)
    budget = RequestBudget(0)
    gateways = [backend.gateway(budget=budget, user_id=account) for account in (7,) * 7 + (8,) * 7]
    with ThreadPoolExecutor(max_workers=14) as pool:
        futures = [pool.submit(port.get_file_translation, 7, index + 1) for index, port in enumerate(gateways)]
        try:
            assert backend.wait_for(lambda _calls: backend.active == 10)
            assert sorted(backend.peaks.values()) == [5, 5]
        finally:
            release.set()
        assert all(future.result(timeout=5) for future in futures)
    assert all(peak <= 5 for peak in backend.peaks.values())
    for gateway in gateways:
        gateway.close()
    assert {id(session) for session in backend.sessions} == backend.closed


@pytest.mark.parametrize("cancel_phase", ["source", "translation"])
def test_cancellation_preserves_successful_in_flight_write_facts(cancel_phase):
    release, cancel = Event(), Event()

    def handle(row):
        if row["kind"] == cancel_phase:
            assert release.wait(5)

    backend = Backend(handler=handle)
    files = tuple(artifact(path=f"file-{index}.json") for index in range(5))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(upload, backend, files, budget=RequestBudget(0, max_concurrent=2), cancel=cancel)
        try:
            assert backend.wait_for(lambda calls: sum(row["kind"] == cancel_phase for row in calls) == 2)
            cancel.set()
        finally:
            release.set()
        result = future.result(timeout=5)
    assert sum(row.source_applied for row in result.files) == 2
    if cancel_phase == "source":
        assert not any(row["kind"] == "translation" for row in backend.calls)
        assert not any(row.translation_applied for row in result.files)
    else:
        assert sum(row.translation_applied for row in result.files) == result.succeeded == 2
    assert len(result.retry_files) == (5 if cancel_phase == "source" else 3)
    assert {id(session) for session in backend.sessions} == backend.closed


def test_unknown_post_is_never_replayed_and_other_in_flight_files_keep_receipts():
    def handle(row):
        if row["path"] == "bad.json":
            return requests.Timeout("response lost")

    backend = Backend(handler=handle)
    files = (artifact(path="bad.json"), artifact(path="good.json"))
    result = upload(backend, files, mode="orig_only")
    assert [row.status for row in result.files] == ["uncertain", "succeeded"]
    assert len([row for row in backend.calls if row["path"] == "bad.json"]) == 1
    assert result.retry_files == (files[0],)


def test_403_stops_new_requests_but_keeps_other_already_sent_success():
    both_entered, release = Event(), Event()
    backend = None

    def handle(row):
        if row["kind"] == "source":
            assert both_entered.wait(5)
            if row["path"] == "forbidden.json":
                return (403, {}, {})
            assert release.wait(5)

    backend = Backend(handler=handle)
    files = (artifact(path="forbidden.json"), artifact(path="sent.json"), artifact(path="later.json"))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(upload, backend, files, budget=RequestBudget(0, max_concurrent=2), mode="orig_only")
        try:
            assert backend.wait_for(lambda calls: sum(row["kind"] == "source" for row in calls) == 2)
            both_entered.set()
            assert backend.wait_for(
                lambda calls: any(row["path"] == "forbidden.json" and "finished" in row for row in calls)
            )
        finally:
            release.set()
        result = future.result(timeout=5)
    assert [row.status for row in result.files] == ["failed", "succeeded", "skipped"]
    assert result.files[1].source_applied
    assert not any(row["path"] == "later.json" for row in backend.calls)


def test_split_path_related_files_keep_input_order_and_complete_retry_facts():
    first_large = True

    def handle(row):
        nonlocal first_large
        if row["path"] == "A.json" and first_large:
            first_large = False
            return (413, {}, {})

    backend = Backend(handler=handle)
    files = (artifact(4, path="A.json"), artifact(path="A_1.json"), artifact(path="B.json"))
    result = upload(backend, files, mode="orig_only")
    assert result.succeeded == 4 and not result.retry_files
    writes = [row for row in backend.calls if row["path"] == "A_1.json"]
    assert len(writes) == 2
    assert writes[0]["endpoint"].endswith("/files")
    assert not writes[1]["endpoint"].endswith("/files")
    assert writes[0]["finished"] <= writes[1]["started"]


def test_global_refusal_interrupts_long_429_wait_without_releasing_shared_deadline():
    both_entered, limited = Event(), Event()

    def handle(row):
        if row["kind"] == "source":
            assert both_entered.wait(5)
            if row["path"] == "limited.json":
                limited.set()
                return (429, {}, {"Retry-After": "120"})
            assert limited.wait(5)
            return (403, {}, {})

    backend, budget = Backend(handler=handle), RequestBudget(0, max_concurrent=2)
    files = (artifact(path="limited.json"), artifact(path="forbidden.json"))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(upload, backend, files, budget=budget, mode="orig_only")
        assert backend.wait_for(lambda calls: sum(row["kind"] == "source" for row in calls) == 2)
        started = monotonic()
        both_entered.set()
        result = future.result(timeout=3)
        assert monotonic() - started < 3
    assert len(result.retry_files) == 2
    assert any(row.http_status == 403 for row in result.files)
    assert max(budget._next.values()) - monotonic() > 110


def test_five_overlapping_requests_still_obey_half_second_start_pacing():
    release = Event()

    def handle(_row):
        assert release.wait(6)

    backend, budget = Backend(handler=handle), RequestBudget()
    gateways = [backend.gateway(budget=budget) for _ in range(5)]
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(gateway.get_file_translation, 7, index + 1) for index, gateway in enumerate(gateways)]
        try:
            assert backend.wait_for(lambda calls: len(calls) == 5, timeout=5)
            starts = sorted(row["started"] for row in backend.calls)
            # Admission uses monotonic >=0.5s; send preparation/scheduling adds a
            # little jitter. Exact fake-clock spacing is covered by timing tests.
            assert all(right - left >= 0.45 for left, right in zip(starts, starts[1:]))
            assert backend.peak == 5
        finally:
            release.set()
        assert all(future.result(timeout=3) for future in futures)
    for gateway in gateways:
        gateway.close()


def test_queued_sixth_request_can_cancel_without_entering_http():
    release, cancel = Event(), Event()

    def handle(_row):
        assert release.wait(5)

    backend, budget = Backend(handler=handle), RequestBudget(0)
    active = [backend.gateway(budget=budget) for _ in range(5)]
    queued = backend.gateway(budget=budget, cancel=cancel)
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(gateway.get_file_translation, 7, index + 1) for index, gateway in enumerate(active)]
        try:
            assert backend.wait_for(lambda calls: len(calls) == 5)
            waiting = pool.submit(queued.get_file_translation, 7, 99)
            cancel.set()
            with pytest.raises(ExternalServiceError, match="取消"):
                waiting.result(timeout=3)
            assert len(backend.calls) == 5
        finally:
            release.set()
        assert all(future.result(timeout=3) for future in futures)
    for gateway in (*active, queued):
        gateway.close()


def test_inventory_refresh_preserves_an_independent_creation_confirmed_during_refresh():
    refreshing, release = Event(), Event()
    inventory = UploadInventory({"old.json": 20})

    class Port:
        def inventory(self, _project_id):
            refreshing.set()
            assert release.wait(3)
            return {"old.json": 30}

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(inventory.relocate, Port(), 7, "old.json")
        try:
            assert refreshing.wait(3)
            inventory["created.json"] = 21
        finally:
            release.set()
        future.result(timeout=3)
    assert inventory.get("old.json") == 30
    assert inventory.get("created.json") == 21


def test_owner_check_failure_stops_bounded_workers_without_deadlock_or_http_writes():
    backend = Backend()
    gateway = backend.gateway()
    calls = []

    def check():
        calls.append(get_ident())
        if len(calls) > 1:
            raise ValueError("target switched")

    owner = get_ident()
    try:
        result = execute_file_upload(
            7, tuple(artifact(path=f"file-{index}.json") for index in range(8)), gateway, check_current=check
        )
    finally:
        gateway.close()
    assert all(thread == owner for thread in calls)
    assert all(row.status == "skipped" for row in result.files)
    assert len(result.retry_files) == 8
    assert [row["kind"] for row in backend.calls] == ["inventory"]
