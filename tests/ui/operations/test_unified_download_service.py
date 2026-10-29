"""Exercise default download composition with real service and file API ports."""

from contextlib import contextmanager
from dataclasses import replace
import json
from pathlib import Path
from time import monotonic
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from PyQt6.QtCore import QEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QDialog
import pytest
import requests

from tests.application.projects.test_categorized_upload import project as project  # noqa: F401
from tests.plugin_fixtures import write_plugin
from tests.ui.operations.test_batch_download_dialog import _APP
from tests.ui.operations.test_batch_download_session import case as batch_case_fixture, states
from transbridge.application.categorized_upload.classification import source_directory_names
from transbridge.application.ports.paratranz import ExternalServiceCategory, ExternalServiceError
from transbridge.config.paratranz import ParatranzConfig
from transbridge.config.paratranz_credentials import UnavailableCredentialStore
from transbridge.paratranz import file_publication
from transbridge.paratranz.file_publication import RequestBudget
from transbridge.ui.operations.batch_download_session import BatchDownloadSession
from transbridge.ui.operations.production import build_operation_plan_facade
from transbridge.ui.operations.sync_review_dialog import SyncReviewDialog

case = batch_case_fixture


@pytest.fixture
def default_download(case, monkeypatch, tmp_path):
    case.context.config = ParatranzConfig(
        token="isolated-production-composition-test-token",
        user_id=7,
        base_url="https://paratranz.example/api",
        credential_store=UnavailableCredentialStore(),
        environment={},
        config_path=str(tmp_path / "isolated-paratranz.json"),
    )
    case.context.target.endpoint = case.context.config.base_url
    calls, failures, projects, file_responses = [], set(), [{"id": 42, "name": "远端"}], {}

    def send(_session, request, **_kwargs):
        parsed = urlparse(request.url)
        calls.append((request.method, parsed.path, parse_qs(parsed.query)))
        assert request.method == "GET", "Downloads must never write cloud files or strings"
        assert parsed.hostname == "paratranz.example"
        status = 200
        if parsed.path == "/api/projects":
            assert parse_qs(parsed.query)["uid"] == ["7"]
            payload = projects
        elif parsed.path == "/api/projects/42/files":
            payload = [{"id": file_id, "name": path} for path, file_id in case.gateway.files.items()]
        elif parsed.path.startswith("/api/projects/42/files/") and parsed.path.endswith("/translation"):
            file_id = int(parsed.path.split("/")[-2])
            if file_id in file_responses:
                status, payload = file_responses[file_id]()
            elif file_id in failures:
                status, payload = 500, {"message": "isolated failed complete file read"}
            else:
                payload = case.gateway.rows[file_id]
        else:
            raise AssertionError(f"Unexpected production HTTP request: {parsed.path}")
        response = requests.Response()
        response.status_code = status
        response.url, response.request = request.url, request
        response._content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return response

    monkeypatch.setattr(requests.Session, "send", send)
    monkeypatch.setattr(file_publication, "_SHARED_BUDGET", RequestBudget(0))
    session = BatchDownloadSession(case.context, case.runtime)
    return SimpleNamespace(
        case=case,
        session=session,
        calls=calls,
        failures=failures,
        projects=projects,
        file_responses=file_responses,
    )


def compare(data, sources=None, *, policy="prefer_remote"):
    return data.session.compare(
        tuple(source.source_id for source in data.case.sources) if sources is None else sources,
        policy,
        (),
        lambda *_: None,
        data.case.cancel,
    )


def paths(data):
    return [path for _method, path, _query in data.calls]


def assert_persisted(data):
    active = data.case.services.project_lifecycle.active
    assert not active.dirty
    assert data.case.services.variants.read_snapshot(active.formal_variant_ref) == active.variant.snapshot().to_dto()
    assert data.case.services.projects.read_snapshot(active.project_ref) == active.project


def merge_evidence_files(data):
    directory = data.case.context.project_commands.task_result_directory(data.case.context.runtime_context)
    return tuple((Path(directory) / "download-results").glob("*/merge-*.json"))


def source_path(data, source_id):
    registration = next(source for source in data.case.context.project_sources if source["source_id"] == source_id)
    return Path(registration["location"])


