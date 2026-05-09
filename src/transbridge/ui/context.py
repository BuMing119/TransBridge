"""
AppContext: 全局应用上下文，持有配置、当前用户、当前项目和本地翻译集合。
所有组件通过持有同一个 AppContext 实例来共享状态，状态变化通过 Qt 信号广播。
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject, pyqtSignal

from src.transbridge.paratranz.config_manager import ParatranzConfig
from src.transbridge.converter.translation_entry_collection import TranslationEntryCollection

if TYPE_CHECKING:
    from src.transbridge.persistence.workspace import WorkspaceState
    from src.transbridge.persistence.project import ProjectHandle
    from src.transbridge.persistence.variant_store import VariantStore


@dataclass
class CollectionSlot:
    """单个翻译集合槽位，绑定一次解析的所有上下文。"""
    label: str                                      # ComboBox 显示名（文件 stem）
    collection: TranslationEntryCollection
    esp_path: str | None = None
    eet_path: str | None = None
    xt_path: str | None = None
    strings_path: str | None = None                 # Strings 目录路径（用于导入翻译）
    strings_lang: str = "chinese"                   # strings 文件语言标签
    sst_path: str | None = None                     # SST 二进制文件路径（用于迁移译文）
    migrate_count: int = 0
    plugin: object = None                           # 解析出的 Plugin 实例
    strings_lookup: object = None                  # PluginStringsLookup 实例（本地化插件）


class AppContext(QObject):
    config_changed = pyqtSignal(object)       # ParatranzConfig
    user_changed = pyqtSignal(object)         # dict | None
    project_selected = pyqtSignal(object)     # dict | None
    collection_changed = pyqtSignal(object)   # TranslationEntryCollection | None
    collection_list_changed = pyqtSignal()    # 集合列表有增删
    navigate_to = pyqtSignal(int)             # 请求切换主 tab（0=工作台, 1=ParaTranz 管理）
    project_list_changed = pyqtSignal()       # 请求刷新项目列表（ParaTranz）
    workspace_changed = pyqtSignal()          # 持久化项目列表变动
    variant_changed = pyqtSignal(str)         # 翻译版本切换（variant_name）
    dirty_changed = pyqtSignal()              # 版本数据被修改（触发自动保存防抖）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._config: ParatranzConfig = ParatranzConfig.create_or_load()
        self._current_user: dict | None = None
        self._current_project: dict | None = None

        # 多集合注册表
        self._slots: dict[str, CollectionSlot] = {}   # key = esp/eet 全路径
        self._active_key: str | None = None

        self.mine_project_ids: set = set()  # 「我参与的」视图最近一次加载的项目 ID 集合

        # 持久化相关（ADR-006）
        self._workspace: WorkspaceState | None = None
        self._active_project: ProjectHandle | None = None
        self._active_variant: str | None = None
        self._variant_store: VariantStore | None = None
        self.collection_changed.connect(lambda _: self.mark_dirty())

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

    # ── 多集合管理 ─────────────────────────────────────────────

    def add_slot(self, key: str, slot: CollectionSlot) -> None:
        """注册或覆盖一个槽位，并激活它。"""
        self._slots[key] = slot
        self._active_key = key
        self.collection_list_changed.emit()
        self.collection_changed.emit(slot.collection)

    def remove_slot(self, key: str) -> None:
        """移除一个槽位。若是当前活跃槽位，则切换到最近的其他槽位。"""
        if key not in self._slots:
            return
        del self._slots[key]
        if self._active_key == key:
            remaining = list(self._slots.keys())
            self._active_key = remaining[-1] if remaining else None
            new_collection = self._slots[self._active_key].collection if self._active_key else None
            self.collection_list_changed.emit()
            self.collection_changed.emit(new_collection)
        else:
            self.collection_list_changed.emit()

    def activate_slot(self, key: str) -> None:
        """激活指定槽位，触发 collection_changed。"""
        if key not in self._slots or key == self._active_key:
            return
        self._active_key = key
        self.collection_changed.emit(self._slots[key].collection)

    @property
    def active_slot(self) -> CollectionSlot | None:
        if self._active_key and self._active_key in self._slots:
            return self._slots[self._active_key]
        return None

    @property
    def slots(self) -> dict[str, CollectionSlot]:
        return self._slots

    @property
    def active_key(self) -> str | None:
        return self._active_key

    # ── 委托 property（向后兼容） ──────────────────────────────

    @property
    def collection(self) -> TranslationEntryCollection | None:
        slot = self.active_slot
        return slot.collection if slot else None

    @collection.setter
    def collection(self, v: TranslationEntryCollection | None) -> None:
        slot = self.active_slot
        if slot is not None:
            slot.collection = v
        self.collection_changed.emit(v)

    @property
    def esp_path(self) -> str | None:
        slot = self.active_slot
        return slot.esp_path if slot else None

    @esp_path.setter
    def esp_path(self, v: str | None) -> None:
        slot = self.active_slot
        if slot is not None:
            slot.esp_path = v

    @property
    def eet_path(self) -> str | None:
        slot = self.active_slot
        return slot.eet_path if slot else None

    @eet_path.setter
    def eet_path(self, v: str | None) -> None:
        slot = self.active_slot
        if slot is not None:
            slot.eet_path = v

    @property
    def xt_path(self) -> str | None:
        slot = self.active_slot
        return slot.xt_path if slot else None

    @xt_path.setter
    def xt_path(self, v: str | None) -> None:
        slot = self.active_slot
        if slot is not None:
            slot.xt_path = v

    @property
    def migrate_count(self) -> int:
        slot = self.active_slot
        return slot.migrate_count if slot else 0

    @migrate_count.setter
    def migrate_count(self, v: int) -> None:
        slot = self.active_slot
        if slot is not None:
            slot.migrate_count = v

    @property
    def plugin(self):
        slot = self.active_slot
        return slot.plugin if slot else None

    @plugin.setter
    def plugin(self, v) -> None:
        slot = self.active_slot
        if slot is not None:
            slot.plugin = v

    @property
    def strings_lookup(self):
        slot = self.active_slot
        return slot.strings_lookup if slot else None

    @strings_lookup.setter
    def strings_lookup(self, v) -> None:
        slot = self.active_slot
        if slot is not None:
            slot.strings_lookup = v

    @property
    def strings_lang(self) -> str:
        slot = self.active_slot
        return slot.strings_lang if slot else "chinese"

    @strings_lang.setter
    def strings_lang(self, v: str) -> None:
        slot = self.active_slot
        if slot is not None:
            slot.strings_lang = v

    # ── 持久化相关属性（ADR-006） ────────────────────────────────

    @property
    def workspace(self) -> WorkspaceState | None:
        return self._workspace

    @workspace.setter
    def workspace(self, v: WorkspaceState | None) -> None:
        self._workspace = v

    @property
    def active_project(self) -> ProjectHandle | None:
        return self._active_project

    @active_project.setter
    def active_project(self, v: ProjectHandle | None) -> None:
        self._active_project = v
        self.workspace_changed.emit()

    @property
    def active_variant(self) -> str | None:
        return self._active_variant

    @active_variant.setter
    def active_variant(self, name: str | None) -> None:
        old = self._active_variant
        self._active_variant = name
        if old != name and name is not None:
            self.variant_changed.emit(name)

    @property
    def variant_store(self) -> VariantStore | None:
        return self._variant_store

    @variant_store.setter
    def variant_store(self, v: VariantStore | None) -> None:
        self._variant_store = v

    def mark_dirty(self) -> None:
        """标记当前版本数据已修改，触发自动保存防抖。"""
        if self._variant_store is not None:
            self._variant_store.dirty = True
            self.dirty_changed.emit()

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
