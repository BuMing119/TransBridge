from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from itertools import count
import json
import os
from pathlib import Path
from threading import Event
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
import pytest

from tests.application.projects.test_categorized_upload import project as project  # noqa: F401
from tests.conftest import make_test_collection
from tests.ui.operations.current_download_support import patch_current_download_gateway
from transbridge.application.contracts import OperationResult
from transbridge.application.ports.paratranz import ParaTranzEntry
from transbridge.application.tasks import (
    CallbackThreadBackend,
    JobState,
    OwnerRef,
    TaskRuntime,
    ThreadBackend,
    TransitionError,
)
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.paratranz.service import ParaTranzService
from transbridge.persistence.project_snapshots import ProjectSnapshotRepository
from transbridge.persistence.v2.variant import VariantSnapshot
from transbridge.persistence.variant_store import VariantStore
from transbridge.ui.operations.ordinary_upload_scope import ordinary_upload_path
from transbridge.ui.operations.paratranz_dialog import ParaTranzSyncDialog
from transbridge.ui.operations.paratranz_sync import build_paratranz_sync_features
from transbridge.ui.operations.plan_view import OperationKind
from transbridge.ui.operations.production import build_operation_plan_facade

_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def download_case(tmp_path, monkeypatch):
    variant_root = tmp_path / "variant"
    store = VariantStore(variant_root / "current.json")
    collection = make_test_collection(1)
    persisted_entry = replace(next(iter(collection)), translation="磁盘上的旧译文", stage=1)
    store.collect_from([persisted_entry], {}, {})
    store.save()
    entry = replace(persisted_entry, translation="尚未保存的本地修改")
    store.dirty = True
    context = SimpleNamespace(
        collection=TranslationEntryCollection([entry]),
        esp_path="source.esp",
        paratranz_binding={
            "project_id": 42,
            "project_name": "云端汉化项目",
            "endpoint": "https://paratranz.cn",
            "account_user_id": 5,
        },
        project_revision=7,
        variant_revision=3,
        config=SimpleNamespace(
            token="configured",
            base_url="https://paratranz.cn",
            user_id=5,
            config_revision=1,
        ),
        current_user={"id": 5},
        active_project_id="local-project",
        active_version_identity=("local-project", "variant"),
        project_name="本地工程",
        active_variant="正式版",
        active_project=SimpleNamespace(variant_dir=lambda _name: variant_root),
        variant_store=store,
        uses_authoritative_projection=False,
        entry_labels={entry.id: {"review"}},
        label_library={"review": {"name": "待复核"}},
        dirty=True,
    )
    events = []
    service = SimpleNamespace(
        list_projects=lambda **_kwargs: (SimpleNamespace(project_id=42, name="云端汉化项目"),),
        list_entries=lambda *_args, **_kwargs: (
            ParaTranzEntry(7, entry.key, entry.original, "云端译文", entry.context, 1),
        ),
        close=lambda: events.append("closed"),
    )
    monkeypatch.setattr(ParaTranzService, "from_config", classmethod(lambda _cls, _config: service))
    patch_current_download_gateway(monkeypatch, service)
    download = next(
        feature
        for feature in build_paratranz_sync_features(SimpleNamespace())
        if feature.kind is OperationKind.DOWNLOAD
    )
    queued = []
    tasks = TaskRuntime(
        id_generator=SimpleNamespace(new_id=lambda: "download-recovery-run"),
        clock=SimpleNamespace(now=lambda: datetime.now(UTC)),
        backend=CallbackThreadBackend(lambda _run_id, target: queued.append(target)),
    )
    return SimpleNamespace(
        context=context,
        download=download,
        queued=queued,
        tasks=tasks,
        owner=OwnerRef("gui", "gui.operation-plan"),
        events=events,
        variant_root=variant_root,
        snapshot_dir=variant_root / "snapshots",
        entry=entry,
    )