def rename_registered_sources(data, labels):
    active = data.case.services.project_lifecycle.active
    document = dict(active.project.envelope.data)
    sources = [dict(source) for source in document["sources"]]
    for source in sources:
        if source["source_id"] in labels:
            source["display_name"] = labels[source["source_id"]]
    document["sources"] = sources
    candidate = replace(
        active.project,
        envelope=replace(active.project.envelope, data=document, revision=active.project.envelope.revision + 1),
    )
    result = data.case.services.project_lifecycle.commit_project_update(
        candidate, active.project.envelope.revision, data.case.context.runtime_context
    )
    assert result.is_success, result.diagnostics


def wait_for_dialog(dialog, predicate):
    deadline = monotonic() + 5
    while not predicate() and monotonic() < deadline:
        QTest.qWait(10)
    assert predicate(), f"Real download worker did not reach the expected UI state: {dialog._status.text()}"


@contextmanager
def real_download_dialog(data, selected_source=None):
    selected_source = data.case.sources[0] if selected_source is None else selected_source
    data.case.context.collection = selected_source.entries
    facade = build_operation_plan_facade(data.case.runtime, data.case.context.runtime_context)
    dialog = facade.begin_download(data.case.context)
    assert dialog is not None
    try:
        wait_for_dialog(dialog, lambda: dialog._worker is None and dialog._sources is not None)
        yield dialog
    finally:
        if dialog._worker is not None:
            dialog.reject()
            wait_for_dialog(dialog, lambda: dialog._worker is None)
        dialog.close()
        _APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert facade.active_plan_count == 0


def test_default_service_factory_reaches_permissions_and_current_file_reads_without_constructor_error(default_download):
    data = default_download
    preview = data.session.compare(
        tuple(source.source_id for source in data.case.sources), "prefer_remote", (), lambda *_: None, data.case.cancel
    )

    assert preview.updates == 2
    assert [(method, path) for method, path, _query in data.calls] == [
        ("GET", "/api/projects"),
        ("GET", "/api/projects/42/files"),
        ("GET", "/api/projects/42/files/1/translation"),
        ("GET", "/api/projects/42/files/2/translation"),
    ]


@pytest.mark.parametrize("project", [3], indirect=True)
def test_named_plugin_subdirectory_is_downloaded_and_saved_without_local_classification(default_download, monkeypatch):
    from transbridge.application.io import paratranz_classification

    data = default_download
    selected = next(source for source in data.case.sources if len(source.entries) == 3)
    old = selected.label + ".json"
    file_id = data.case.gateway.files.pop(old)
    data.case.gateway.files[selected.label + "/任务/对话_[Discerning the Transmundane].json"] = file_id
    monkeypatch.setattr(
        paratranz_classification,
        "classify_entries",
        lambda *_: pytest.fail("Download must not rebuild local category names"),
    )
    assert compare(data, (selected.source_id,)).updates == 3
    assert data.session.apply(lambda *_: None, data.case.cancel).updated == 3
    assert all(states(data.case)[entry.identity][0] for entry in selected.entries)
    assert_persisted(data)
    assert data.session.plan is None and not data.session._reads
    assert not list(Path(data.case.services.root).rglob("download-results"))
    assert paths(data).count(f"/api/projects/42/files/{file_id}/translation") == 1


def test_real_service_policy_changes_and_successful_save_reuse_the_complete_cloud_read(default_download):
    data = default_download
    assert compare(data).updates == 2
    key = data.case.sources[0].entries[0].identity
    assert data.case.context.project_commands.update_entry(
        key, data.case.context.runtime_context, translation="下载期间的新编辑", stage=1
    ).is_success
    assert compare(data, policy="keep_current").updates == 1
    assert compare(data).updates == 2
    calls = tuple(data.calls)

    receipt = data.session.apply(lambda *_: None, data.case.cancel)

    assert receipt.updated == 2 and tuple(data.calls) == calls
    assert {translation for translation, _stage in states(data.case).values()} == {"云端1", "云端2"}
    assert_persisted(data)
    assert not list((Path(data.case.services.root) / "snapshots").glob("*.json"))
    assert data.session.plan is None and not data.session._reads


