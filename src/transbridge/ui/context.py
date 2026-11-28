# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""
AppContext: 全局应用上下文，持有配置、当前用户、当前项目和本地翻译集合。
所有组件通过持有同一个 AppContext 实例来共享状态，状态变化通过 Qt 信号广播。
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject, Qt, pyqtSignal, pyqtSlot

from transbridge.application.translation.entry_alias import ai_entry_key
from transbridge.application.workbench_filters import FilterState
from transbridge.application.workbench_statistics import scope_statistics
from transbridge.config.dictionary_preferences import DictionaryPreferences
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.paratranz.config_manager import ParatranzConfig
from transbridge.ui.project_labels import entry_label_key, exact_label_key
from transbridge.ui.project_rows import ProjectRowsCollection
from transbridge.ui.project_view_binding import ProjectViewBinding
from transbridge.ui.projection_types import CollectionSlot, projected_variant_name
from transbridge.ui.translation_task_execution import execute_translation_sources

__all__ = ["AppContext", "CollectionSlot"]

if TYPE_CHECKING:
    from transbridge.application.projections import ProjectionSnapshot, ProjectionStore
    from transbridge.persistence.v2.ids import VariantRef
    from transbridge.persistence.workspace import WorkspaceState


class AppContext(QObject):
    config_changed = pyqtSignal(object)  # ParatranzConfig
    user_changed = pyqtSignal(object)  # dict | None
    project_selected = pyqtSignal(object)  # dict | None
    paratranz_permissions_changed = pyqtSignal()  # 账号或项目成员权限变化
    collection_changed = pyqtSignal(object)  # TranslationEntryCollection | None
    collection_list_changed = pyqtSignal()  # 集合列表有增删
    navigate_to = pyqtSignal(int)  # 请求切换主 tab（0=工作台, 1=ParaTranz 管理）
    project_list_changed = pyqtSignal()  # 请求刷新项目列表（ParaTranz）
    workspace_changed = pyqtSignal()  # 持久化项目列表变动
    project_installed = pyqtSignal()  # Complete project view installed
    project_changed = pyqtSignal()  # 翻译项目/Variant 目录投影变动
    text_encodings_changed = pyqtSignal()  # 当前工程文本语言与编码变动
    paratranz_binding_changed = pyqtSignal(object)  # 当前本地工程的 ParaTranz 绑定投影
    variant_changed = pyqtSignal(str)  # 旧模式为名称，V2 模式为稳定 Variant ID
    dirty_changed = pyqtSignal()  # 版本数据被修改（触发自动保存防抖）
    # Story 03: ViewModel 扩展
    filter_changed = pyqtSignal(dict)  # 筛选状态变更
    label_data_changed = pyqtSignal()  # 标签数据变更

    def __init__(
        self,
        parent=None,
        *,
        project_projection: ProjectionStore | None = None,
        project_commands=None,
        project_source_references=None,
        project_text_encodings=None,
        text_encoding_defaults=None,
        dictionary_preferences=None,
        dictionary_base_dir=None,
        project_remote_bindings=None,
        runtime_context=None,
        effective_terminology_factory=None,
        terminology_profile_service_factory=None,
        terminology_sync_service_factory=None,
        task_result_preferences_provider=None,
        project_catalog=None,
        task_store=None,
    ):
        super().__init__(parent)
        self._config: ParatranzConfig = ParatranzConfig.create_or_load()
        self._current_user: dict | None = None
        self._current_project: dict | None = None

        # 多集合注册表
        self._slots: dict[str, CollectionSlot] = {}  # key = esp/eet 全路径
        self._active_key: str | None = None

        self.mine_project_ids: set = set()  # 「我参与的」视图最近一次加载的项目 ID 集合

        # 持久化相关（ADR-006）
        self._workspace: WorkspaceState | None = None

        # Story 03: ViewModel 扩展
        self._filter_state = FilterState()
        self._label_library: dict[str, dict] = {}  # B1: 标签库 {label_id: {name, color}}
        self._entry_labels: dict[str, set[str]] = {}  # B1: 条目标签 {entry_id: {label_id, ...}}
        self._entry_labels_exact: dict[tuple[str, ...], set[str]] = {}
        self._translation_scope: dict = {  # E8: 翻译作用域
            "stages": [],
            "labels": [],
            "categories": [],
        }
        self.workbench_row_selection = None
        self.paratranz_project_id: int | None = None  # Story 15: 当前选中的 ParaTranz 项目 ID（会话内有效）
        self._project_projection = project_projection
        self._project_commands = project_commands
        self.project_source_references = project_source_references
        self.project_text_encodings = project_text_encodings
        self.text_encoding_defaults = text_encoding_defaults
        self.dictionary_preferences = (
            dictionary_preferences if dictionary_preferences is not None else DictionaryPreferences()
        )
        self.dictionary_base_dir = dictionary_base_dir
        self._project_remote_bindings = project_remote_bindings
        self._runtime_context = runtime_context
        self._effective_terminology_factory = effective_terminology_factory
        self._terminology_profile_service_factory = terminology_profile_service_factory
        self.terminology_sync_service_factory = terminology_sync_service_factory
        self.task_result_preferences_provider = task_result_preferences_provider
        self.translation_task_executor = execute_translation_sources
        self.project_catalog = project_catalog
        self.task_store = task_store
        self.__init_safe_mutate()
        self._project_view = ProjectViewBinding(self, project_projection)
        self._project_view.start()

    # ── Story 03: 筛选/标签/作用域 ViewModel ─────────────────────

    DEFAULT_FILTER_STATE = FilterState().to_mapping()

    @property
    def filter_state(self) -> dict:
        """获取当前筛选状态（只读副本）。"""
        return self._filter_state.to_mapping()

    @filter_state.setter
    def filter_state(self, v: dict) -> None:
        state = FilterState.from_mapping(v)
        if state != self._filter_state:
            self._filter_state = state
            self.filter_changed.emit(state.to_mapping())

    def set_filter(self, **kwargs) -> None:
        """合并更新筛选状态并发射 filter_changed 信号。"""
        self.filter_state = {**self.filter_state, **kwargs}

    def clear_filters(self) -> None:
        """重置所有筛选条件为默认值。"""
        self.filter_state = self.DEFAULT_FILTER_STATE

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
        collection = self.collection
        if isinstance(collection, ProjectRowsCollection):
            return collection.labels()
        if self._project_projection is not None and collection is not None:
            return {
                entry_label_key(entry): set(self._entry_labels_exact.get(exact_label_key(entry.identity), ()))
                for entry in collection
                if entry.id
            }
        return {key: set(value) for key, value in self._entry_labels.items()}

    @entry_labels.setter
    def entry_labels(self, v: dict[str, set[str]]) -> None:
        if self._project_projection is not None:
            raise RuntimeError("entry_labels is a read-only projection; submit a Variant command")
        self._entry_labels = {key: set(value) for key, value in v.items()}
        self._entry_labels_exact = {}
        self.label_data_changed.emit()

    def workbench_statistics(self, collection, *, keys=None):
        """Read the source's durable entry labels alongside its stage and category counts."""
        if isinstance(collection, ProjectRowsCollection):
            return collection.workbench_statistics(keys=keys)
        if self._project_projection is not None:
            exact = self._entry_labels_exact
            return scope_statistics(
                collection, keys=keys, labels_for_entry=lambda entry: exact.get(exact_label_key(entry.identity), ())
            )
        return scope_statistics(collection, self._entry_labels, keys=keys)

    # E8: 翻译作用域（带类型校验的正式属性）
    @property
    def translation_scope(self) -> dict:
        return dict(self._translation_scope)

    @translation_scope.setter
    def translation_scope(self, v: dict) -> None:
        from transbridge.application.translation.task_scope import TranslationScope

        scope = TranslationScope.from_filters(v)
        self._translation_scope = {key: list(v.get(key, [])) for key in ("stages", "labels", "categories")}
        if scope.preset == "all":
            self._translation_scope["preset"] = "all"

    @property
    def selected_entries(self) -> tuple:
        return () if self.workbench_row_selection is None else self.workbench_row_selection.entries()

    @property
    def selected_ids(self) -> set[str]:
        return {ai_entry_key(entry) for entry in self.selected_entries}

    def select_entries(self, entry_ids: list[str], action: str = "select") -> int:
        if self.workbench_row_selection is None:
            raise RuntimeError("请先打开工作台表格，再选择词条。")
        self.workbench_row_selection.apply(entry_ids, action)
        return len(self.selected_entries)

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
        self.paratranz_permissions_changed.emit()

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
    def active_variant(self) -> str | None:
        """Display name of the current projected variant."""
        return projected_variant_name(self.project_variants, self.active_variant_id)

    @property
    def dirty(self) -> bool:
        return self._project_view.state.dirty

    @property
    def active_variant_id(self) -> str | None:
        return self._project_view.state.variant_id

    @property
    def active_project_id(self) -> str | None:
        return self._project_view.state.project_id

    @property
    def project_revision(self) -> int | None:
        return self._project_view.state.project_revision

    @property
    def variant_revision(self) -> int | None:
        return self._project_view.state.variant_revision

    @property
    def paratranz_binding(self) -> dict[str, object] | None:
        return None if self._project_view.state.binding is None else dict(self._project_view.state.binding)

    def resolve_paratranz_target(
        self,
        *,
        explicit_project_id: int | None = None,
        explicit_project_name: str = "",
        explicit_verified: bool = False,
    ):
        from transbridge.application.projects import ParaTranzTargetResolver

        user_id = None
        if self._current_user is not None:
            user_id = self._current_user.get("id")
        if user_id is None:
            user_id = getattr(self._config, "user_id", None)
        return ParaTranzTargetResolver().resolve(
            binding=self._project_view.state.binding,
            binding_revision=self._project_view.state.project_revision,
            explicit_project_id=explicit_project_id,
            explicit_project_name=explicit_project_name,
            endpoint=str(getattr(self._config, "base_url", "")),
            account_user_id=user_id,
            explicit_verified=explicit_verified,
        )

    def set_paratranz_binding(self, binding):
        if self._project_remote_bindings is None or self._runtime_context is None:
            raise RuntimeError("authoritative Project remote binding adapter is unavailable")
        request = self._runtime_context
        if request.project_id is None:
            request = replace(request, project_id=self.active_project_id)
        return self._project_remote_bindings.set_paratranz_binding(
            binding,
            request,
            expected_project_revision=self._project_view.state.project_revision,
        )

    def clear_paratranz_binding(self):
        if self._project_remote_bindings is None or self._runtime_context is None:
            raise RuntimeError("authoritative Project remote binding adapter is unavailable")
        return self._project_remote_bindings.clear_paratranz_binding(
            self._runtime_context,
            expected_project_revision=self._project_view.state.project_revision,
        )

    @property
    def project_name(self) -> str | None:
        return self._project_view.state.name

    @property
    def project_variants(self) -> tuple[dict[str, object], ...]:
        return tuple(dict(value) for value in self._project_view.state.variants)

    @property
    def project_sources(self) -> tuple[dict[str, object], ...]:
        return tuple(dict(value) for value in self._project_view.state.sources)

    @property
    def uses_authoritative_projection(self) -> bool:
        return self._project_projection is not None

    def entry_projection(self, keys, *, snapshot: ProjectionSnapshot | None = None):
        """Read authoritative values for exact workbench rows in a single snapshot."""
        from transbridge.ui.entry_projection import EntryProjectionUpdate

        if snapshot is None and self._project_projection is not None:
            snapshot = self._project_projection.snapshot()
        return EntryProjectionUpdate.from_snapshot(snapshot, keys=keys)

    @property
    def project_open_generation(self) -> int:
        return self._project_view.generation

    @property
    def project_opening(self) -> bool:
        return self._project_view.opening

    @property
    def project_installing(self) -> bool:
        return self._project_view.installing

    @property
    def project_view_failed(self) -> bool:
        return self._project_view.failed

    @project_view_failed.setter
    def project_view_failed(self, failed: bool) -> None:
        self._project_view.failed = bool(failed)

    def begin_project_open(self) -> int:
        return self._project_view.begin()

    def install_project_open(
        self, token: int, snapshot: ProjectionSnapshot, slots, *, state=None, recover_source_update: bool = False
    ) -> bool:
        return self._project_view.install(
            token, snapshot, slots, state=state, recover_source_update=recover_source_update
        )

    def finish_project_open(self, token: int) -> bool:
        return self._project_view.finish(token)

    def install_working_sources(self, identity, *, additions=(), removals=(), view_token=None) -> bool:
        return self._project_view.install_working_sources(
            identity, additions=additions, removals=removals, view_token=view_token
        )

    def close_projection(self) -> None:
        self._project_view.close()

    def update_projected_entry(
        self,
        local_key,
        *,
        translation: str | None = None,
        stage: int | None = None,
        expected_project_revision: int | None = None,
        expected_variant_revision: int | None = None,
        expected_variant_ref: VariantRef | None = None,
    ):
        if self._project_commands is None or self._runtime_context is None:
            raise RuntimeError("authoritative Variant command adapter is unavailable")
        rejected = self._project_view.edit_rejection()
        if rejected is not None:
            return rejected
        return self._project_commands.update_entry(
            local_key,
            self._runtime_context,
            translation=translation,
            stage=stage,
            expected_project_revision=expected_project_revision,
            expected_variant_revision=expected_variant_revision,
            expected_variant_ref=expected_variant_ref,
        )

    def replace_projected_labels(
        self,
        entry_labels: dict[str, set[str]],
        label_library: dict[str, dict],
        *,
        expected_project_revision: int | None = None,
        expected_variant_revision: int | None = None,
        expected_variant_ref: VariantRef | None = None,
        exact_changes: dict | None = None,
    ):
        if self._project_commands is None or self._runtime_context is None:
            raise RuntimeError("authoritative Variant command adapter is unavailable")
        rejected = self._project_view.edit_rejection()
        if rejected is not None:
            return rejected
        collection = self.collection
        if exact_changes is not None:
            exact_labels = exact_changes
        elif isinstance(collection, ProjectRowsCollection):
            exact_labels = collection.label_changes(
                entry_labels, label_library, removing_library_labels=bool(self._label_library.keys() - label_library)
            )
        elif collection is None:
            exact_labels = entry_labels
        else:
            exact_labels = {}
            for entry in collection:
                keys = (
                    (entry.identity.serialize(),)
                    if entry.requires_original_match
                    else (entry.id, entry.identity.serialize(), entry.key)
                )
                labels = next((entry_labels[key] for key in keys if key in entry_labels), ())
                exact_labels[entry.identity] = set(labels)
        expected = (
            {"only_listed": True} if exact_changes is not None or isinstance(collection, ProjectRowsCollection) else {}
        )
        if expected_project_revision is not None:
            expected["expected_project_revision"] = expected_project_revision
        if expected_variant_revision is not None:
            expected["expected_variant_revision"] = expected_variant_revision
        if expected_variant_ref is not None:
            expected["expected_variant_ref"] = expected_variant_ref
        return self._project_commands.replace_labels(
            exact_labels,
            label_library,
            self._runtime_context,
            **expected,
        )

    @property
    def active_version_identity(self) -> tuple[str, str] | None:
        if self.active_project_id is None or self.active_variant_id is None:
            return None
        return self.active_project_id, self.active_variant_id

    @property
    def project_commands(self):
        return self._project_commands

    @property
    def runtime_context(self):
        return self._runtime_context

    def authoritative_projection_diverged(self) -> bool:
        """Return whether a visible workbench state bypassed the V2 aggregate."""
        return self._project_view.diverged()

    @property
    def effective_terminology_factory(self):
        """Return the injected Project/Variant-scoped read-only adapter factory."""

        return self._effective_terminology_factory

    @property
    def terminology_profile_service_factory(self):
        """Return the composition for formal profile operations and preview."""

        return self._terminology_profile_service_factory

    def _apply_project_projection(self, snapshot: ProjectionSnapshot | None) -> None:
        self._project_view.receive(snapshot)

    # ── C10: 跨线程安全状态变更 ─────────────────────────────────

    mutation_requested = pyqtSignal(object)  # 携带 callable，由主线程执行

    def __init_safe_mutate(self) -> None:
        """初始化安全变更机制。延迟调用以兼容 QObject.__init__ 顺序。"""
        self.mutation_requested.connect(self._on_mutation, Qt.ConnectionType.QueuedConnection)

    @pyqtSlot(object)
    def _on_mutation(self, callback) -> None:
        """在主线程执行通过 safe_mutate 排队的变更回调。(C10)"""
        try:
            callback()
        except Exception:
            import logging

            logging.getLogger(__name__).exception("[AppContext] safe_mutate 回调执行异常")

    def safe_mutate(self, callback) -> None:
        """将回调调度到主线程执行，确保对共享状态（TranslationEntry、entry_labels 等）
        的写入不会与 UI 读取竞态。(C10)

        设计: 使用 mutation_requested 信号 + QueuedConnection，
        当从 ThreadPoolExecutor 工作线程调用时，回调自动排队到主线程事件循环。
        """
        self.mutation_requested.emit(callback)

    # ── helpers ───────────────────────────────────────────────

    def update_paratranz_members(self, project_id: int, members: list[dict]) -> None:
        """Apply a member response only to its project and notify permission views."""
        if self._current_project is None or self._current_project.get("id") != project_id:
            return
        self._current_project["_members"] = list(members)
        self.paratranz_permissions_changed.emit()

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