def _submit(case):
    draft = case.download.create_draft(case.context, False, {})
    preflight = case.download.mapper.preflight(draft)
    assert preflight.ready
    assert "未保存修改" in draft.backup_summary
    return case.download.submit(draft, preflight, case.owner, case.tasks)


def test_dirty_download_creates_recoverable_snapshot_without_manual_save(download_case) -> None:
    case = download_case

    ref = _submit(case)

    # Neither snapshot I/O nor the local overwrite runs on the confirming caller.
    assert len(case.queued) == 1
    assert not case.snapshot_dir.exists()
    assert next(iter(case.context.collection)).translation == "尚未保存的本地修改"
    case.queued[0]()

    assert case.tasks.get(ref, case.owner).state is JobState.COMPLETED
    snapshots = VariantStore.list_snapshots(case.snapshot_dir)
    assert len(snapshots) == 1
    assert "ParaTranz-下载前-" in snapshots[0]["name"]
    snapshot = VariantStore.load_snapshot(Path(snapshots[0]["path"]))
    assert snapshot.translations == {case.entry.id: "尚未保存的本地修改"}
    assert snapshot.entry_states[case.entry.id]["stage"] == 1
    assert snapshot.labels == case.context.entry_labels
    assert snapshot.label_library == case.context.label_library
    assert VariantStore.load(case.variant_root / "current.json").translations == {case.entry.id: "磁盘上的旧译文"}
    assert next(iter(case.context.collection)).translation == "云端译文"
    assert case.events == ["closed"]


def test_autosave_completion_does_not_invalidate_download_plan(download_case) -> None:
    case = download_case
    draft = case.download.create_draft(case.context, False, {})
    case.context.variant_store.collect_from(
        list(case.context.collection), case.context.entry_labels, case.context.label_library
    )
    case.context.variant_store.save()
    case.context.dirty = False

    refreshed = case.download.create_draft(case.context, False, {})
    preflight = case.download.mapper.preflight(draft)

    assert preflight.ready
    assert refreshed.request_digest == draft.request_digest
    ref = case.download.submit(draft, preflight, case.owner, case.tasks)
    case.queued[0]()
    assert case.tasks.get(ref, case.owner).state is JobState.COMPLETED


@pytest.mark.parametrize("authoritative", [False, True], ids=["legacy-io-error", "v2-failed-result"])
def test_snapshot_failure_keeps_unsaved_local_content(download_case, monkeypatch, authoritative) -> None:
    case = download_case
    if authoritative:
        case.context.uses_authoritative_projection = True
        case.context.runtime_context = object()
        case.context.project_commands = SimpleNamespace(
            save_snapshot=lambda *_args: OperationResult.from_exception(OSError("无法写入下载前历史还原点"))
        )
    else:

        def fail_snapshot(*_args):
            raise OSError("无法写入下载前历史还原点")

        # Recovery writes a detached store so unsaved state never changes the
        # live version. Inject the I/O failure at that actual write boundary.
        monkeypatch.setattr(VariantStore, "save_snapshot", fail_snapshot)
    before = case.context.collection

    ref = _submit(case)
    case.queued[0]()

    assert case.tasks.get(ref, case.owner).state is JobState.FAILED
    assert case.context.collection is before
    assert next(iter(before)).translation == "尚未保存的本地修改"
    assert not case.snapshot_dir.exists()
    assert case.events == ["closed"]


def test_content_edit_after_preflight_still_prevents_stale_overwrite(download_case) -> None:
    case = download_case
    ref = _submit(case)
    case.context.collection = TranslationEntryCollection([replace(case.entry, translation="确认后又编辑了本地译文")])

    case.queued[0]()

    assert case.tasks.get(ref, case.owner).state is JobState.FAILED
    assert next(iter(case.context.collection)).translation == "确认后又编辑了本地译文"
    assert not case.snapshot_dir.exists()
    assert case.events == ["closed"]