def test_real_file_api_partial_read_failure_retries_only_the_failed_source(default_download):
    data = default_download
    data.failures.add(2)
    initial = compare(data)
    assert initial.updates == 1 and any("下载失败" in detail for detail in initial.details)
    data.failures.clear()

    assert compare(data).updates == 2
    assert data.session.apply(lambda *_: None, data.case.cancel).updated == 2

    assert paths(data).count("/api/projects") == 1
    assert paths(data).count("/api/projects/42/files") == 2
    assert paths(data).count("/api/projects/42/files/1/translation") == 1
    assert paths(data).count("/api/projects/42/files/2/translation") == 2
    assert_persisted(data)


def test_real_service_permission_failure_can_retry_without_reopening_the_window(default_download):
    data = default_download
    data.projects.clear()
    with pytest.raises(PermissionError, match="无权访问"):
        compare(data)
    assert paths(data) == ["/api/projects"]
    data.projects.append({"id": 42, "name": "远端"})

    assert compare(data).updates == 2

    assert paths(data).count("/api/projects") == 2
    assert paths(data).count("/api/projects/42/files") == 1


def test_real_service_save_failure_preserves_review_and_retries_without_http(default_download, monkeypatch):
    data = default_download
    compare(data)
    calls, before = tuple(data.calls), states(data.case)
    original = data.case.services.variants.save_if_revision

    def fail(*_args, **_kwargs):
        raise OSError("真实持久化端口临时写入失败")

    with monkeypatch.context() as patch:
        patch.setattr(data.case.services.variants, "save_if_revision", fail)
        with pytest.raises(RuntimeError, match="临时写入失败"):
            data.session.apply(lambda *_: None, data.case.cancel)
    assert states(data.case) == before
    assert data.case.services.variants.save_if_revision == original

    assert compare(data).updates == 2
    assert data.session.apply(lambda *_: None, data.case.cancel).updated == 2

    assert len(data.calls) > len(calls)
    assert_persisted(data)


def test_new_selection_uses_current_service_and_does_not_redownload_cached_sources(default_download):
    data = default_download
    first, second = data.case.sources
    assert compare(data, (first.source_id,)).updates == 1
    assert compare(data, (first.source_id, second.source_id)).updates == 2

    assert paths(data).count("/api/projects") == 1
    assert paths(data).count("/api/projects/42/files") == 2
    assert paths(data).count("/api/projects/42/files/1/translation") == 1
    assert paths(data).count("/api/projects/42/files/2/translation") == 1


def test_source_rename_keeps_frozen_selected_result_without_cross_source_reads(default_download):
    data = default_download
    first, second = data.case.sources
    assert compare(data, (first.source_id,)).updates == 1
    active = data.case.services.project_lifecycle.active
    document = dict(active.project.envelope.data)
    sources = [dict(source) for source in document["sources"]]
    for source in sources:
        source["display_name"] = first.label
    document["sources"] = sources
    changed = replace(
        active.project,
        envelope=replace(active.project.envelope, data=document, revision=active.project.envelope.revision + 1),
    )
    assert data.case.services.project_lifecycle.commit_project_update(
        changed, active.project.envelope.revision, data.case.context.runtime_context
    ).is_success
    # A rename affects future tasks; this task keeps its already downloaded input.
    renamed_sources = tuple(replace(source, label=first.label) for source in data.case.sources)
    names = source_directory_names(renamed_sources)
    data.case.gateway.files = {names[first.source_id] + ".json": 1, names[second.source_id] + ".json": 2}

    preview = compare(data, (first.source_id,))
    assert preview.updates == 1
    assert any(first.label + ".json" in detail for detail in preview.details)
    assert data.session.apply(lambda *_: None, data.case.cancel).updated == 1

    assert paths(data).count("/api/projects/42/files/1/translation") == 1
    assert paths(data).count("/api/projects/42/files/2/translation") == 0
    assert states(data.case)[second.entries[0].identity] == ("", 0)
    assert_persisted(data)


def test_download_uses_saved_authoritative_source_until_an_explicit_source_update(default_download):
    data = default_download
    first = data.case.sources[0]
    assert compare(data, (first.source_id,)).updates == 1
    selected_path = source_path(data, first.source_id)
    source_bytes = selected_path.read_bytes()
    calls = tuple(data.calls)
    write_plugin(selected_path, [(0x800, "Shared", "Physical source changed")])

    preview = compare(data, (first.source_id,))

    assert preview.updates == 1
    assert not any("读取失败" in detail for detail in preview.details)
    assert tuple(data.calls) == calls
    selected_path.write_bytes(source_bytes)
    assert compare(data, (first.source_id,)).updates == 1
    assert tuple(data.calls) == calls


