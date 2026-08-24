from transbridge.ui.operations.coordinator import OperationPlanCoordinator
from transbridge.ui.operations.mappers import OperationPlanDraft, UploadOperationMapper
from transbridge.ui.operations.plan_presenter import OperationPlanPresenter
from transbridge.ui.operations.plan_view import OperationKind


class _Signal:
    def __init__(self) -> None:
        self.callbacks = []

    def connect(self, callback) -> None:
        self.callbacks.append(callback)

    def emit(self, *args) -> None:
        for callback in tuple(self.callbacks):
            callback(*args)


class _DeferredWorker:
    def __init__(self, fn) -> None:
        self.fn = fn
        self.result = _Signal()
        self.error = _Signal()
        self.finished = _Signal()
        self.started = False
        self.deleted = False

    def start(self) -> None:
        self.started = True

    def complete(self) -> None:
        try:
            self.result.emit(self.fn())
        except Exception as exc:  # pragma: no cover - exercised through the error signal contract
            self.error.emit(str(exc))
        finally:
            self.finished.emit()

    def deleteLater(self) -> None:
        self.deleted = True


class _Dialog:
    def __init__(self, plan, _parent=None) -> None:
        self.plan = plan
        self.preflight = None
        self.preflight_requested = _Signal()
        self.return_to_edit_requested = _Signal()
        self.confirm_requested = _Signal()
        self.rejected = _Signal()
        self.destroyed = _Signal()

    def render_plan(self, plan) -> None:
        self.plan = plan

    def render_preflight(self, result) -> None:
        self.preflight = result

    def setModal(self, _value) -> None:  # noqa: N802 - Qt compatibility surface
        pass

    def show(self) -> None:
        pass


class _Submitter:
    def submit(self, *_args):
        return object()


def _draft() -> OperationPlanDraft:
    return OperationPlanDraft(
        request=object(),
        target="ParaTranz #42",
        target_revision="binding:7",
        input_fingerprint="local:1",
        scope_summary="1 个对象",
        mode_summary="上传",
        conflict_summary="冲突停止",
        backup_summary="远端快照",
        estimated_impact=(("objects", 1),),
    )


def test_remote_preflight_is_deferred_and_cancelled_dialog_ignores_late_result() -> None:
    workers = []
    discarded = []

    def worker_factory(fn):
        worker = _DeferredWorker(fn)
        workers.append(worker)
        return worker

    mapper = UploadOperationMapper()
    presenter = OperationPlanPresenter((mapper,), _Submitter())
    coordinator = OperationPlanCoordinator(
        presenter,
        {OperationKind.UPLOAD: lambda _context, _batch, _values: _draft()},
        owner_id=lambda _context: "gui",
        discard_factories={OperationKind.UPLOAD: discarded.append},
        preflight_worker_factory=worker_factory,
        dialog_factory=_Dialog,
    )
    dialog = coordinator.begin_upload(object())

    dialog.preflight_requested.emit(dialog.plan.session_id, ())

    assert workers[0].started
    assert dialog.preflight is None
    dialog.rejected.emit()
    workers[0].complete()

    assert dialog.preflight is None
    assert workers[0].deleted
    assert discarded