class _AuthorityDownloadContext:
    uses_authoritative_projection = True
    active_variant = "默认"
    project_name = "自动保存下载"

    def __init__(self, services, request):
        self.services = services
        self.project_commands = services.gui_project_commands
        self.runtime_context = request
        self.config = SimpleNamespace(token="configured", base_url="https://paratranz.cn", user_id=7, config_revision=1)
        self.current_user = {"id": 7}
        self.paratranz_binding = {
            "project_id": 42,
            "project_name": "云端汉化项目",
            "endpoint": "https://paratranz.cn",
            "account_user_id": 7,
        }

    @property
    def active_project_id(self):
        return self.services.project_lifecycle.active.project_ref.identity.value

    @property
    def active_version_identity(self):
        return self.active_project_id, self.services.project_lifecycle.active.formal_variant_ref.identity.value

    @property
    def project_revision(self):
        return self.services.project_lifecycle.active.project.envelope.revision

    @property
    def variant_revision(self):
        return self.services.project_lifecycle.active.variant.revision

    @property
    def project_sources(self):
        return self.services.project_lifecycle.active.project.envelope.data["sources"]

    @property
    def project_relations(self):
        return self.services.project_lifecycle.active.project.envelope.data.get("source_relations", ())


@pytest.fixture
def authority_download_case(project, monkeypatch):  # noqa: F811 - imported project fixture
    services, source_service, request, first, second = project
    context = _AuthorityDownloadContext(services, request)
    initial = source_service.snapshot(request, endpoint=context.config.base_url, remote_project_id=42).value
    key = max(initial.sources, key=lambda source: len(source.entries)).entries[0].identity
    changed = context.project_commands.update_entry(key, request, translation="尚未保存的修改", stage=1)
    assert changed.is_success
    captured = source_service.snapshot(request, endpoint=context.config.base_url, remote_project_id=42).value
    source = next(source for source in captured.sources if source.entries[0].identity.namespace == key.namespace)
    context.collection = TranslationEntryCollection(source.entries)
    context.slots = {
        source.source_id: SimpleNamespace(collection=source.entries, label=source.label, esp_path=str(path))
        for source, path in zip(captured.sources, (first, second), strict=True)
    }
    context.esp_path = str(first)
    entry = next(iter(context.collection))
    remote = [ParaTranzEntry(99, entry.key, entry.original, "云端译文", entry.context, stage=3)]
    membership, outcomes = [], []

    def list_projects(**kwargs):
        membership.append(kwargs["uid"])
        return (SimpleNamespace(project_id=42, name="云端汉化项目"),)

    service = SimpleNamespace(
        list_projects=list_projects, list_entries=lambda *_args, **_kwargs: tuple(remote), close=lambda: None
    )
    monkeypatch.setattr(ParaTranzService, "from_config", classmethod(lambda _cls, _config: service))
    gateway = patch_current_download_gateway(monkeypatch, service, path=ordinary_upload_path(context))
    queued, ids = [], count()
    tasks = TaskRuntime(
        id_generator=SimpleNamespace(new_id=lambda: f"download-save-{next(ids)}"),
        clock=SimpleNamespace(now=lambda: datetime.now(UTC)),
        backend=CallbackThreadBackend(lambda _run_id, target: queued.append(target)),
    )
    download = build_paratranz_sync_features(SimpleNamespace(), on_result=lambda _run, result: outcomes.append(result))[
        1
    ]
    return SimpleNamespace(
        services=services,
        context=context,
        entry=entry,
        remote=remote,
        gateway=gateway,
        membership=membership,
        outcomes=outcomes,
        queued=queued,
        tasks=tasks,
        download=download,
        owner=OwnerRef("gui", "gui.operation-plan"),
    )


