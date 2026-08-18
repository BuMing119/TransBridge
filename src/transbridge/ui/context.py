"""
AppContext: 全局应用上下文，持有配置、当前用户、当前项目和本地翻译集合。
所有组件通过持有同一个 AppContext 实例来共享状态，状态变化通过 Qt 信号广播。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import threading
from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject, Qt, pyqtSignal, pyqtSlot

from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.paratranz.config_manager import ParatranzConfig

if TYPE_CHECKING:
    from transbridge.application.projections import ProjectionSnapshot, ProjectionStore, ProjectionSubscription
    from transbridge.persistence.project import ProjectHandle
    from transbridge.persistence.variant_store import VariantStore
    from transbridge.persistence.workspace import WorkspaceState


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
    source_snapshot: object = None                 # V2 写回所需的不可变来源快照
    format_id: object = None                       # V2 FormatId；兼容期保持可选


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
    # Story 03: ViewModel 扩展
    filter_changed = pyqtSignal(dict)         # 筛选状态变更
    label_data_changed = pyqtSignal()         # 标签数据变更

    def __init__(
        self,
        parent=None,
        *,
        project_projection: ProjectionStore | None = None,
        project_commands=None,
        runtime_context=None,
    ):
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

        # Story 03: ViewModel 扩展
        self._filter_state: dict = dict(self.DEFAULT_FILTER_STATE)
        self._label_library: dict[str, dict] = {}        # B1: 标签库 {label_id: {name, color}}
        self._entry_labels: dict[str, set[str]] = {}     # B1: 条目标签 {entry_id: {label_id, ...}}
        self._translation_scope: dict = {                # E8: 翻译作用域
            "stages": [], "labels": [], "categories": [], "action": "include",
        }
        self._selected_ids: set[str] = set()            # H2: Agent 选择集合（独立于标签系统）
        self.paratranz_project_id: int | None = None     # Story 15: 当前选中的 ParaTranz 项目 ID（会话内有效）
        self._project_projection = project_projection
        self._project_commands = project_commands
        self._runtime_context = runtime_context
        self._projection_subscription: ProjectionSubscription | None = None
        self._projection_dirty = False
        self._active_variant_id: str | None = None

        if self._project_projection is not None:
            self._projection_subscription = self._project_projection.subscribe(
                self._on_project_projection
            )

        self.__init_safe_mutate()

    # ── Story 03: 筛选/标签/作用域 ViewModel ─────────────────────

    DEFAULT_FILTER_STATE = {
        "stage": [],
        "category": [],
        "label": [],
        "search_query": "",
        "search_field": "text",  # "id" | "key" | "text" (E6: Agent 统一搜索字段)
    }

    @property
    def filter_state(self) -> dict:
        """获取当前筛选状态（只读副本）。"""
        return dict(self._filter_state)

    @filter_state.setter
    def filter_state(self, v: dict) -> None:
        self._filter_state = dict(v)
        self.filter_changed.emit(dict(self._filter_state))

    def set_filter(self, **kwargs) -> None:
        """合并更新筛选状态并发射 filter_changed 信号。"""
        changed = False
        for k, v in kwargs.items():
            if k in self._filter_state and self._filter_state[k] != v:
                self._filter_state[k] = v
                changed = True
        if changed:
            self.filter_changed.emit(dict(self._filter_state))

    def clear_filters(self) -> None:
        """重置所有筛选条件为默认值。"""
        self._filter_state = dict(self.DEFAULT_FILTER_STATE)
        self.filter_changed.emit(dict(self._filter_state))

    # B1: 标签数据
    @property
    def label_library(self) -> dict[str, dict]:
        return {key: dict(value) for key, value in self._label_library.items()}

    @label_library.setter
    def label_library(self, v: dict[str, dict]) -> None:
        if self._project_projection is not None:
            raise RuntimeError("label_library is a read-only projection; submit a Variant command")
        self._label_library = {key: dict(value) for key, value in v.items()}
        self.label_data_changed.emit()

    @property
    def entry_labels(self) -> dict[str, set[str]]:
        return {key: set(value) for key, value in self._entry_labels.items()}

    @entry_labels.setter
    def entry_labels(self, v: dict[str, set[str]]) -> None:
        if self._project_projection is not None:
            raise RuntimeError("entry_labels is a read-only projection; submit a Variant command")
        self._entry_labels = {key: set(value) for key, value in v.items()}
        self.label_data_changed.emit()

    # E8: 翻译作用域（带类型校验的正式属性）
    @property
    def translation_scope(self) -> dict:
        return dict(self._translation_scope)

    @translation_scope.setter
    def translation_scope(self, v: dict) -> None:
        stages = v.get("stages", [])
        if not isinstance(stages, list) or not all(isinstance(s, int) for s in stages):
            raise TypeError("translation_scope.stages 必须为 list[int]")
        valid_actions = {"include", "exclude", "only"}
        action = v.get("action", "include")
        if action not in valid_actions:
            raise ValueError(f"translation_scope.action 必须为 {valid_actions}")
        self._translation_scope = {
            "stages": list(stages),
            "labels": list(v.get("labels", [])),
            "categories": list(v.get("categories", [])),
            "action": action,
        }

    # H2: Agent 选择集合（独立于用户标签系统）
    @property
    def selected_ids(self) -> set[str]:
        return self._selected_ids

    @selected_ids.setter
    def selected_ids(self, v: set[str]) -> None:
        self._selected_ids = v

    def select_entries(self, entry_ids: list[str], action: str = "select") -> int:
        """Agent 条目选择操作。action: select/deselect/clear。返回当前选中数。"""
        if action == "clear":
            self._selected_ids.clear()
        elif action == "select":
            self._selected_ids.update(entry_ids)
        elif action == "deselect":
            self._selected_ids.difference_update(entry_ids)
        return len(self._selected_ids)

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
        if self._project_projection is not None:
            raise RuntimeError("active_project is a read-only projection; use the lifecycle command facade")
        self._active_project = v
        self.workspace_changed.emit()

    @property
    def active_variant(self) -> str | None:
        return self._active_variant

    @active_variant.setter
    def active_variant(self, name: str | None) -> None:
        if self._project_projection is not None:
            raise RuntimeError("active_variant is a read-only projection; use the lifecycle command facade")
        old = self._active_variant
        self._active_variant = name
        if old != name and name is not None:
            self.variant_changed.emit(name)

    @property
    def variant_store(self) -> VariantStore | None:
        return self._variant_store

    @variant_store.setter
    def variant_store(self, v: VariantStore | None) -> None:
        if self._project_projection is not None:
            raise RuntimeError("variant_store is disabled when V2 aggregate authority is active")
        self._variant_store = v

    def mark_dirty(self) -> None:
        """标记当前版本数据已修改，触发自动保存防抖。"""
        if self._project_projection is not None:
            return
        if self._variant_store is not None:
            self._variant_store.dirty = True
            self.dirty_changed.emit()

    @property
    def dirty(self) -> bool:
        if self._project_projection is not None:
            return self._projection_dirty
        return self._variant_store is not None and self._variant_store.dirty

    @property
    def active_variant_id(self) -> str | None:
        return self._active_variant_id

    @property
    def uses_authoritative_projection(self) -> bool:
        return self._project_projection is not None

    def close_projection(self) -> None:
        subscription = self._projection_subscription
        self._projection_subscription = None
        if subscription is not None:
            subscription.close()

    def update_projected_entry(
        self,
        local_key: str,
        *,
        translation: str | None = None,
        stage: int | None = None,
    ):
        if self._project_commands is None or self._runtime_context is None:
            raise RuntimeError("authoritative Variant command adapter is unavailable")
        return self._project_commands.update_entry(
            local_key,
            self._runtime_context,
            translation=translation,
            stage=stage,
        )

    def replace_projected_labels(
        self,
        entry_labels: dict[str, set[str]],
        label_library: dict[str, dict],
    ):
        if self._project_commands is None or self._runtime_context is None:
            raise RuntimeError("authoritative Variant command adapter is unavailable")
        return self._project_commands.replace_labels(
            entry_labels,
            label_library,
            self._runtime_context,
        )

    def _on_project_projection(self, snapshot: ProjectionSnapshot | None) -> None:
        old_dirty = self._projection_dirty
        old_variant = self._active_variant_id
        if snapshot is None:
            self._projection_dirty = False
            self._active_variant_id = None
            self._label_library = {}
            self._entry_labels = {}
        else:
            values = snapshot.to_dict()["values"]
            self._projection_dirty = snapshot.dirty
            variant_id = values.get("variant_id") or values.get("active_variant_id")
            self._active_variant_id = None if variant_id is None else str(variant_id)
            library = values.get("label_library") or {}
            self._label_library = {str(key): dict(value) for key, value in library.items()}
            labels: dict[str, set[str]] = {}
            for entry in values.get("entries", ()):
                entry_key = entry.get("entry_key") or {}
                local_key = entry_key.get("local_key")
                if local_key is not None:
                    labels[str(local_key)] = set(str(value) for value in entry.get("labels", ()))
            self._entry_labels = labels
        self.label_data_changed.emit()
        if old_dirty != self._projection_dirty:
            self.dirty_changed.emit()
        if old_variant != self._active_variant_id and self._active_variant_id is not None:
            self.variant_changed.emit(self._active_variant_id)

    # ── C10: 跨线程安全状态变更 ─────────────────────────────────

    mutation_requested = pyqtSignal(object)  # 携带 callable，由主线程执行

    def __init_safe_mutate(self) -> None:
        """初始化安全变更机制。延迟调用以兼容 QObject.__init__ 顺序。"""
        self._mutation_lock = threading.Lock()
        self._mutation_pending: deque = deque()
        self.mutation_requested.connect(self._on_mutation, Qt.ConnectionType.QueuedConnection)

    @pyqtSlot(object)
    def _on_mutation(self, callback) -> None:
        """在主线程执行通过 safe_mutate 排队的变更回调。(C10)"""
        try:
            callback()
        except Exception:
            import logging
            logging.getLogger(__name__).exception(
                "[AppContext] safe_mutate 回调执行异常"
            )

    def safe_mutate(self, callback) -> None:
        """将回调调度到主线程执行，确保对共享状态（TranslationEntry、entry_labels 等）
        的写入不会与 UI 读取竞态。(C10)

        设计: 使用 mutation_requested 信号 + QueuedConnection，
        当从 ThreadPoolExecutor 工作线程调用时，回调自动排队到主线程事件循环。
        """
        self.mutation_requested.emit(callback)

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
