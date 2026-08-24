"""Qt composition for the shared operation-plan presenter and dialog."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from PyQt6.QtWidgets import QMessageBox

from transbridge.ui.workers import ApiWorker

from .plan_dialog import OperationPlanDialog
from .plan_presenter import OperationPlanError, OperationPlanPresenter
from .plan_view import OperationKind

OperationDraftFactory = Callable[[object, bool, Mapping[str, object]], object]
OperationEditFactory = Callable[[object, tuple[tuple[str, str], ...]], object]
OperationDiscardFactory = Callable[[object], None]


class OperationPlanCoordinator:
    """Stable public intent facade used by menus, cards, and the FOMOD panel."""

    def __init__(
        self,
        presenter: OperationPlanPresenter,
        draft_factories: Mapping[OperationKind, OperationDraftFactory],
        *,
        owner_id: Callable[[object], str],
        edit_factories: Mapping[OperationKind, OperationEditFactory] | None = None,
        discard_factories: Mapping[OperationKind, OperationDiscardFactory] | None = None,
        preflight_worker_factory=ApiWorker,
        dialog_factory=OperationPlanDialog,
    ) -> None:
        self._presenter = presenter
        self._draft_factories = dict(draft_factories)
        self._owner_id = owner_id
        self._edit_factories = dict(edit_factories or {})
        self._discard_factories = dict(discard_factories or {})
        self._preflight_worker_factory = preflight_worker_factory
        self._dialog_factory = dialog_factory
        self._owned_windows: dict[str, object] = {}
        self._preflight_workers: dict[str, object] = {}

    @property
    def active_window_count(self) -> int:
        return len(self._owned_windows)

    def begin_upload(self, context, *, batch: bool = False, **values):
        return self._begin(OperationKind.UPLOAD, context, batch=batch, values=values)

    def begin_download(self, context, *, batch: bool = False, **values):
        return self._begin(OperationKind.DOWNLOAD, context, batch=batch, values=values)

    def begin_write(self, context, *, batch: bool = False, **values):
        return self._begin(OperationKind.WRITE, context, batch=batch, values=values)

    def begin_fomod(self, context, *, batch: bool = False, parent=None, **values):
        return self._begin(OperationKind.FOMOD, context, batch=batch, values=values, parent=parent)

    def _begin(self, kind, context, *, batch, values, parent=None):
        factory = self._draft_factories.get(kind)
        if factory is None:
            raise OperationPlanError("DRAFT_FACTORY_UNAVAILABLE", f"no draft factory is registered for {kind.value}")
        owner_id = self._owner_id(context)
        draft_holder = [factory(context, batch, values)]
        plan = self._presenter.open(kind, draft_holder[0], owner_id=owner_id)
        dialog = self._dialog_factory(plan, parent)
        self._owned_windows[plan.session_id] = dialog
        released = False

        def release() -> None:
            self._owned_windows.pop(plan.session_id, None)

        def apply_edits(fields) -> None:
            edit = self._edit_factories.get(kind)
            if edit is None or not fields:
                return
            previous = draft_holder[0]
            draft_holder[0] = edit(previous, tuple(fields))
            if draft_holder[0] is not previous:
                self._discard_factories.get(kind, lambda _draft: None)(previous)
            edited = self._presenter.edit(plan.session_id, draft_holder[0], owner_id=owner_id)
            dialog.render_plan(edited)

        def preflight(_session_id, fields) -> None:
            try:
                apply_edits(fields)
            except (RuntimeError, TypeError, ValueError) as exc:
                QMessageBox.warning(dialog, "预检失败", str(exc))
                return
            if kind not in {OperationKind.UPLOAD, OperationKind.DOWNLOAD}:
                try:
                    dialog.render_preflight(self._presenter.preflight(plan.session_id, owner_id=owner_id))
                except (RuntimeError, TypeError, ValueError) as exc:
                    QMessageBox.warning(dialog, "预检失败", str(exc))
                return
            if plan.session_id in self._preflight_workers:
                QMessageBox.information(dialog, "正在预检", "远端预检仍在进行，请稍候。")
                return

            worker = self._preflight_worker_factory(
                lambda: self._presenter.preflight(plan.session_id, owner_id=owner_id)
            )
            self._preflight_workers[plan.session_id] = worker

            def on_result(result) -> None:
                if self._owned_windows.get(plan.session_id) is dialog:
                    dialog.render_preflight(result)

            def on_error(message) -> None:
                if self._owned_windows.get(plan.session_id) is dialog:
                    QMessageBox.warning(dialog, "预检失败", str(message))

            def on_finished() -> None:
                self._preflight_workers.pop(plan.session_id, None)
                if self._owned_windows.get(plan.session_id) is not dialog:
                    self._discard_factories.get(kind, lambda _draft: None)(draft_holder[0])
                delete_later = getattr(worker, "deleteLater", None)
                if callable(delete_later):
                    delete_later()

            worker.result.connect(on_result)
            worker.error.connect(on_error)
            worker.finished.connect(on_finished)
            worker.start()

        def return_to_edit(_session_id, fields) -> None:
            try:
                apply_edits(fields)
            except (RuntimeError, TypeError, ValueError) as exc:
                QMessageBox.warning(dialog, "无法返回编辑", str(exc))

        def confirm(_session_id, token) -> None:
            try:
                self._presenter.confirm(plan.session_id, token, owner_id=owner_id)
            except (RuntimeError, TypeError, ValueError) as exc:
                QMessageBox.warning(dialog, "无法开始操作", str(exc))
                return
            dialog.accept()
            release()

        def cancel() -> None:
            nonlocal released
            if released:
                return
            released = True
            try:
                self._presenter.cancel(plan.session_id, owner_id=owner_id)
            except OperationPlanError:
                pass
            self._discard_factories.get(kind, lambda _draft: None)(draft_holder[0])
            release()

        def destroyed(*_args) -> None:
            # Parent teardown may destroy a non-modal plan without emitting
            # rejected; still discard the presenter session without effects.
            cancel()

        dialog.preflight_requested.connect(preflight)
        dialog.return_to_edit_requested.connect(return_to_edit)
        dialog.confirm_requested.connect(confirm)
        dialog.rejected.connect(cancel)
        destroyed_signal = getattr(dialog, "destroyed", None)
        if destroyed_signal is not None and hasattr(destroyed_signal, "connect"):
            destroyed_signal.connect(destroyed)
        set_modal = getattr(dialog, "setModal", None)
        if callable(set_modal):
            set_modal(False)
        dialog.show()
        raise_window = getattr(dialog, "raise_", None)
        if callable(raise_window):
            raise_window()
        activate = getattr(dialog, "activateWindow", None)
        if callable(activate):
            activate()
        return dialog