def test_authoritative_download_saves_current_version_and_readback_is_clean(authority_download_case):
    case = authority_download_case
    before = case.services.project_lifecycle.active.variant.snapshot()
    disk_before = VariantSnapshot.from_dto(case.services.variants.read_snapshot(before.ref), before.ref)
    assert case.services.project_lifecycle.active.dirty
    assert (
        next(item for item in disk_before.entries if item.entry_key == case.entry.identity).translation
        != "尚未保存的修改"
    )
    ref = _submit(case)
    case.queued.pop(0)()
    active = case.services.project_lifecycle.active
    assert case.tasks.get(ref, case.owner).state is JobState.COMPLETED
    assert not active.dirty
    assert (
        VariantSnapshot.from_dto(case.services.variants.read_snapshot(before.ref), before.ref)
        == active.variant.snapshot()
    )
    assert next(iter(case.context.collection)).translation == "云端译文"
    snapshots = case.services.project_snapshots.list(case.context.runtime_context)
    assert len(snapshots) == 1
    restored = ProjectSnapshotRepository(case.services.root, case.services.filesystem).load(
        snapshots[0].identity, before.ref
    )
    assert (
        next(item for item in restored.entries if item.entry_key == case.entry.identity).translation == "尚未保存的修改"
    )
    assert case.membership == [7]
    assert case.gateway.reads == [("inventory", 42), ("translation", 42, 11)]


def test_authoritative_save_failure_retries_same_snapshot_and_choices_without_redownload(
    authority_download_case, monkeypatch, caplog
):
    case = authority_download_case
    case.remote.append(replace(case.remote[0], remote_id=100, translation="选择的云端译文"))
    draft = case.download.create_draft(case.context, False, {})
    checked = case.download.mapper.preflight(draft)
    row = checked.sync_review[0]
    selected = next(candidate for candidate in row.candidates if candidate.translation == "选择的云端译文")
    fields = {field.field_id: field.value for field in draft.editable_fields}
    fields["download_decisions"] = json.dumps({row.row_id: selected.candidate_id})
    selected_draft = case.download.edit_draft(draft, tuple(fields.items()))
    case.download.discard_draft(draft)
    checked = case.download.mapper.preflight(selected_draft)
    assert checked.ready
    active = case.services.project_lifecycle.active
    before, disk_before = active.variant.snapshot(), case.services.variants.read_snapshot(active.formal_variant_ref)
    snapshots, fail_save = [], [False]
    original_snapshot = case.context.project_commands.save_snapshot
    original_write = case.services.filesystem.write_bytes

    def save_snapshot(name, context):
        result = original_snapshot(name, context)
        if result.is_success:
            snapshots.append(name)
            fail_save[0] = True
        return result

    def write(path, data):
        if fail_save[0]:
            raise OSError("自动保存磁盘写入失败")
        return original_write(path, data)

    monkeypatch.setattr(case.context.project_commands, "save_snapshot", save_snapshot)
    monkeypatch.setattr(case.services.filesystem, "write_bytes", write)
    ref = case.download.submit(selected_draft, checked, case.owner, case.tasks)
    case.queued.pop(0)()
    assert case.tasks.get(ref, case.owner).state is JobState.FAILED
    assert dict(case.tasks.get(ref, case.owner).progress)["operation_outcome"] == "failed"
    assert case.services.project_lifecycle.active.variant.snapshot() == before
    assert case.services.variants.read_snapshot(before.ref) == disk_before
    assert next(iter(case.context.collection)).translation == "尚未保存的修改"
    assert case.services.project_lifecycle.active.dirty
    assert len(snapshots) == 1
    assert "自动保存磁盘写入失败" in " ".join(item.message for item in case.outcomes[-1].diagnostics)
    assert "cannot update progress for a terminal task" not in caplog.text
    assert '"phase": "result_publication"' not in caplog.text

    fail_save[0] = False
    retry = case.download.edit_draft(selected_draft, tuple(fields.items()))
    case.download.discard_draft(selected_draft)
    assert retry.request.download_session is selected_draft.request.download_session
    assert retry.request.download_decisions == ((row.row_id, selected.candidate_id),)
    checked = case.download.mapper.preflight(retry)
    assert checked.ready
    ref = case.download.submit(retry, checked, case.owner, case.tasks)
    case.queued.pop(0)()
    active = case.services.project_lifecycle.active
    assert case.tasks.get(ref, case.owner).state is JobState.COMPLETED
    assert not active.dirty
    assert next(iter(case.context.collection)).translation == "选择的云端译文"
    assert (
        VariantSnapshot.from_dto(case.services.variants.read_snapshot(before.ref), before.ref)
        == active.variant.snapshot()
    )
    assert len(snapshots) == len(case.services.project_snapshots.list(case.context.runtime_context)) == 1
    assert case.membership == [7]
    assert case.gateway.reads == [("inventory", 42), ("translation", 42, 11)]