def test_an_unselected_broken_source_does_not_block_selected_source_download(default_download):
    data = default_download
    selected, unselected = data.case.sources
    unselected_path = source_path(data, unselected.source_id)
    unselected_path.write_bytes(b"not a plugin")

    preview = compare(data, (selected.source_id,))

    assert preview.updates == 1
    assert data.session.apply(lambda *_: None, data.case.cancel).updated == 1
    assert paths(data).count("/api/projects/42/files/1/translation") == 1
    assert paths(data).count("/api/projects/42/files/2/translation") == 0
    assert states(data.case)[unselected.entries[0].identity] == ("", 0)
    assert_persisted(data)


def test_cancelled_real_membership_request_does_not_retry_or_misreport_timeout(default_download, monkeypatch):
    data = default_download
    attempts = []

    def timeout_after_cancel(_session, request, **_kwargs):
        attempts.append(urlparse(request.url).path)
        data.case.cancel.set()
        raise requests.Timeout("cancel was requested during the membership request")

    with monkeypatch.context() as patch:
        patch.setattr(requests.Session, "send", timeout_after_cancel)
        with pytest.raises(ExternalServiceError) as caught:
            compare(data)

    assert caught.value.category is ExternalServiceCategory.CANCELLED
    assert attempts == ["/api/projects"]
    data.case.cancel.clear()
    assert compare(data).updates == 2


def test_default_source_catalog_load_does_not_parse_sources_or_read_cloud_and_selects_current(
    default_download, monkeypatch
):
    data = default_download
    selected, other = data.case.sources
    data.case.context.collection = selected.entries
    service = data.case.context.project_commands._categorized_uploads

    def forbidden(*_args, **_kwargs):
        raise AssertionError("The initial source catalog must not hydrate plugins or read baseline documents")

    monkeypatch.setattr(service._source_preparer, "prepare_source", forbidden)
    monkeypatch.setattr(data.case.services.baselines, "provide", forbidden)

    options = data.session.load_sources(lambda *_: None, data.case.cancel)

    assert {option.source_id for option in options} == {selected.source_id, other.source_id}
    assert {option.source_id for option in options if option.selected} == {selected.source_id}
    assert {option.count for option in options} == {1}
    assert data.calls == []


def test_default_facade_real_dialog_worker_http_review_and_save_are_one_complete_path(default_download):
    data = default_download
    selected, other = data.case.sources
    with real_download_dialog(data) as dialog:
        assert dialog.selection() == (selected.source_id,)
        assert dialog._source_list.count() == 2
        assert dialog._primary_button.isEnabled() and data.calls == []
        dialog._primary_button.click()
        wait_for_dialog(dialog, lambda: dialog._worker is None)

        assert "并保存到当前本地版本" in dialog._status.text()
        assert "已更新 1 条" in dialog._status.text()
        assert "临时下载数据已清理" in dialog._summary.text()
        assert states(data.case)[selected.entries[0].identity] == ("云端1", 3)
        assert states(data.case)[other.entries[0].identity] == ("", 0)
        assert_persisted(data)
        assert paths(data) == [
            "/api/projects",
            "/api/projects/42/files",
            "/api/projects/42/files/1/translation",
        ]
        assert not dialog._primary_button.isEnabled()
        assert dialog._primary_button.text() == "已保存"
        calls_after_save = tuple(data.calls)
        dialog._primary_button.click()
        QTest.qWait(10)
        assert tuple(data.calls) == calls_after_save
        dialog._select_all.click()
        assert dialog._primary_button.isEnabled() and dialog._primary_button.text() == "下载并保存"
        assert tuple(data.calls) == calls_after_save


