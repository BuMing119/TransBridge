from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.conftest import make_test_collection
from transbridge.application.contracts import OperationResult
from transbridge.application.ports.paratranz import ParaTranzEntry
from transbridge.application.tasks import CallbackThreadBackend, JobState, OwnerRef, TaskRuntime
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.paratranz.service import ParaTranzService
from transbridge.persistence.variant_store import VariantStore
from transbridge.ui.operations.paratranz_sync import build_paratranz_sync_features
from transbridge.ui.operations.plan_view import OperationKind


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

        monkeypatch.setattr(case.context.variant_store, "save_snapshot", fail_snapshot)
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