def test_authoritative_save_completes_task_before_late_cancel_in_real_background_thread(
    authority_download_case, monkeypatch
):
    case = authority_download_case
    committed, release_cleanup = Event(), Event()
    ids = count()
    backend = ThreadBackend()
    tasks = TaskRuntime(
        id_generator=SimpleNamespace(new_id=lambda: f"download-save-thread-{next(ids)}"),
        clock=SimpleNamespace(now=lambda: datetime.now(UTC)),
        backend=backend,
    )
    draft = case.download.create_draft(case.context, False, {})
    checked = case.download.mapper.preflight(draft)
    assert checked.ready
    outcome_count = len(case.outcomes)

    def cleanup():
        committed.set()
        if not release_cleanup.wait(5):
            raise TimeoutError("保存后取消测试未释放清理阶段")

    monkeypatch.setattr(case.gateway.service, "close", cleanup)
    ref = None
    try:
        ref = case.download.submit(draft, checked, case.owner, tasks)
        assert committed.wait(5)
        active = case.services.project_lifecycle.active
        assert not active.dirty
        assert (
            VariantSnapshot.from_dto(case.services.variants.read_snapshot(active.formal_variant_ref))
            == active.variant.snapshot()
        )
        assert tasks.get(ref, case.owner).state is JobState.COMPLETED
        assert dict(tasks.get(ref, case.owner).progress)["operation_outcome"] == "completed"
        assert len(case.outcomes) == outcome_count  # The workload is still paused before publishing its receipt.
        with pytest.raises(TransitionError, match="completed"):
            tasks.cancel(ref, case.owner)
        release_cleanup.set()
        assert backend.join(ref.run_id, 5)
        assert tasks.get(ref, case.owner).state is JobState.COMPLETED
        assert case.outcomes[-1].is_success
        assert case.outcomes[-1].counts.succeeded == 1
        assert not case.outcomes[-1].counts.cancelled
        assert next(iter(case.context.collection)).translation == "云端译文"
    finally:
        release_cleanup.set()
        if ref is not None:
            backend.join(ref.run_id, 5)
        backend.close(5)


@pytest.mark.parametrize("project", [2], indirect=True)
def test_authoritative_download_with_deferred_translation_keeps_final_receipt_progress(authority_download_case):
    case = authority_download_case
    first, deferred = tuple(case.context.collection)
    case.remote.extend((
        ParaTranzEntry(100, deferred.key, deferred.original, "候选甲", deferred.context, stage=3),
        ParaTranzEntry(101, deferred.key, deferred.original, "候选乙", deferred.context, stage=3),
    ))
    ref = _submit(case)
    case.queued.pop(0)()
    snapshot = case.tasks.get(ref, case.owner)
    receipt = case.outcomes[-1]
    assert snapshot.state is JobState.COMPLETED
    assert receipt.is_success
    assert dict(snapshot.progress)["operation_outcome"] == receipt.outcome.value == "completed"
    assert receipt.value["deferred_count"] == 1
    assert receipt.counts.succeeded == receipt.counts.skipped == 1
    assert case.context.collection.get(first.identity).translation == "云端译文"
    assert case.context.collection.get(deferred.identity) == deferred
    assert not case.services.project_lifecycle.active.dirty


