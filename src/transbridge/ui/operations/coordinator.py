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
OperationContextDialogFactory = Callable[[object, object, object | None], object]


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
        dialog_factories: Mapping[OperationKind, OperationContextDialogFactory] | None = None,
        execution_observer: Callable[[object, str, object], None] | None = None,
    ) -> None:
        self._presenter = presenter
        self._draft_factories = dict(draft_factories)
        self._owner_id = owner_id
        self._edit_factories = dict(edit_factories or {})
        self._discard_factories = dict(discard_factories or {})
        self._preflight_worker_factory = preflight_worker_factory
        self._dialog_factory = dialog_factory
        self._dialog_factories = dict(dialog_factories or {})
        self._execution_observer = execution_observer
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
        context_dialog_factory = self._dialog_factories.get(kind)
        dialog = (
            self._dialog_factory(plan, parent)
            if context_dialog_factory is None
            else context_dialog_factory(plan, context, parent)
        )
        self._owned_windows[plan.session_id] = dialog
        released = False
        preflight_generation = 0
        pending_generation: int | None = None
        in_flight_draft: object | None = None
        retired_drafts: list[object] = []

        def release() -> None:
            self._owned_windows.pop(plan.session_id, None)

        def show_error(title: str, message: str) -> None:
            renderer = getattr(dialog, "render_preflight_error", None)
            if callable(renderer):
                renderer(message)
            else:
                QMessageBox.warning(dialog, title, message)

        def apply_edits(fields) -> None:
            edit = self._edit_factories.get(kind)
            if edit is None or not fields:
                return
            previous = draft_holder[0]
            draft_holder[0] = edit(previous, tuple(fields))
            if draft_holder[0] is not previous:
                if previous is in_flight_draft:
                    retired_drafts.append(previous)
                else:
                    self._discard_factories.get(kind, lambda _draft: None)(previous)
            edited = self._presenter.edit(plan.session_id, draft_holder[0], owner_id=owner_id)
            dialog.render_plan(edited)

        def preflight(_session_id, fields) -> None:
            nonlocal preflight_generation, pending_generation
            try:
                apply_edits(fields)
            except (RuntimeError, TypeError, ValueError) as exc:
                show_error("预检失败", str(exc))
                return
            if kind not in {OperationKind.UPLOAD, OperationKind.DOWNLOAD}:
                try:
                    dialog.render_preflight(self._presenter.preflight(plan.session_id, owner_id=owner_id))
                except (RuntimeError, TypeError, ValueError) as exc:
                    show_error("预检失败", str(exc))
                return
            preflight_generation += 1
            generation = preflight_generation
            if plan.session_id in self._preflight_workers:
                pending_generation = generation
                running = getattr(dialog, "set_preflight_running", None)
                if callable(running):
                    running(True)
                return
            start_remote_preflight(generation)

        def start_remote_preflight(generation: int) -> None:
            nonlocal in_flight_draft, pending_generation
            pending_generation = None
            captured_draft = draft_holder[0]
            in_flight_draft = captured_draft
            running = getattr(dialog, "set_preflight_running", None)
            if callable(running):
                running(True)
            worker = self._preflight_worker_factory(
                lambda: self._presenter.preflight(plan.session_id, owner_id=owner_id)
            )
            self._preflight_workers[plan.session_id] = worker

            def on_result(result) -> None:
                if self._owned_windows.get(plan.session_id) is dialog and generation == preflight_generation:
                    dialog.render_preflight(result)

            def on_error(message) -> None:
                if self._owned_windows.get(plan.session_id) is dialog and generation == preflight_generation:
                    show_error("预检失败", str(message))

            def on_finished() -> None:
                nonlocal in_flight_draft
                self._preflight_workers.pop(plan.session_id, None)
                in_flight_draft = None
                discard = self._discard_factories.get(kind, lambda _draft: None)
                pending_discards = list(retired_drafts)
                retired_drafts.clear()
                if self._owned_windows.get(plan.session_id) is not dialog:
                    pending_discards.append(captured_draft)
                elif pending_generation is not None:
                    start_remote_preflight(pending_generation)
                elif callable(running):
                    running(False)
                unique_discards: list[object] = []
                for retired in pending_discards:
                    if not any(retired is existing for existing in unique_discards):
                        unique_discards.append(retired)
                for retired in unique_discards:
                    discard(retired)
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
                show_error("无法返回编辑", str(exc))

        def confirm(_session_id, token) -> None:
            running = getattr(dialog, "set_execution_running", None)
            retain_session = callable(running) and self._execution_observer is not None
            if retain_session:
                running(True)
            try:
                ref = self._presenter.confirm(plan.session_id, token, owner_id=owner_id, retain_session=retain_session)
            except (RuntimeError, TypeError, ValueError) as exc:
                if retain_session:
                    running(False)
                show_error("无法开始操作", str(exc))
                if kind in {OperationKind.UPLOAD, OperationKind.DOWNLOAD}:
                    preflight(plan.session_id, dialog.edited_values())
                return
            if retain_session:
                self._execution_observer(ref, owner_id, dialog)
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
            if draft_holder[0] is in_flight_draft:
                retired_drafts.append(draft_holder[0])
            else:
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
