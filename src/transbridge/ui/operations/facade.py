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

from transbridge.application.contracts import RequestContext
from transbridge.application.tasks import OwnerRef, TaskRuntime
from transbridge.bootstrap.runtime import AppRuntime

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

    Construction requires all four feature adapters, so a production window
    cannot silently fall back to an empty optional interface.  Every begin
    method opens the same dialog, executes feature-owned preflight, consumes
    one request-bound confirmation and only then calls the feature task port.
    """

    def __init__(
        self,
        runtime: AppRuntime,
        runtime_context: RuntimeContextFactory,
        features: tuple[OperationPlanFeature, ...],
        *,
        dialog_factory=OperationPlanDialog,
        dialog_factories: Mapping[OperationKind, Callable[[object, object, object | None], object]] | None = None,
    ) -> None:
        self._runtime = runtime
        self._runtime_context = runtime_context
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
        )

    @property
    def tasks(self) -> TaskRuntime:
        """Expose the exact process runtime used by every feature adapter."""
        return self._runtime.tasks

    @property
    def active_plan_count(self) -> int:
        return self._coordinator.active_window_count

    def begin_upload(self, context, *, batch: bool = False, **values):
        return self._coordinator.begin_upload(context, batch=batch, **values)

    def begin_download(self, context, *, batch: bool = False, **values):
        return self._coordinator.begin_download(context, batch=batch, **values)

    def begin_write(self, context, *, batch: bool = False, **values):
        return self._coordinator.begin_write(context, batch=batch, **values)

    def begin_fomod(self, context, *, batch: bool = False, parent=None, **values):
        return self._coordinator.begin_fomod(context, batch=batch, parent=parent, **values)

    def supports(self, kind: OperationKind | str, context: object, *, batch: bool = False) -> bool:
        """Return whether this facade can preserve parity for the requested scope."""
        feature = self._features.get(OperationKind(kind))
        return feature is not None and feature.supports(context, batch)

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


__all__ = [
    "OperationFeatureAdapter",
    "OperationPlanFacade",
    "OperationPlanFeature",
    "RuntimeContextFactory",
]
