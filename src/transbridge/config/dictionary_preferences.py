# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Application-wide automatic dictionary actions in the shared INI repository."""

from dataclasses import dataclass
import logging
from threading import Lock

from .repository import ConfigRepository, ConfigSnapshot, default_config_repository


@dataclass(frozen=True, slots=True)
class PendingDictionaryPreference:
    preferences: "DictionaryPreferences"
    name: str
    value: bool

    def commit(self):
        try:
            self.preferences._persist(self)
        finally:
            self.cancel()

    def cancel(self):
        self.preferences._release(self)


@dataclass(frozen=True, slots=True)
class _Flags:
    save_on_project_save: bool
    apply_on_plugin_open: bool

    @classmethod
    def from_config(cls, snapshot: ConfigSnapshot):
        def enabled(key):
            raw = snapshot.value("dictionary", key, "true").strip().lower()
            if raw not in ("true", "false"):
                logging.getLogger(__name__).warning("词典设置 %s 的值无效，已跳过对应自动操作", key)
                return False
            return raw == "true"

        return cls(enabled("save_on_project_save"), enabled("apply_on_plugin_open"))


class DictionaryPreferences:
    def __init__(self, repository: ConfigRepository | None = None):
        self._repository = repository
        self._lock = Lock()
        self._flags: _Flags | None = None
        self._pending: dict[str, PendingDictionaryPreference] = {}
        self._pending_lock = Lock()

    def _repo(self) -> ConfigRepository:
        if self._repository is None:
            self._repository = default_config_repository()
        return self._repository

    def _enabled(self, name: str) -> bool:
        with self._pending_lock:
            pending = self._pending.get(name)
            if pending is not None:
                return pending.value
        if self._flags is None:
            with self._lock:
                if self._flags is None:
                    self._flags = _Flags.from_config(self._repo().load())
        return getattr(self._flags, name)

    @property
    def save_on_project_save(self) -> bool:
        return self._enabled("save_on_project_save")

    @property
    def apply_on_plugin_open(self) -> bool:
        return self._enabled("apply_on_plugin_open")

    def stage_set(self, name: str, value: bool) -> PendingDictionaryPreference:
        """Make the click effective immediately while its file write runs on a worker."""
        if name not in ("save_on_project_save", "apply_on_plugin_open"):
            raise ValueError(f"未知词典设置：{name}")
        if type(value) is not bool:
            raise TypeError("词典设置必须为布尔值")
        with self._pending_lock:
            if name in self._pending:
                raise RuntimeError("该词典设置正在保存")
            change = PendingDictionaryPreference(self, name, value)
            self._pending[name] = change
            return change

    def _release(self, change):
        with self._pending_lock:
            if self._pending.get(change.name) is change:
                del self._pending[change.name]

    def _persist(self, change: PendingDictionaryPreference):
        with self._lock:
            with self._pending_lock:
                if self._pending.get(change.name) is not change:
                    return
            snapshot = self._repo().update_sections({"dictionary": {change.name: change.value}})
            self._flags = _Flags.from_config(snapshot)

    def set_save_on_project_save(self, value: bool):
        self.stage_set("save_on_project_save", value).commit()

    def set_apply_on_plugin_open(self, value: bool):
        self.stage_set("apply_on_plugin_open", value).commit()


def dictionary_preferences(context) -> DictionaryPreferences:
    preferences = getattr(context, "dictionary_preferences", None)
    return preferences if preferences is not None else DictionaryPreferences()