def test_real_one_click_disk_failure_retries_same_button_without_http_or_another_restore_point(
    default_download, monkeypatch
):
    data = default_download
    selected = data.case.sources[0]
    original_save = data.case.services.variants.save_if_revision
    attempts = []

    def fail_first_save(*args, **kwargs):
        attempts.append(True)
        if len(attempts) == 1:
            raise OSError("首次自动保存遇到临时磁盘故障")
        return original_save(*args, **kwargs)

    monkeypatch.setattr(data.case.services.variants, "save_if_revision", fail_first_save)
    with real_download_dialog(data) as dialog:
        dialog._primary_button.click()
        wait_for_dialog(dialog, lambda: dialog._worker is None)
        assert "临时磁盘故障" in dialog._status.text()
        assert states(data.case)[selected.entries[0].identity] == ("", 0)
        assert dialog._primary_button.isEnabled() and dialog._primary_button.text() == "重试未完成项"
        initial_http = tuple(data.calls)
        restore_points = {path: path.read_bytes() for path in merge_evidence_files(data)}
        assert not restore_points

        dialog._primary_button.click()
        wait_for_dialog(dialog, lambda: dialog._worker is None)

        assert "并保存到当前本地版本" in dialog._status.text()
        assert states(data.case)[selected.entries[0].identity] == ("云端1", 3)
        assert len(data.calls) > len(initial_http)
        assert {path: path.read_bytes() for path in merge_evidence_files(data)} == restore_points
        assert attempts == [True, True]
        assert_persisted(data)


def test_real_one_click_saves_reliable_source_then_accepting_review_automatically_saves_the_conflict(
    default_download, monkeypatch
):
    data = default_download
    first, second = data.case.sources
    data.case.gateway.rows[1].append(dict(data.case.gateway.rows[1][0], id=101, translation="不同候选译文"))
    accepted = []

    def choose_review_candidate(subject):
        assert len(subject._model.rows) == 1
        row = subject._model.rows[0]
        candidate = next(item for item in row.candidates if item.translation == "云端1" and item.selectable)
        subject._model.set_decision((0,), candidate.candidate_id)
        accepted.append(row.row_id)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(SyncReviewDialog, "exec", choose_review_candidate)
    with real_download_dialog(data) as dialog:
        dialog._select_all.click()
        dialog._primary_button.click()
        wait_for_dialog(dialog, lambda: dialog._worker is None)

        assert states(data.case)[first.entries[0].identity] == ("", 0)
        assert states(data.case)[second.entries[0].identity] == ("云端2", 3)
        assert_persisted(data)
        assert dialog._preview is not None and dialog._preview.deferred == 1
        assert dialog._review_button.isVisible() and dialog._review_button.isEnabled()
        initial_http = tuple(data.calls)

        dialog._review_button.click()
        wait_for_dialog(dialog, lambda: dialog._worker is None)

        assert len(accepted) == 1
        assert states(data.case)[first.entries[0].identity] == ("云端1", 3)
        assert states(data.case)[second.entries[0].identity] == ("云端2", 3)
        assert "并保存到当前本地版本" in dialog._status.text()
        assert tuple(data.calls) == initial_http
        assert_persisted(data)


def test_real_one_click_saves_dirty_current_version_when_remote_content_already_matches(default_download):
    data = default_download
    selected = data.case.sources[0]
    key = selected.entries[0].identity
    assert data.case.context.project_commands.update_entry(
        key, data.case.context.runtime_context, translation="云端1", stage=3
    ).is_success
    active = data.case.services.project_lifecycle.active
    assert active.dirty
    old_disk = data.case.services.variants.read_snapshot(active.formal_variant_ref)

    with real_download_dialog(data) as dialog:
        dialog._primary_button.click()
        wait_for_dialog(dialog, lambda: dialog._worker is None)

        assert "并保存到当前本地版本" in dialog._status.text()
        assert states(data.case)[key] == ("云端1", 3)
        assert_persisted(data)
        active = data.case.services.project_lifecycle.active
        assert data.case.services.variants.read_snapshot(active.formal_variant_ref) != old_disk
        assert not list((Path(data.case.services.root) / "snapshots").glob("*.json"))
        assert not merge_evidence_files(data)
        assert paths(data) == [
            "/api/projects",
            "/api/projects/42/files",
            "/api/projects/42/files/1/translation",
        ]


