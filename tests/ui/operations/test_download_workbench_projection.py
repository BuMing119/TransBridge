"""A real download must update the loaded AppContext, table and save authority."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import replace
import json
from pathlib import Path
from threading import Event
from time import monotonic
from types import SimpleNamespace
from urllib.parse import urlparse

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtTest import QTest
import pytest
import requests

from tests.application.projects.test_categorized_upload import project as project  # noqa: F401
from tests.ui.operations.test_batch_download_dialog import _APP
from tests.ui.operations.test_production_facade import _runtime
from transbridge.application.contracts import DomainError
from transbridge.application.io.paratranz_mapping import paratranz_record_from_entry
from transbridge.application.projects import ParaTranzProjectBinding
from transbridge.config.paratranz import ParatranzConfig
from transbridge.config.paratranz_credentials import UnavailableCredentialStore
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.paratranz import file_publication
from transbridge.paratranz.file_publication import RequestBudget
from transbridge.ui import project_view_binding
from transbridge.ui.context import AppContext
from transbridge.ui.main_window import MainWindow
from transbridge.ui.operations.production import build_operation_plan_facade
from transbridge.ui.source_hydration import prepare_project_slots
from transbridge.ui.workbench.step2 import Step2PreviewWidget
from transbridge.ui.workbench.translation_table import COL_TRANSLATION


def wait_until(predicate):
    deadline = monotonic() + 5
    while not predicate() and monotonic() < deadline:
        QTest.qWait(10)
    _APP.processEvents()
    assert predicate()


@pytest.fixture
def workbench(project, monkeypatch, tmp_path):  # noqa: F811 - imported pytest fixture
    services, source_service, request, *_ = project
    config = ParatranzConfig(
        token="isolated-real-workbench-test-token",
        user_id=7,
        base_url="https://paratranz.example/api",
        credential_store=UnavailableCredentialStore(),
        environment={},
        config_path=str(tmp_path / "workbench-paratranz.json"),
    )
    binding = services.project_remote_bindings.set_paratranz_binding(
        ParaTranzProjectBinding(42, "真实工作台云端", config.base_url, 7), request
    )
    assert binding.is_success, binding.diagnostics
    monkeypatch.setattr(ParatranzConfig, "create_or_load", lambda: config)
    context = AppContext(
        project_projection=services.project_projection,
        project_commands=services.gui_project_commands,
        project_remote_bindings=services.project_remote_bindings,
        runtime_context=request,
    )
    context.current_user = {"id": 7}
    hydration = services.gui_project_commands.consume_create_hydration(context.active_project_id, request)
    assert hydration.is_success, hydration.diagnostics
    projection = services.project_projection.snapshot()
    slots = prepare_project_slots(hydration.value.sources, projection)
    token = context.begin_project_open()
    assert context.install_project_open(token, projection, slots)
    assert context.finish_project_open(token)
    sources = source_service.snapshot(request, endpoint=config.base_url, remote_project_id=42).value.sources
    locations = {
        source.source_id: next(
            location
            for location, slot in slots
            if next(iter(slot.collection)).identity.namespace == source.entries[0].identity.namespace
        )
        for source in sources
    }
    context.activate_slot(locations[sources[0].source_id])
    preview = Step2PreviewWidget(context)
    preview.refresh(context.collection)
    assert not context.authoritative_projection_diverged()
    rows, calls = {}, []
    inventory = []
    for file_id, source in enumerate(sources, 1):
        inventory.append({"id": file_id, "name": source.label + ".json"})
        rows[file_id] = [
            dict(
                paratranz_record_from_entry(entry),
                id=file_id * 100 + offset,
                fileId=file_id,
                translation=f"真实工作台云端译文{file_id}",
                stage=3,
            )
            for offset, entry in enumerate(source.entries)
        ]

    def send(_session, prepared, **_kwargs):
        path = urlparse(prepared.url).path
        calls.append(path)
        assert prepared.method == "GET" and urlparse(prepared.url).hostname == "paratranz.example"
        if path == "/api/projects":
            payload = [{"id": 42, "name": "真实工作台云端"}]
        elif path == "/api/projects/42/files":
            payload = inventory
        elif path.startswith("/api/projects/42/files/") and path.endswith("/translation"):
            payload = rows[int(path.split("/")[-2])]
        else:
            raise AssertionError(f"Unexpected HTTP request: {path}")
        response = requests.Response()
        response.status_code = 200
        response.url, response.request = prepared.url, prepared
        response._content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return response

    monkeypatch.setattr(requests.Session, "send", send)
    monkeypatch.setattr(file_publication, "_SHARED_BUDGET", RequestBudget(0))
    runtime = _runtime()
    facade = build_operation_plan_facade(runtime, request)
    dialog = facade.begin_download(context)
    assert dialog is not None
    wait_until(lambda: dialog._worker is None and dialog._sources is not None)
    yield SimpleNamespace(
        services=services,
        request=request,
        context=context,
        preview=preview,
        dialog=dialog,
        sources=sources,
        locations=locations,
        calls=calls,
    )
    if dialog._worker is not None:
        dialog.reject()
        wait_until(lambda: dialog._worker is None)
    dialog.close()
    preview.close()
    context.close_projection()
    _APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert facade.active_plan_count == 0


@pytest.mark.parametrize("all_sources", [False, True])
def test_real_download_updates_loaded_workbench_and_keeps_close_save_authority_consistent(workbench, all_sources):
    case = workbench
    selected, other = case.sources
    if all_sources:
        case.dialog._select_all.click()
    case.dialog._primary_button.click()
    wait_until(lambda: case.dialog._worker is None)
    assert "保存到当前本地版本" in case.dialog._status.text()
    active = case.services.project_lifecycle.active
    persisted = case.services.variants.read_snapshot(active.formal_variant_ref)
    assert persisted == active.variant.snapshot().to_dto()
    authority = {entry.entry_key: entry for entry in active.variant.snapshot().entries}
    assert authority[selected.entries[0].identity].translation == "真实工作台云端译文1"

    # Disk success alone is insufficient: these are the actual loaded objects
    # used by the Workbench and its exit/save authority check.
    visible = case.context.collection.get(selected.entries[0].identity)
    assert (visible.translation, visible.stage) == ("真实工作台云端译文1", 3)
    assert not case.context.authoritative_projection_diverged()
    assert not case.context.dirty
    table = case.preview._table
    assert table.model().index(0, COL_TRANSLATION).data(Qt.ItemDataRole.EditRole) == "真实工作台云端译文1"
    assert table.model().index(0, COL_TRANSLATION).data(Qt.ItemDataRole.UserRole) is visible

    case.context.activate_slot(case.locations[other.source_id])
    expected_other = ("真实工作台云端译文2", 3) if all_sources else ("", 0)
    other_entry = case.context.collection.get(other.entries[0].identity)
    assert (other_entry.translation, other_entry.stage) == expected_other
    case.context.activate_slot(case.locations[selected.source_id])
    assert case.context.collection.get(selected.entries[0].identity).translation == "真实工作台云端译文1"
    assert not case.context.authoritative_projection_diverged()

    host = SimpleNamespace(
        _ctx=case.context,
        _project_commands=case.services.gui_project_commands,
        _runtime_context=case.request,
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        saved = pool.submit(MainWindow._save_current_project, host).result(timeout=5)
    assert saved.is_success, saved.diagnostics
    prepared = case.services.current_project_opener.prepare_identity(
        case.context.active_project_id, case.context.active_variant_id, case.request
    )
    assert prepared.is_success, prepared.diagnostics
    token = case.context.begin_project_open()
    reopened = case.services.current_project_opener.activate(prepared.value, case.request)
    assert reopened.is_success, reopened.diagnostics
    snapshot = case.services.project_projection.snapshot()
    reopened_slots = prepare_project_slots(prepared.value.hydrations, snapshot)
    assert case.context.install_project_open(token, snapshot, reopened_slots)
    assert case.context.finish_project_open(token)
    case.context.activate_slot(case.locations[selected.source_id])
    assert case.context.collection.get(selected.entries[0].identity).translation == "真实工作台云端译文1"
    assert not case.context.authoritative_projection_diverged()
    assert not list((Path(case.services.root) / "snapshots").glob("*.json"))
    directory = case.services.gui_project_commands.task_result_directory(case.request)
    assert list((Path(directory) / "download-results").glob("*/merge-*.json"))
    assert case.calls.count("/api/projects") == 1
    assert case.calls.count("/api/projects/42/files/1/translation") == 1
    assert case.calls.count("/api/projects/42/files/2/translation") == int(all_sources)


def test_real_download_does_not_make_the_exit_save_guard_report_projection_authority_diverged(workbench):
    case = workbench
    case.dialog._primary_button.click()
    wait_until(lambda: case.dialog._worker is None)
    assert "保存到当前本地版本" in case.dialog._status.text()
    active = case.services.project_lifecycle.active
    assert case.services.variants.read_snapshot(active.formal_variant_ref) == active.variant.snapshot().to_dto()
    host = SimpleNamespace(
        _ctx=case.context,
        _project_commands=case.services.gui_project_commands,
        _runtime_context=case.request,
    )

    with ThreadPoolExecutor(max_workers=1) as pool:
        saved = pool.submit(MainWindow._save_current_project, host).result(timeout=5)

    assert saved.is_success, tuple((item.code, item.message) for item in saved.diagnostics)
    assert not case.context.authoritative_projection_diverged()


@pytest.mark.parametrize(("local_draft", "prepare_failure"), [(False, False), (True, False), (False, True)])
def test_real_save_waits_for_download_projection_and_still_rejects_a_genuine_new_draft(
    workbench, monkeypatch, caplog, local_draft, prepare_failure
):
    case = workbench
    key = case.sources[0].entries[0].identity
    assert case.services.gui_project_commands.update_entry(
        key, case.request, translation="下载前等待自动保存的编辑", stage=1
    ).is_success
    assert case.context.dirty
    entered, release, prepared, save_started = Event(), Event(), Event(), Event()
    real_prepare = project_view_binding.prepare_projected_entries

    def blocked_prepare(*args, **kwargs):
        entered.set()
        assert release.wait(5), "下载投影准备屏障未释放"
        try:
            if prepare_failure:
                raise OSError("测试准备投影失败")
            return real_prepare(*args, **kwargs)
        finally:
            prepared.set()

    monkeypatch.setattr(project_view_binding, "prepare_projected_entries", blocked_prepare)
    host = SimpleNamespace(
        _ctx=case.context,
        _project_commands=case.services.gui_project_commands,
        _runtime_context=case.request,
    )

    def save():
        save_started.set()
        return MainWindow._save_current_project(host)

    pool = ThreadPoolExecutor(max_workers=1)
    future = None
    try:
        case.dialog._primary_button.click()
        assert entered.wait(5)
        future = pool.submit(save)
        assert save_started.wait(5)
        release.set()
        assert prepared.wait(5)
        # Deliberately leave mutation_requested queued. Preparing or persisting
        # the download is not yet permission to compare an older visible view.
        with pytest.raises(FutureTimeoutError):
            future.result(timeout=0.05)
        assert case.context.collection.get(key).translation == "下载前等待自动保存的编辑"
        if local_draft:
            case.context.active_slot.collection = TranslationEntryCollection(
                replace(entry, translation="准备完毕后产生的真正本地草稿", stage=9) if entry.identity == key else entry
                for entry in case.context.collection
            )
        wait_until(lambda: future.done() and case.dialog._worker is None)
        saved = future.result(timeout=1)
    finally:
        release.set()
        if future is not None:
            wait_until(future.done)
        pool.shutdown(wait=True)

    active = case.services.project_lifecycle.active
    assert case.services.variants.read_snapshot(active.formal_variant_ref) == active.variant.snapshot().to_dto()
    assert "保存到当前本地版本" in case.dialog._status.text()
    if prepare_failure:
        assert "测试准备投影失败" in caplog.text
    if local_draft:
        assert not saved.is_success
        assert saved.diagnostics[0].code == "PROJECTION_AUTHORITY_DIVERGED"
        assert case.context.collection.get(key).translation == "准备完毕后产生的真正本地草稿"
        assert case.context.authoritative_projection_diverged()
    else:
        assert saved.is_success, tuple((item.code, item.message) for item in saved.diagnostics)
        assert case.context.collection.get(key).translation == "真实工作台云端译文1"
        assert not case.context.authoritative_projection_diverged()
        assert not case.context.dirty


def test_closing_projection_releases_a_background_authority_check_waiting_for_gui_delivery(workbench, monkeypatch):
    case = workbench
    entered, release, prepared, check_started = Event(), Event(), Event(), Event()
    real_prepare = project_view_binding.prepare_projected_entries

    def blocked_prepare(*args, **kwargs):
        entered.set()
        assert release.wait(5), "下载投影准备屏障未释放"
        try:
            return real_prepare(*args, **kwargs)
        finally:
            prepared.set()

    monkeypatch.setattr(project_view_binding, "prepare_projected_entries", blocked_prepare)

    def check():
        check_started.set()
        return case.context.authoritative_projection_diverged()

    pool = ThreadPoolExecutor(max_workers=1)
    future = None
    try:
        case.dialog._primary_button.click()
        assert entered.wait(5)
        future = pool.submit(check)
        assert check_started.wait(5)
        release.set()
        assert prepared.wait(5)
        with pytest.raises(FutureTimeoutError):
            future.result(timeout=0.05)
        case.context.close_projection()
        with pytest.raises(DomainError) as closed:
            future.result(timeout=1)
        assert closed.value.code == "PROJECT_VIEW_UNAVAILABLE"
    finally:
        release.set()
        if future is not None:
            wait_until(future.done)
        pool.shutdown(wait=True)
    wait_until(lambda: case.dialog._worker is None)
