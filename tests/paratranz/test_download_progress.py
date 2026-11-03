"""Progress reflects files, including out-of-order downloads and inventory changes."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event, get_ident

from tests.paratranz.concurrent_file_support import Backend
from tests.paratranz.test_batch_download_snapshot import Gateway, reader, row, scope
from tests.paratranz.test_parallel_file_downloads import fetch
from transbridge.application.ports.paratranz import ExternalServiceCategory, ExternalServiceError


def test_each_file_advances_before_the_plugin_finishes_and_excludes_unselected_files():
    updates = []

    def second():
        assert updates[-1][:2] == (1, 3)
        return [row()]

    gateway = Gateway(
        {"a.esp/one.json": 1, "a.esp/two.json": 2, "b.esp.json": 3, "other.esp.json": 4},
        {1: [row()], 2: second, 3: []},
    )
    reader(gateway).fetch(7, (scope("a"), scope("b")), progress=lambda *args: updates.append(args))
    assert [item[:2] for item in updates] == [(0, 3), (1, 3), (2, 3), (3, 3), (3, 3)]
    assert updates[1][2].startswith("33%（1/3）；")
    assert "a.esp/one.json" in updates[1][2]


def test_failed_file_does_not_count_as_downloaded_or_force_completion():
    updates = []
    gateway = Gateway({"a.esp.json": 1, "b.esp.json": 2}, {1: ValueError("failed"), 2: []})
    result = reader(gateway).fetch(7, (scope("a"), scope("b")), progress=lambda *args: updates.append(args))
    assert result[0].error
    assert updates[-1][:2] == (1, 2)


def test_inventory_refresh_recounts_current_files_without_counting_cached_files_twice():
    updates = []
    gateway = Gateway(
        [{"a.esp_1.json": 1, "a.esp_2.json": 2}, {"a.esp_1.json": 1, "a.esp_3.json": 3, "a.esp_4.json": 4}],
        {1: [], 2: ExternalServiceError(ExternalServiceCategory.NOT_FOUND, "gone", status=404), 3: [], 4: []},
    )
    reader(gateway).fetch(7, (scope("a"),), progress=lambda *args: updates.append(args))
    assert [item[:2] for item in updates] == [(0, 2), (1, 2), (1, 3), (2, 3), (3, 3), (3, 3)]


def test_empty_selection_reports_zero_files():
    updates = []
    reader(Gateway({}, {})).fetch(7, (scope("a"),), progress=lambda *args: updates.append(args))
    assert all(item[:2] == (0, 0) for item in updates)
    assert all(item[2].startswith("0%（0/0）；") for item in updates)


def test_parallel_completion_updates_while_first_file_is_blocked_on_owner_thread():
    release, reported = Event(), Event()
    updates, owners = [], []

    def handle(request):
        if request["kind"] == "download" and request["endpoint"].endswith("/1/translation"):
            assert release.wait(5)

    def progress(current, total, message):
        updates.append((current, total, message))
        owners.append(get_ident())
        if current == 2:
            reported.set()

    backend = Backend({"a.esp/one.json": 1, "a.esp/two.json": 2, "a.esp/three.json": 3}, handler=handle)

    def run():
        return get_ident(), fetch(backend, (scope("a"),), progress=progress)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run)
        try:
            assert reported.wait(3)
            assert not future.done()
            assert updates[-1][:2] == (2, 3)
        finally:
            release.set()
        owner, result = future.result(timeout=5)
    assert not result[0].error
    assert updates[-1][:2] == (3, 3)
    assert set(owners) == {owner}
