from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from tests.conftest import make_test_collection
from transbridge.application.contracts import OperationResult
from transbridge.application.ports.paratranz import ParaTranzEntry
from transbridge.application.tasks import CallbackThreadBackend, JobState, OwnerRef, TaskRuntime
from transbridge.paratranz.service import ParaTranzService
from transbridge.ui.operations.paratranz_sync import build_paratranz_sync_features
from transbridge.ui.operations.plan_view import OperationKind


class _Ids:
    def new_id(self) -> str:
        return "async-download-run"


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 30, tzinfo=UTC)

    def now(self):
        self.value += timedelta(microseconds=1)
        return self.value


class _Service:
    def __init__(self) -> None:
        self.remote_reads = 0
        self.closed = False

    def list_projects(self, *, uid):
        assert uid == 5
        return (SimpleNamespace(project_id=42, name="云端汉化项目"),)

    def list_entries(self, project_id, *, limit, cancellation=None):
        del limit, cancellation
        assert project_id == 42
        self.remote_reads += 1
        return ()

    def close(self) -> None:
        self.closed = True


def test_download_confirmation_schedules_remote_revalidation_instead_of_blocking_caller(monkeypatch) -> None:
    service = _Service()
    monkeypatch.setattr(ParaTranzService, "from_config", classmethod(lambda _cls, _config: service))
    context = SimpleNamespace(
        collection=make_test_collection(3),
        paratranz_binding={
            "project_id": 42,
            "project_name": "云端汉化项目",
            "endpoint": "https://paratranz.cn",
            "account_user_id": 5,
        },
        project_revision=7,
        config=SimpleNamespace(
            token="configured",
            base_url="https://paratranz.cn",
            user_id=5,
            config_revision=1,
        ),
        current_user={"id": 5},
        active_project_id="local-project",
        project_name="本地工程",
        active_variant="正式版",
        dirty=False,
    )
    download = next(
        feature
        for feature in build_paratranz_sync_features(SimpleNamespace())
        if feature.kind is OperationKind.DOWNLOAD
    )
    draft = download.create_draft(context, False, {})
    preflight = download.mapper.preflight(draft)
    assert preflight.ready
    assert service.remote_reads == 1

    queued = []
    tasks = TaskRuntime(
        id_generator=_Ids(),
        clock=_Clock(),
        backend=CallbackThreadBackend(lambda _run_id, target: queued.append(target)),
    )
    owner = OwnerRef("gui", "gui.operation-plan")

    ref = download.submit(draft, preflight, owner, tasks)

    assert len(queued) == 1
    assert service.remote_reads == 1
    assert tasks.get(ref, owner).state is JobState.RUNNING

    queued[0]()
    assert service.remote_reads == 3
    assert tasks.get(ref, owner).state is JobState.COMPLETED
    assert service.closed


def test_set_default_refreshes_project_revision_before_local_download_commit(monkeypatch) -> None:
    class ChangedService(_Service):
        def list_entries(self, project_id, *, limit, cancellation=None):
            del limit, cancellation
            assert project_id == 42
            self.remote_reads += 1
            return (ParaTranzEntry(7, "entry_000", "Original text 0", "远端译文", "INFO:NAM1", 1),)

    service = ChangedService()
    monkeypatch.setattr(ParaTranzService, "from_config", classmethod(lambda _cls, _config: service))
    committed_revisions = []
    monkeypatch.setattr(
        "transbridge.ui.operations.paratranz_sync.replace_local_snapshots",
        lambda _context, _values, _project_id, **expected: committed_revisions.append(expected["project_revision"]),
    )
    context = SimpleNamespace(
        collection=make_test_collection(1),
        paratranz_binding=None,
        project_revision=7,
        variant_revision=3,
        active_version_identity=("local-project", "variant"),
        config=SimpleNamespace(
            token="configured",
            base_url="https://paratranz.cn",
            user_id=5,
            config_revision=1,
        ),
        current_user={"id": 5},
        active_project_id="local-project",
        project_name="本地工程",
        active_variant="正式版",
        dirty=False,
    )

    def set_binding(_binding):
        context.project_revision = 8
        return OperationResult.completed({"project_revision": 8})

    context.set_paratranz_binding = set_binding
    download = next(
        feature
        for feature in build_paratranz_sync_features(SimpleNamespace())
        if feature.kind is OperationKind.DOWNLOAD
    )
    draft = download.create_draft(
        context,
        False,
        {"paratranz_project_id": "42", "paratranz_project_name": "云端汉化项目", "set_as_default": True},
    )
    preflight = download.mapper.preflight(draft)
    assert preflight.ready
    queued = []
    tasks = TaskRuntime(
        id_generator=_Ids(),
        clock=_Clock(),
        backend=CallbackThreadBackend(lambda _run_id, target: queued.append(target)),
    )

    ref = download.submit(draft, preflight, OwnerRef("gui", "gui.operation-plan"), tasks)
    queued[0]()

    assert tasks.get(ref, OwnerRef("gui", "gui.operation-plan")).state is JobState.COMPLETED
    assert committed_revisions == [8]