@pytest.mark.parametrize("fail_save", [False, True], ids=["saved-receipt", "failed-save-retry"])
def test_real_production_window_waits_for_saved_receipt_after_early_terminal_event(
    authority_download_case, monkeypatch, fail_save
):
    case = authority_download_case
    committed, release_cleanup = Event(), Event()
    ids = count()
    backend = ThreadBackend()
    tasks = TaskRuntime(
        id_generator=SimpleNamespace(new_id=lambda: f"download-ui-thread-{next(ids)}"),
        clock=SimpleNamespace(now=lambda: datetime.now(UTC)),
        backend=backend,
    )
    facade = build_operation_plan_facade(
        SimpleNamespace(tasks=tasks),
        case.context.runtime_context,
        dialog_factory=lambda plan, parent: ParaTranzSyncDialog(plan, case.context, parent),
    )
    dialog = facade.begin_download(case.context)
    owner = OwnerRef(
        case.context.runtime_context.owner_id, "gui.operation-plan", project_id=case.context.active_project_id
    )
    write_failure = [False]
    original_write = case.services.filesystem.write_bytes
    original_snapshot = case.context.project_commands.save_snapshot

    def save_snapshot(name, context):
        result = original_snapshot(name, context)
        write_failure[0] = fail_save and result.is_success
        return result

    def write(path, data):
        if write_failure[0]:
            raise OSError("真实窗口自动保存写盘失败")
        return original_write(path, data)

    monkeypatch.setattr(case.services.filesystem, "write_bytes", write)
    monkeypatch.setattr(case.context.project_commands, "save_snapshot", save_snapshot)

    def wait_for(predicate):
        for _ in range(1000):
            QTest.qWait(5)
            if predicate():
                return
        pytest.fail("真实下载窗口未完成预期的后台阶段")

    def cleanup():
        committed.set()
        if not release_cleanup.wait(5):
            raise TimeoutError("保存后回执测试未释放清理阶段")

    try:
        dialog._confirm.click()
        wait_for(lambda: not dialog._preflight_running)
        assert dialog._check_ready
        monkeypatch.setattr(case.gateway.service, "close", cleanup)
        dialog._confirm.click()
        assert committed.wait(5)
        snapshot = tasks.list(owner)[-1]
        assert snapshot.state is (JobState.FAILED if fail_save else JobState.COMPLETED)
        assert dict(snapshot.specification.metadata)["await_result_receipt"] == "true"
        QTest.qWait(30)
        assert dialog._execution_running
        assert not dialog._cancel.isEnabled()
        assert "已自动保存" not in dialog._status.text()
        if not fail_save:
            with pytest.raises(TransitionError, match="completed"):
                tasks.cancel(snapshot.ref, owner)
        release_cleanup.set()
        assert backend.join(snapshot.ref.run_id, 5)
        wait_for(lambda: not dialog._execution_running)
        assert "未取得执行结果" not in dialog._status.text()
        assert dialog._cancel.isEnabled()
        result = facade.read_task_result(snapshot.ref, owner)
        if fail_save:
            assert not result.is_success
            assert tasks.get(snapshot.ref, owner).state is JobState.FAILED
            assert "真实窗口自动保存写盘失败" in dialog._status.text()
            assert "已自动保存" not in dialog._status.text()
            assert dialog._confirm.isEnabled()
            write_failure[0] = False
            dialog._confirm.click()
            wait_for(lambda: not dialog._preflight_running)
            assert dialog._check_ready
            dialog._confirm.click()
            wait_for(lambda: not dialog._execution_running)
            assert case.membership == [7]
            assert case.gateway.reads == [("inventory", 42), ("translation", 42, 11)]
            assert len(case.services.project_snapshots.list(case.context.runtime_context)) == 1
        else:
            assert result.is_success
            assert tasks.get(snapshot.ref, owner).state is JobState.COMPLETED
        assert "已自动保存到当前本地版本" in dialog._status.text()
        assert "成功 1 · 失败 0" in dialog._status.text()
        active = case.services.project_lifecycle.active
        assert not active.dirty
        assert (
            VariantSnapshot.from_dto(case.services.variants.read_snapshot(active.formal_variant_ref))
            == active.variant.snapshot()
        )
    finally:
        release_cleanup.set()
        backend.close(5)
        dialog.set_execution_running(False)
        dialog.close()
        _APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
