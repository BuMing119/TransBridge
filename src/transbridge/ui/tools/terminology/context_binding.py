"""Invalidate a terminology window before its shared profile context can drift."""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal


class TerminologyWindowContext(QObject):
    """Bind profile actions and updates to the same identity as the window presenter."""

    state_changed = pyqtSignal(object)

    def __init__(self, window, context, profiles, identity: tuple[str | None, str | None]) -> None:
        super().__init__(window)
        self._window = window
        self._context = context
        self._profiles = profiles
        self._identity = identity
        self._closed = False
        self._connections = []
        for name in ("project_changed", "variant_changed"):
            signal = getattr(context, name, None)
            if signal is not None:
                signal.connect(self._identity_changed)
                self._connections.append((signal, self._identity_changed))
        if profiles is not None:
            profiles.state_changed.connect(self._profile_changed)
            self._connections.append((profiles.state_changed, self._profile_changed))

    @property
    def profiles(self):
        return self if self._profiles is not None else None

    @property
    def identity(self):
        return self._identity if not self._closed and self._live_identity() == self._identity else None

    @property
    def state(self):
        return self._profiles.state

    def select(self, profile_id) -> None:
        if self._current():
            self._profiles.select(profile_id)

    def open_manager(self) -> None:
        if self._current():
            self._profiles.open_manager()

    def refresh(self) -> None:
        if self._current():
            self._profiles.refresh()

    def preview_source_import(self, source):
        return self._require_profiles().preview_source_import(source)

    def create_from_source_import(self, name, preview, *, select=False):
        return self._require_profiles().create_from_source_import(name, preview, select=select)

    def dispose(self) -> None:
        self._closed = True
        for signal, callback in self._connections:
            signal.disconnect(callback)
        self._connections.clear()

    def _live_identity(self):
        if self._context is not None:
            return getattr(self._context, "active_version_identity", None)
        return getattr(self._profiles, "identity", self._identity)

    def _current(self) -> bool:
        if self._closed:
            return False
        if self._live_identity() == self._identity:
            return True
        self.dispose()
        self._window.setEnabled(False)
        self._window.close()
        return False

    def _require_profiles(self):
        if not self._current() or self._profiles is None:
            raise RuntimeError("工程或翻译版本已变化，请重新打开术语工作台。")
        return self._profiles

    def _identity_changed(self, *_args) -> None:
        self._current()

    def _profile_changed(self, state) -> None:
        if self._current():
            self.state_changed.emit(state)


__all__ = ["TerminologyWindowContext"]