def test_real_one_click_with_no_remote_files_preserves_dirty_state_without_false_save(default_download):
    data = default_download
    selected = data.case.sources[0]
    key = selected.entries[0].identity
    assert data.case.context.project_commands.update_entry(
        key, data.case.context.runtime_context, translation="本地尚未保存的译文", stage=1
    ).is_success
    data.case.gateway.files.clear()
    active = data.case.services.project_lifecycle.active
    disk_before = data.case.services.variants.read_snapshot(active.formal_variant_ref)

    with real_download_dialog(data) as dialog:
        dialog._primary_button.click()
        wait_for_dialog(dialog, lambda: dialog._worker is None)

        assert data.case.services.project_lifecycle.active.dirty
        assert states(data.case)[key] == ("本地尚未保存的译文", 1)
        assert data.case.services.variants.read_snapshot(active.formal_variant_ref) == disk_before
        assert not list((Path(data.case.services.root) / "snapshots").glob("*.json"))
        assert "并保存到当前本地版本" not in dialog._status.text()
        details = dialog._details.toPlainText()
        assert "云端未找到匹配文件" in details
        assert selected.label + ".json" in details
        assert Path(selected.label).stem + ".json" in details
        assert dialog._primary_button.isEnabled()
        assert paths(data) == ["/api/projects", "/api/projects/42/files"]


def test_real_one_click_all_conflicts_can_save_existing_local_edits_and_keep_pending_review(default_download):
    data = default_download
    selected = data.case.sources[0]
    key = selected.entries[0].identity
    assert data.case.context.project_commands.update_entry(
        key, data.case.context.runtime_context, translation="需保留并保存的本地编辑", stage=1
    ).is_success
    data.case.gateway.rows[1].append(dict(data.case.gateway.rows[1][0], id=101, translation="另一个候选译文"))

    with real_download_dialog(data) as dialog:
        dialog._primary_button.click()
        wait_for_dialog(dialog, lambda: dialog._worker is None)

        assert states(data.case)[key] == ("需保留并保存的本地编辑", 1)
        assert_persisted(data)
        assert dialog._preview is not None and dialog._preview.deferred == 1
        assert not dialog._preview.can_save
        assert dialog._review_button.isVisible() and dialog._review_button.isEnabled()
        assert not list((Path(data.case.services.root) / "snapshots").glob("*.json"))
        assert not merge_evidence_files(data)


@pytest.mark.parametrize("legacy_path", ["LegacyPlugin.json", "legacyplugin.json"])
def test_real_one_click_accepts_unique_extensionless_legacy_root_and_case_variant(default_download, legacy_path):
    data = default_download
    selected = data.case.sources[0]
    rename_registered_sources(data, {selected.source_id: "LegacyPlugin.esm"})
    data.case.gateway.files = {legacy_path: 1}

    with real_download_dialog(data) as dialog:
        dialog._primary_button.click()
        wait_for_dialog(dialog, lambda: dialog._worker is None)

        assert states(data.case)[selected.entries[0].identity] == ("云端1", 3)
        assert "并保存到当前本地版本" in dialog._status.text()
        assert legacy_path in dialog._details.toPlainText()
        assert_persisted(data)
        assert paths(data) == [
            "/api/projects",
            "/api/projects/42/files",
            "/api/projects/42/files/1/translation",
        ]


@pytest.mark.parametrize(
    "current_path", ["LegacyPlugin.esm.json", "LegacyPlugin.esm/人名.json", "LegacyPlugin.esm_1.json"]
)
def test_real_one_click_prefers_extension_qualified_layout_without_mixing_legacy_content(
    default_download, current_path
):
    data = default_download
    selected = data.case.sources[0]
    rename_registered_sources(data, {selected.source_id: "LegacyPlugin.esm"})
    data.case.gateway.files = {current_path: 1, "LegacyPlugin.json": 9}
    data.case.gateway.rows[1] = [dict(row, translation="采用带扩展名的新布局") for row in data.case.gateway.rows[1]]
    data.case.gateway.rows[9] = [
        dict(row, id=900, fileId=9, translation="不应采用旧布局") for row in data.case.gateway.rows[1]
    ]

    with real_download_dialog(data) as dialog:
        dialog._primary_button.click()
        wait_for_dialog(dialog, lambda: dialog._worker is None)

        assert states(data.case)[selected.entries[0].identity] == ("采用带扩展名的新布局", 3)
        assert "/api/projects/42/files/9/translation" not in paths(data)
        assert paths(data).count("/api/projects/42/files/1/translation") == 1
        assert_persisted(data)


