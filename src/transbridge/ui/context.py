"""
AppContext: 全局应用上下文，持有配置、当前用户、当前项目和本地翻译集合。
所有组件通过持有同一个 AppContext 实例来共享状态，状态变化通过 Qt 信号广播。
"""

from PyQt6.QtCore import QObject, pyqtSignal

from src.transbridge.paratranz.config_manager import ParatranzConfig
from src.transbridge.converter.translation_entry_collection import TranslationEntryCollection


class AppContext(QObject):
    config_changed = pyqtSignal(object)       # ParatranzConfig
    user_changed = pyqtSignal(object)         # dict | None
    project_selected = pyqtSignal(object)     # dict | None
    collection_changed = pyqtSignal(object)   # TranslationEntryCollection | None

    def __init__(self, parent=None):
        super().__init__(parent)
        self._config: ParatranzConfig = ParatranzConfig.create_or_load()
        self._current_user: dict | None = None
        self._current_project: dict | None = None
        self._collection: TranslationEntryCollection | None = None
        self.esp_path: str | None = None  # 最近一次解析的插件文件路径

    # ── config ────────────────────────────────────────────────

    @property
    def config(self) -> ParatranzConfig:
        return self._config

    @config.setter
    def config(self, v: ParatranzConfig) -> None:
        self._config = v
        self.config_changed.emit(v)

    # ── current_user ──────────────────────────────────────────

    @property
    def current_user(self) -> dict | None:
        return self._current_user

    @current_user.setter
    def current_user(self, v: dict | None) -> None:
        self._current_user = v
        self.user_changed.emit(v)

    # ── current_project ───────────────────────────────────────

    @property
    def current_project(self) -> dict | None:
        return self._current_project

    @current_project.setter
    def current_project(self, v: dict | None) -> None:
        self._current_project = v
        self.project_selected.emit(v)

    # ── collection ────────────────────────────────────────────

    @property
    def collection(self) -> TranslationEntryCollection | None:
        return self._collection

    @collection.setter
    def collection(self, v: TranslationEntryCollection | None) -> None:
        self._collection = v
        self.collection_changed.emit(v)

    # ── helpers ───────────────────────────────────────────────

    def is_admin(self) -> bool:
        """当前用户是否为当前项目的管理员或所有者。"""
        if not self._current_project or not self._current_user:
            return False
        uid = self._current_user.get("id")
        if self._current_project.get("uid") == uid:
            return True  # 项目所有者
        for m in self._current_project.get("_members", []):
            if m.get("uid") == uid and m.get("permission", 1) >= 3:
                return True
        return False

    def is_member(self) -> bool:
        """当前用户是否为当前项目的成员（含所有者）。"""
        if not self._current_project or not self._current_user:
            return False
        uid = self._current_user.get("id")
        if self._current_project.get("uid") == uid:
            return True
        return any(m.get("uid") == uid for m in self._current_project.get("_members", []))
