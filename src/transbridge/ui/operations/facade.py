"""Production composition facade for all destructive operation plans.

The facade is the one public UI port wired into ``MainWindow``.  Feature
adapters retain their application request and use-case ownership; this module
only composes the shared plan/preflight/confirmation presentation with the
process ``TaskRuntime`` identity.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
from threading import RLock
from typing import Protocol

from transbridge.application.contracts import JobRef, OperationResult, RequestContext
from transbridge.application.tasks import OwnerRef, TaskRuntime
from transbridge.bootstrap.runtime import AppRuntime
from transbridge.ui.presentation.task_projection import TaskProjectionBinding

from .coordinator import OperationPlanCoordinator
from .plan_dialog import OperationPlanDialog
from .plan_presenter import OperationPlanMapper, OperationPlanPresenter
from .plan_view import OperationKind
from .preflight_view import OperationPreflightResult


class OperationPlanFeature(Protocol):
    """Feature-owned bridge behind the common operation presentation."""

    kind: OperationKind
    mapper: OperationPlanMapper

    def supports(self, context: object, batch: bool) -> bool: ...

    def create_draft(self, context: object, batch: bool, values: dict[str, object]) -> object: ...

    def edit_draft(self, draft: object, fields: tuple[tuple[str, str], ...]) -> object: ...

    def discard_draft(self, draft: object) -> None: ...

    def submit(
        self,
        draft: object,
        preflight: OperationPreflightResult,
        owner: OwnerRef,
        runtime: TaskRuntime,
    ) -> object: ...


@dataclass(frozen=True, slots=True)
class OperationFeatureAdapter:
    """Concrete adapter for an existing application use case.

    ``submit_task`` must submit to the supplied ``TaskRuntime`` (or an
    entrypoint already constructed from that exact runtime).  It must never
    perform the operation synchronously on the GUI thread.
    """

    kind: OperationKind
    mapper: OperationPlanMapper
    draft_factory: Callable[[object, bool, dict[str, object]], object]
    task_submitter: Callable[[object, OperationPreflightResult, OwnerRef, TaskRuntime], object]
    draft_editor: Callable[[object, tuple[tuple[str, str], ...]], object] = lambda draft, _fields: draft
    capability: Callable[[object, bool], bool] = lambda _context, _batch: True
    draft_discarder: Callable[[object], None] = lambda _draft: None

    def __post_init__(self) -> None:
        if self.mapper.kind is not self.kind:
            raise ValueError("operation feature mapper kind does not match its feature kind")

    def create_draft(self, context: object, batch: bool, values: dict[str, object]) -> object:
        return self.draft_factory(context, batch, values)

    def supports(self, context: object, batch: bool) -> bool:
        return bool(self.capability(context, batch))

    def edit_draft(self, draft: object, fields: tuple[tuple[str, str], ...]) -> object:
        return self.draft_editor(draft, fields)

    def discard_draft(self, draft: object) -> None:
        self.draft_discarder(draft)

    def submit(
        self,
        draft: object,
        preflight: OperationPreflightResult,
        owner: OwnerRef,
        runtime: TaskRuntime,
    ) -> object:
        return self.task_submitter(draft, preflight, owner, runtime)


RuntimeContextFactory = Callable[[object], RequestContext]


class OperationPlanFacade:
    """Real upload/download/write/FOMOD intent facade.

    Construction requires all four feature adapters. Current-project upload
    and download use their feature windows; other operations and explicit
    custom plans use shared preflight and request-bound confirmation before
    calling the feature task port.
    """

    def __init__(
        self,
        runtime: AppRuntime,
        runtime_context: RuntimeContextFactory,
        features: tuple[OperationPlanFeature, ...],
        *,
        dialog_factory=OperationPlanDialog,
        dialog_factories: Mapping[OperationKind, Callable[[object, object, object | None], object]] | None = None,
        execution_result_reader: Callable[[JobRef, OwnerRef], OperationResult | None] | None = None,
        upload_launcher: Callable | None = None,
        download_launcher: Callable | None = None,
    ) -> None:
        self._runtime = runtime
        self._runtime_context = runtime_context
        self._execution_result_reader = execution_result_reader
        self._upload_launcher = upload_launcher
        self._upload_windows = {}
        self._download_launcher = download_launcher
        self._download_windows = {}
        self._pending_results: dict[str, Callable[[], None]] = {}
        self._features = {feature.kind: feature for feature in features}
        required = frozenset(OperationKind)
        registered = frozenset(self._features)
        if registered != required or len(features) != len(required):
            missing = ", ".join(sorted(item.value for item in required - registered)) or "none"
            extra = ", ".join(sorted(item.value for item in registered - required)) or "none"
            raise ValueError(f"operation facade requires one adapter per kind (missing={missing}; extra={extra})")
        self._owners: dict[str, OwnerRef] = {}
        self._owner_order: list[str] = []
        self._lock = RLock()
        presenter = OperationPlanPresenter(tuple(feature.mapper for feature in features), self)
        self._coordinator = OperationPlanCoordinator(
            presenter,
            {kind: feature.create_draft for kind, feature in self._features.items()},
            owner_id=self._bind_owner,
            edit_factories={kind: feature.edit_draft for kind, feature in self._features.items()},
            discard_factories={kind: feature.discard_draft for kind, feature in self._features.items()},
            dialog_factory=dialog_factory,
            dialog_factories=dialog_factories,
            execution_observer=self._observe_execution,
        )

    @property
    def tasks(self) -> TaskRuntime:
        """Expose the exact process runtime used by every feature adapter."""
        return self._runtime.tasks

    @property
    def active_plan_count(self) -> int:
        return self._coordinator.active_window_count + len(self._upload_windows) + len(self._download_windows)

    def begin_upload(self, context, *, batch: bool = False, **values):
        if self._upload_launcher is not None and not values:
            window = self._upload_launcher(context, batch=batch)
            if window is not None:
                key = id(window)
                self._upload_windows[key] = window
                window.destroyed.connect(lambda: self._upload_windows.pop(key, None))
            return window
        return self._coordinator.begin_upload(context, batch=batch, **values)

    def begin_download(self, context, *, batch: bool = False, **values):
        if (
            self._download_launcher is not None
            and not values
            and bool(getattr(context, "uses_authoritative_projection", False))
        ):
            window = self._download_launcher(context)
            if window is not None:
                key = id(window)
                self._download_windows[key] = window
                window.destroyed.connect(lambda: self._download_windows.pop(key, None))
            return window
        return self._coordinator.begin_download(context, batch=batch, **values)

    def begin_write(self, context, *, batch: bool = False, **values):
        return self._coordinator.begin_write(context, batch=batch, **values)

    def begin_fomod(self, context, *, batch: bool = False, parent=None, **values):
        return self._coordinator.begin_fomod(context, batch=batch, parent=parent, **values)

    def supports(self, kind: OperationKind | str, context: object, *, batch: bool = False) -> bool:
        """Return whether this facade can preserve parity for the requested scope."""
        if OperationKind(kind) is OperationKind.UPLOAD and self._upload_launcher is not None:
            return True
        if (
            OperationKind(kind) is OperationKind.DOWNLOAD
            and self._download_launcher is not None
            and bool(getattr(context, "uses_authoritative_projection", False))
        ):
            return True
        feature = self._features.get(OperationKind(kind))
        return feature is not None and feature.supports(context, batch)

    def read_task_result(self, ref: JobRef, actor: OwnerRef) -> OperationResult | None:
        """Read a retained receipt after its execution window has closed."""
        self._runtime.tasks.get(ref, actor)
        if self._execution_result_reader is None:
            return None
        return self._execution_result_reader(ref, actor)

    def submit(
        self,
        kind: OperationKind,
        draft: object,
        preflight: OperationPreflightResult,
        owner_id: str,
    ) -> object:
        feature = self._features[OperationKind(kind)]
        with self._lock:
            owner = self._owners.get(owner_id)
        if owner is None:
            raise PermissionError("operation owner scope expired before task submission")
        return feature.submit(draft, preflight, owner, self._runtime.tasks)

    def _bind_owner(self, ui_context: object) -> str:
        context = self._runtime_context(ui_context)
        owner = OwnerRef(
            owner_id=context.owner_id,
            entrypoint="gui.operation-plan",
            project_id=context.project_id,
            variant_id=context.variant_id,
            session_id=context.session_id,
            permissions=context.permissions,
        )
        scope_key = json.dumps(
            (
                owner.owner_id,
                owner.entrypoint,
                owner.project_id,
                owner.variant_id,
                owner.session_id,
                tuple(sorted(owner.permissions)),
            ),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with self._lock:
            self._owners[scope_key] = owner
            if scope_key in self._owner_order:
                self._owner_order.remove(scope_key)
            self._owner_order.append(scope_key)
            while len(self._owner_order) > 100:
                expired = self._owner_order.pop(0)
                self._owners.pop(expired, None)
        return scope_key

    def notify_result_ready(self, run_id: str) -> None:
        """Deliver a finalized receipt after a final mutation has won cancellation."""
        with self._lock:
            refresh = self._pending_results.pop(run_id, None)
        if refresh is not None:
            refresh()

    def _observe_execution(self, ref, owner_id: str, dialog) -> None:
        with self._lock:
            owner = self._owners[owner_id]
        run_id = ref.run_id or ref.job_id
        metadata = dict(self._runtime.tasks.get(ref, owner).specification.metadata)
        await_receipt = metadata.get("await_result_receipt") == "true"
        closed = False

        def on_change(activity) -> None:
            with self._lock:
                if closed or activity.run_id != run_id:
                    return
                result_signal = getattr(dialog, "execution_result_changed", None)
                if result_signal is not None:
                    result = None
                    if activity.is_terminal and self._execution_result_reader is not None:
                        result = self._execution_result_reader(ref, owner)
                        if await_receipt and result is None:
                            self._pending_results[run_id] = lambda: on_change(activity)
                            return
                    self._pending_results.pop(run_id, None)
                    result_signal.emit(activity, result)
                else:
                    dialog.execution_changed.emit(activity)

        binding = TaskProjectionBinding(self._runtime.tasks, owner, on_change)

        def close_binding(*_args):
            nonlocal closed
            with self._lock:
                closed = True
                self._pending_results.pop(run_id, None)
            binding.close()

        dialog.execution_finished.connect(close_binding)
        dialog.finished.connect(close_binding)
        dialog.destroyed.connect(close_binding)
        binding.start()


__all__ = [
    "OperationFeatureAdapter",
    "OperationPlanFacade",
    "OperationPlanFeature",
    "RuntimeContextFactory",
]