@pytest.mark.parametrize(
    "current_path", ["LegacyPlugin.esm.json", "LegacyPlugin.esm/人名.json", "LegacyPlugin.esm_1.json"]
)
def test_real_one_click_new_layout_read_failure_never_falls_back_to_stale_legacy_root(default_download, current_path):
    data = default_download
    selected = data.case.sources[0]
    rename_registered_sources(data, {selected.source_id: "LegacyPlugin.esm"})
    data.case.gateway.files = {current_path: 1, "LegacyPlugin.json": 9}
    data.case.gateway.rows[9] = [dict(row, id=900, fileId=9) for row in data.case.gateway.rows[1]]
    data.failures.add(1)
    dirty_before = data.case.services.project_lifecycle.active.dirty

    with real_download_dialog(data) as dialog:
        dialog._primary_button.click()
        wait_for_dialog(dialog, lambda: dialog._worker is None)

        assert states(data.case)[selected.entries[0].identity] == ("", 0)
        assert "并保存到当前本地版本" not in dialog._status.text()
        assert "下载失败" in dialog._details.toPlainText()
        assert "/api/projects/42/files/9/translation" not in paths(data)
        assert not list((Path(data.case.services.root) / "snapshots").glob("*.json"))
        assert data.case.services.project_lifecycle.active.dirty == dirty_before


def test_real_one_click_404_refresh_cannot_switch_to_legacy_until_an_explicit_retry(default_download):
    data = default_download
    selected = data.case.sources[0]
    rename_registered_sources(data, {selected.source_id: "LegacyPlugin.esm"})
    data.case.gateway.files = {"LegacyPlugin.esm.json": 1, "LegacyPlugin.json": 9}
    data.case.gateway.rows[9] = [
        dict(row, id=900, fileId=9, translation="下次明确重试才采用的旧布局") for row in data.case.gateway.rows[1]
    ]

    def new_file_disappeared():
        data.case.gateway.files = {"LegacyPlugin.json": 9}
        return 404, {"message": "new file was removed while downloading"}

    data.file_responses[1] = new_file_disappeared
    with real_download_dialog(data) as dialog:
        dialog._primary_button.click()
        wait_for_dialog(dialog, lambda: dialog._worker is None)

        assert states(data.case)[selected.entries[0].identity] == ("", 0)
        assert "下载失败" in dialog._details.toPlainText()
        assert "/api/projects/42/files/9/translation" not in paths(data)
        assert paths(data).count("/api/projects/42/files") == 2
        assert not list((Path(data.case.services.root) / "snapshots").glob("*.json"))

        dialog._primary_button.click()
        wait_for_dialog(dialog, lambda: dialog._worker is None)

        assert states(data.case)[selected.entries[0].identity] == ("下次明确重试才采用的旧布局", 3)
        assert paths(data).count("/api/projects/42/files/9/translation") == 1
        assert paths(data).count("/api/projects/42/files") == 3
        assert_persisted(data)


def test_real_one_click_does_not_guess_legacy_owner_when_unselected_plugin_has_same_stem(default_download):
    data = default_download
    selected, unselected = data.case.sources
    rename_registered_sources(data, {selected.source_id: "Shared.esp", unselected.source_id: "Shared.esm"})
    data.case.gateway.files = {"Shared.json": 1}

    with real_download_dialog(data) as dialog:
        assert dialog.selection() == (selected.source_id,)
        dialog._primary_button.click()
        wait_for_dialog(dialog, lambda: dialog._worker is None)

        assert states(data.case)[selected.entries[0].identity] == ("", 0)
        assert states(data.case)[unselected.entries[0].identity] == ("", 0)
        assert "/api/projects/42/files/1/translation" not in paths(data)
        assert "并保存到当前本地版本" not in dialog._status.text()
        assert not list((Path(data.case.services.root) / "snapshots").glob("*.json"))


def test_real_content_download_ignores_candidate_context_and_stage_differences(default_download):
    data = default_download
    selected = data.case.sources[0]
    original = data.case.gateway.rows[1][0]
    data.case.gateway.rows[1] = [
        dict(original, context="cloud metadata A", stage=1),
        dict(original, id=101, context="cloud metadata B", stage=5),
    ]

    with real_download_dialog(data) as dialog:
        dialog._primary_button.click()
        wait_for_dialog(dialog, lambda: dialog._worker is None)

        assert states(data.case)[selected.entries[0].identity][0] == "云端1"
        assert not dialog._review_button.isVisible()
        assert_persisted(data)


