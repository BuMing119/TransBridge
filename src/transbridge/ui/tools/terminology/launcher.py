"""Open and retain a terminology window for the active workspace context."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QWidget

from transbridge.application.contracts import RequestContext
from transbridge.ui.windowing import show_and_activate

from .presenter import TerminologyPresenter, TerminologyUiServices

if TYPE_CHECKING:
    from .window import TerminologyWindow


class TerminologyLauncher:
    """Resolve a narrow service bundle and retain one window per shell host."""

    def __init__(self, host: object) -> None:
        self._host = host
        self._window: TerminologyWindow | None = None
        self._retiring: dict[int, TerminologyWindow] = {}

    @property
    def window(self) -> TerminologyWindow | None:
        return self._window

    def open(self) -> TerminologyWindow | None:
        from .window import TerminologyWindow

        if self._window is not None and not self._window.presenter.closed:
            if self._window.context_is_current:
                return show_and_activate(self._window)
            old_window = self._window
            self._retiring[id(old_window)] = old_window
            old_window.invalidate_context()
        runtime = getattr(self._host, "app_runtime", None)
        if runtime is None:
            getattr(self._host, "show_message")("术语工作台需要应用运行服务，当前不可用。")
            return None
        context = _terminology_context(self._host, runtime)
        services = TerminologyUiServices.from_runtime(runtime, context)
        parent = self._host if isinstance(self._host, QWidget) else None
        workbench = getattr(self._host, "workbench", None)
        profile_controller = getattr(workbench, "terminology_profile_controller", None)
        window = TerminologyWindow(
            TerminologyPresenter(services, context),
            parent,
            ui_context=getattr(self._host, "context", None),
            profile_controller=profile_controller,
        )
        identity = id(window)
        window.destroyed.connect(lambda _obj=None: self._clear(identity))
        window.closed.connect(lambda: self._retired_closed(identity))
        self._window = window
        show_and_activate(window)
        return window

    def close(self) -> None:
        for window in tuple(self._retiring.values()):
            window.close()
        if self._window is not None and self._window.close():
            self._window.deleteLater()
            self._window = None

    def _retired_closed(self, identity: int) -> None:
        window = self._retiring.pop(identity, None)
        if window is not None:
            window.deleteLater()

    def _clear(self, identity: int) -> None:
        self._retiring.pop(identity, None)
        if self._window is not None and id(self._window) == identity:
            self._window = None


def _terminology_context(host: object, runtime: object) -> RequestContext:
    ui_context = getattr(host, "context", None)
    identity = None if ui_context is None else getattr(ui_context, "active_version_identity", None)
    project_id, variant_id = identity if identity is not None else (None, None)
    existing = getattr(host, "runtime_context", None)
    if isinstance(existing, RequestContext):
        metadata = dict(existing.metadata)
        metadata.setdefault("entrypoint", "gui")
        metadata.setdefault("manual_actor_id", existing.owner_id)
        _add_display_context(metadata, ui_context)
        return replace(
            existing,
            project_id=project_id,
            variant_id=variant_id,
            metadata=tuple(sorted(metadata.items())),
        )
    metadata = {"entrypoint": "gui", "manual_actor_id": "local-gui-user"}
    _add_display_context(metadata, ui_context)
    return runtime.context(
        "terminology-workbench",
        project_id=project_id,
        variant_id=variant_id,
        metadata=tuple(sorted(metadata.items())),
    )


def _add_display_context(metadata: dict[str, str], ui_context: object | None) -> None:
    if ui_context is None:
        return
    project_name = getattr(ui_context, "project_name", None)
    if project_name:
        metadata.setdefault("project_name", str(project_name))
    variants = tuple(getattr(ui_context, "project_variants", ()))
    active = next((item for item in variants if item.get("active")), None)
    variant_name = None if active is None else active.get("name")
    if variant_name:
        metadata.setdefault("variant_name", str(variant_name))


__all__ = ["TerminologyLauncher"]