def test_real_content_download_uses_unique_exact_original_after_remote_key_changes(default_download):
    data = default_download
    selected = data.case.sources[0]
    data.case.gateway.rows[1] = [dict(data.case.gateway.rows[1][0], key="changed-cloud-key")]

    with real_download_dialog(data) as dialog:
        dialog._primary_button.click()
        wait_for_dialog(dialog, lambda: dialog._worker is None)

        assert states(data.case)[selected.entries[0].identity][0] == "云端1"
        assert not dialog._review_button.isVisible()
        assert_persisted(data)


@pytest.mark.parametrize("project", [2], indirect=True)
def test_real_content_download_can_reuse_same_original_translation_for_distinct_local_keys_without_remote_id(
    default_download,
):
    data = default_download
    selected = next(source for source in data.case.sources if len(source.entries) == 2)
    file_id = data.case.gateway.files[selected.label + ".json"]
    row = dict(data.case.gateway.rows[file_id][0], key="shared-content-key", translation="可复用的唯一内容")
    row.pop("id")
    data.case.gateway.rows[file_id] = [row]

    with real_download_dialog(data, selected) as dialog:
        dialog._primary_button.click()
        wait_for_dialog(dialog, lambda: dialog._worker is None)

        for entry in selected.entries:
            assert states(data.case)[entry.identity][0] == "可复用的唯一内容"
        assert "已更新 2 条" in dialog._status.text()
        assert not dialog._review_button.isVisible()
        assert_persisted(data)


def test_real_content_download_never_chooses_between_different_nonempty_translations_without_evidence(default_download):
    data = default_download
    selected = data.case.sources[0]
    data.case.gateway.rows[1].append(dict(data.case.gateway.rows[1][0], id=101, translation="真正不同的候选译文"))

    with real_download_dialog(data) as dialog:
        dialog._primary_button.click()
        wait_for_dialog(dialog, lambda: dialog._worker is None)

        assert states(data.case)[selected.entries[0].identity] == ("", 0)
        assert dialog._preview is not None and dialog._preview.deferred == 1
        assert dialog._review_button.isVisible() and dialog._review_button.isEnabled()
        assert not list((Path(data.case.services.root) / "snapshots").glob("*.json"))


def test_real_content_download_retains_local_text_when_it_uniquely_matches_one_remote_translation(default_download):
    data = default_download
    selected = data.case.sources[0]
    key = selected.entries[0].identity
    assert data.case.context.project_commands.update_entry(
        key, data.case.context.runtime_context, translation="云端1", stage=3
    ).is_success
    data.case.gateway.rows[1].append(dict(data.case.gateway.rows[1][0], id=101, translation="真正不同的候选译文"))

    with real_download_dialog(data) as dialog:
        dialog._primary_button.click()
        wait_for_dialog(dialog, lambda: dialog._worker is None)

        assert states(data.case)[key] == ("云端1", 3)
        assert not dialog._review_button.isVisible()
        assert_persisted(data)


def test_real_partial_content_download_retries_only_unfinished_source_after_success_is_saved(default_download):
    data = default_download
    first, second = data.case.sources
    data.failures.add(2)

    with real_download_dialog(data) as dialog:
        dialog._select_all.click()
        dialog._primary_button.click()
        wait_for_dialog(dialog, lambda: dialog._worker is None)

        assert states(data.case)[first.entries[0].identity][0] == "云端1"
        assert states(data.case)[second.entries[0].identity] == ("", 0)
        assert_persisted(data)
        assert dialog._primary_button.isEnabled() and dialog._primary_button.text() == "重试未完成项"
        original_restore_points = {path: path.read_bytes() for path in merge_evidence_files(data)}
        assert not original_restore_points
        data.failures.clear()

        dialog._primary_button.click()
        wait_for_dialog(dialog, lambda: dialog._worker is None)

        assert states(data.case)[first.entries[0].identity][0] == "云端1"
        assert states(data.case)[second.entries[0].identity][0] == "云端2"
        assert_persisted(data)
        assert not dialog._primary_button.isEnabled() and dialog._primary_button.text() == "已保存"
        assert paths(data).count("/api/projects") == 2
        assert paths(data).count("/api/projects/42/files/1/translation") == 2
        assert paths(data).count("/api/projects/42/files/2/translation") == 2
        assert all(path.read_bytes() == content for path, content in original_restore_points.items())
