from pathlib import Path as PathLib

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QFileDialog, QMessageBox

from transbridge.application.projects.source_registry import plugin_source_location, select_workbench_source
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.persistence import (
    PERSISTENCE_ROOT,
    ProjectHandle,
    VariantStore,
    WorkspaceState,
    workspace_path,
)

from ..context import CollectionSlot
from ..workers import ApiWorker


class ProjectCoordinator:
    """Own one application-shell interaction slice."""

    def __init__(self, host) -> None:
        self._host = host

    def init_workspace(self, *, initial_project_path: str | None = None):
        """启动时读取 workspace.json，恢复上次项目+版本。"""
        ws_path = workspace_path()
        ws = WorkspaceState.load(ws_path)
        self._host.context.workspace = ws

        if self._host.context.uses_authoritative_projection:
            if self._host.current_project_opener is None or self._host.runtime_context is None:
                self._show_start_center_failure(
                    "PROJECT_OPENER_UNAVAILABLE",
                    "当前工程打开服务不可用。",
                )
                return
            if initial_project_path:
                if hasattr(self._host, "show_start_center_restoring"):
                    self._host.show_start_center_restoring()
                self._start_current_project_open(
                    lambda: self._host.current_project_opener.prepare_path(
                        initial_project_path,
                        self._host.runtime_context,
                    ),
                    dirty_decision=None,
                    success_verb="已打开",
                    show_error_dialog=False,
                    on_success=lambda _opened: self._show_workbench(),
                    on_failure=self._show_start_center_failure,
                )
                return
            if not getattr(self._host.current_project_opener, "has_active_reference", True):
                self._show_start_center_empty()
                return
            from transbridge.application.projects import DirtyDecision

            if hasattr(self._host, "show_start_center_restoring"):
                self._host.show_start_center_restoring()
            self._start_current_project_open(
                lambda: self._host.current_project_opener.prepare_active(self._host.runtime_context),
                dirty_decision=DirtyDecision.SAVE,
                success_verb="已恢复",
                show_error_dialog=False,
                on_success=lambda _opened: self._show_workbench(),
                on_failure=self._show_start_center_failure,
            )
            return

        if initial_project_path:
            self.open_project_path(initial_project_path)
            return

        active = ws.active_project
        if not active or active not in ws.projects:
            self._host.show_message("就绪 — 无活跃项目，请新建或打开项目")
            self._show_start_center_empty()
            return

        proj_path = PathLib(ws.projects[active])
        filter_state = ws.last_session.get("filter_state", {})

        def _prepare_restore():
            project = ProjectHandle.load(proj_path)
            if not project.name:
                raise ValueError(f"上次项目「{active}」的配置文件不存在或已损坏")
            variant_store = None
            variant_name = project.active_variant
            if variant_name and project.has_variant(variant_name):
                variant_store = VariantStore.load(project.variant_dir(variant_name) / "current.json")
            return project, variant_store

        def _activate_restore(result) -> None:
            project, variant_store = result
            self._host.context.active_project = project
            self._host.context.active_variant = project.active_variant
            self._host.context.variant_store = variant_store
            if variant_store is not None and self._host.context.collection:
                count = variant_store.apply_to(list(self._host.context.collection))
                self._host.show_message(
                    f"项目「{project.name}」已恢复，版本「{project.active_variant}」，恢复 {count} 条译文"
                )
            self._restore_plugin_sources(project.sources)
            if filter_state:
                QTimer.singleShot(3000, lambda: self._apply_saved_filter_state(filter_state))
            self._show_workbench()

        self._host.start_foreground_task(
            _prepare_restore,
            message="正在恢复上次项目…",
            on_result=_activate_restore,
            on_error=lambda error: self._show_start_center_failure(
                "LEGACY_PROJECT_RESTORE_FAILED",
                error,
            ),
        )

    def save_workspace_session(self):
        """关闭前保存会话状态。"""
        ctx = self._host.context
        ws = ctx.workspace
        if ws is None:
            return
        if ctx.active_project:
            ws.active_project = ctx.active_project.name
            ws.last_session["project"] = ctx.active_project.name
            ws.last_session["variant"] = ctx.active_variant
        # 保存筛选状态
        ws.last_session["filter_state"] = self._host.workbench.preview.get_filter_state()

    def _apply_saved_filter_state(self, filter_state: dict):
        """恢复持久化的筛选状态。"""
        if filter_state:
            self._host.workbench.preview.apply_filter_state(filter_state)

    def new_project(self):
        """弹出新建项目对话框。"""
        if self._host.context.uses_authoritative_projection:
            if hasattr(self._host, "show_start_center"):
                self._host.show_start_center(user_requested=True)
            else:
                self._host.show_message("Legacy Project creation is disabled under the V2 authority migration gate.")
            return
        from PyQt6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(self._host, "新建项目", "项目名称:")
        if not ok or not name.strip():
            return
        name = name.strip()
        proj_dir = PERSISTENCE_ROOT / name
        if proj_dir.exists():
            QMessageBox.warning(self._host, "冲突", f"项目「{name}」已存在")
            return

        proj = ProjectHandle.create(PERSISTENCE_ROOT, name)
        proj.add_variant("默认")
        proj.active_variant = "默认"
        proj.save()

        # 创建默认版本的 current.json
        from transbridge.persistence import VariantStore

        vs = VariantStore(proj.variant_dir("默认") / "current.json")
        vs.save()

        ws = self._host.context.workspace or WorkspaceState.load(workspace_path())
        ws.add_project(name, proj.config_path)
        ws.save()
        self._host.context.workspace = ws
        self._host.context.active_project = proj
        self._host.context.active_variant = "默认"
        self._host.context.variant_store = vs

        QMessageBox.information(
            self._host, "项目已创建", f"项目「{name}」已创建，默认版本「默认」。\n请通过文件菜单解析插件或导入 JSON。"
        )

    def open_project(self):
        """弹出打开项目对话框。"""
        from transbridge.persistence.current_project import PROJECT_FILE_FILTER

        initial_directory = (
            str(PERSISTENCE_ROOT)
            if self._host.current_project_opener is None
            else self._host.current_project_opener.directory
        )
        path, _ = QFileDialog.getOpenFileName(
            self._host,
            "打开项目文件",
            initial_directory,
            PROJECT_FILE_FILTER,
        )
        if not path:
            return
        self.open_project_path(path)

    def open_project_path(self, path: str) -> None:
        """Open a selected/recent Project through the same lifecycle intent."""

        if (
            getattr(self._host.context, "project_opening", False)
            or getattr(self._host, "project_open_worker", None) is not None
        ):
            self._host.show_message("已有项目正在后台打开，请稍候。")
            return
        if self._host.context.uses_authoritative_projection:
            if self._host.current_project_opener is None or self._host.runtime_context is None:
                self._host.show_message("当前项目打开服务不可用。")
                return
            accepted, dirty_decision = self.confirm_project_switch()
            if not accepted:
                return
            self._start_current_project_open(
                lambda: self._host.current_project_opener.prepare_path(path, self._host.runtime_context),
                dirty_decision=dirty_decision,
                success_verb="已打开",
                on_success=lambda _opened: self._show_workbench(),
            )
            return

        def _prepare_legacy_project():
            project = ProjectHandle.load(PathLib(path))
            variant_store = None
            variant_name = project.active_variant
            if variant_name and project.has_variant(variant_name):
                variant_store = VariantStore.load(project.variant_dir(variant_name) / "current.json")
            return project, variant_store

        def _activate_legacy(result) -> None:
            project, variant_store = result
            workspace = self._host.context.workspace or WorkspaceState.load(workspace_path())
            workspace.add_project(project.name, PathLib(path))
            workspace.save()
            self._host.context.workspace = workspace
            self._host.context.active_project = project
            self._host.context.active_variant = project.active_variant
            self._host.context.variant_store = variant_store
            if variant_store is not None and self._host.context.collection:
                variant_store.apply_to(list(self._host.context.collection))
            self._restore_plugin_sources(project.sources)
            self._show_workbench()

        def _start_legacy_open(saved: bool) -> None:
            if saved:
                self._host.start_foreground_task(
                    _prepare_legacy_project,
                    message="正在加载项目…",
                    on_result=_activate_legacy,
                    on_error=lambda error: QMessageBox.warning(
                        self._host,
                        "无法读取项目文件",
                        error,
                    ),
                )

        self._host.save_current_project_async(on_finished=_start_legacy_open)

    def confirm_project_switch(self):
        """Use the same save/discard/cancel decision for every Project navigation."""
        from transbridge.application.projects import DirtyDecision

        if not self._host.context.dirty:
            return True, None
        answer = QMessageBox.question(
            self._host,
            "保存确认",
            "当前工程版本有未保存修改。切换前是否保存？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Cancel:
            return False, None
        return True, DirtyDecision.SAVE if answer == QMessageBox.StandardButton.Yes else DirtyDecision.DISCARD

    def open_project_identity(self, context, on_finished) -> None:
        """Open an assistant's exact binding through the normal background transaction."""
        accepted, decision = self.confirm_project_switch()
        if not accepted:
            on_finished(False)
            return
        completed = False

        def finish(success):
            nonlocal completed
            if completed:
                return
            completed = True
            matches = self._host.context.active_version_identity == (context.project_id, context.variant_id)
            ready = success and matches and not getattr(self._host.context, "project_view_failed", False)
            if ready:
                self._show_workbench()
            on_finished(ready)

        started = self._start_current_project_open(
            lambda: self._host.current_project_opener.prepare_identity(context.project_id, context.variant_id, context),
            dirty_decision=decision,
            success_verb="已打开",
            on_success=lambda _opened: finish(True),
            on_failure=lambda _code, _message: finish(False),
            request_context=context,
        )
        if not started:
            finish(False)

    def _start_current_project_open(
        self,
        prepare,
        *,
        dirty_decision,
        success_verb: str,
        show_error_dialog: bool = True,
        on_success=None,
        on_failure=None,
        request_context=None,
    ) -> bool:
        """Run preparation, activation and detached view construction in one worker."""
        from .project_open_operation import ProjectOpenOperation

        if not hasattr(self, "_project_open"):
            self._project_open = ProjectOpenOperation(self._host, self._restore_plugin_sources, self.open_project_path)
        return self._project_open.start(
            prepare,
            dirty_decision=dirty_decision,
            success_verb=success_verb,
            show_error_dialog=show_error_dialog,
            on_success=on_success,
            on_failure=on_failure,
            request_context=request_context,
        )

    def _show_start_center_empty(self) -> None:
        if hasattr(self._host, "show_start_center_empty"):
            self._host.show_start_center_empty()

    def _show_start_center_failure(self, code: str, message: str) -> None:
        self._host.show_message(f"{code}: {message}")
        if hasattr(self._host, "show_start_center_recovery_failed"):
            self._host.show_start_center_recovery_failed(code, message)

    def _show_workbench(self) -> None:
        if hasattr(self._host, "show_workbench"):
            self._host.show_workbench()

    def _restore_plugin_sources(self, sources, hydrations=None) -> str | None:
        if hydrations is not None:
            from transbridge.ui.source_hydration import apply_variant_projection, slot_from_hydration

            projection = self._host.app_runtime.use_cases.resolve("project_projection").snapshot()
            states = () if projection is None else projection.to_dict()["values"].get("entries", ())
            prepared_slots = []
            try:
                for hydration in hydrations:
                    slot = slot_from_hydration(hydration)
                    slot.collection = apply_variant_projection(slot.collection, states)
                    prepared_slots.append((hydration.location, slot))
            except Exception as exc:  # noqa: BLE001 - opening must surface a recoverable source error
                message = f"工程来源界面数据恢复失败：{exc}。请重新打开工程或检查来源文件。"
                self._host.show_message(message)
                return message
            for key in tuple(self._host.context.slots):
                self._host.context.remove_slot(key)
            for location, slot in prepared_slots:
                self._host.context.add_slot(location, slot)
            return None
        source = select_workbench_source(sources)
        location = plugin_source_location(source)
        if location is not None:
            self.restore_parse_esp(location)
        return None

    def restore_active_sources(self) -> None:
        """Rehydrate every source after a Variant change without reactivating it."""
        projection = self._host.app_runtime.use_cases.resolve("project_projection")
        expected = projection.snapshot()
        if expected is None:
            return

        def _restore(prepared) -> None:
            current = projection.snapshot()
            if current != expected:
                self._host.show_message("工程版本已变化，已忽略过期的来源加载结果。请重新打开当前工程。")
                return
            if not prepared.is_success or prepared.value is None:
                message = "；".join(item.message for item in prepared.diagnostics)
                self._host.show_message(f"工程来源恢复失败：{message}。请重新打开工程。")
                return
            value = prepared.value
            if value.recovery is not None:
                self._host.show_message("工程来源不可用，工作台已清空。请重新打开工程进入恢复视图。")
                return
            self._restore_plugin_sources(value.sources, value.hydrations)

        started = self._host.start_foreground_task(
            lambda: self._host.current_project_opener.prepare_active(self._host.runtime_context),
            message="正在恢复当前版本的全部来源…",
            on_result=_restore,
        )
        if started:
            # A failed/late hydration must never leave the previous Variant editable.
            for key in tuple(self._host.context.slots):
                self._host.context.remove_slot(key)

    def restore_parse_esp(self, esp_path: str, *, hydration=None):
        """后台解析 ESP 源文件（启动恢复用，不阻塞 UI）。"""
        from transbridge.parser.plugin_parser import PluginParser
        from transbridge.ui.source_hydration import apply_variant_projection, slot_from_hydration

        if hydration is not None:
            try:
                projection = self._host.app_runtime.use_cases.resolve("project_projection").snapshot()
                states = () if projection is None else projection.to_dict()["values"].get("entries", ())
                slot = slot_from_hydration(hydration)
                slot.collection = apply_variant_projection(slot.collection, states)
                self._host.context.add_slot(hydration.location, slot)
            except Exception as exc:  # noqa: BLE001 - compatibility callers need an actionable error
                self._host.show_message(f"工程来源界面数据恢复失败：{exc}。请重新打开工程或检查来源文件。")
            return

        self._host.workbench.show_step2_progress(0, f"解析中: {PathLib(esp_path).name}…")

        def _do():
            parser = PluginParser()
            parsed_entries = parser.parse_plugin(PathLib(esp_path))
            collection = TranslationEntryCollection(parsed_entries)
            if self._host.context.uses_authoritative_projection:
                projection = self._host.app_runtime.use_cases.resolve("project_projection").snapshot()
                states = () if projection is None else projection.to_dict()["values"].get("entries", ())
                collection = apply_variant_projection(collection, states)
            return collection, parser.get_plugin()

        def _on_done(result):
            collection, plugin = result
            try:
                # 应用已缓存的翻译数据（必须在 add_slot 之前，否则 collection_changed 触发时表格仍为空）
                if self._host.context.variant_store:
                    self._host.context.variant_store.apply_to(list(collection))
                label = PathLib(esp_path).stem
                slot = CollectionSlot(
                    label=label,
                    collection=collection,
                    esp_path=esp_path,
                    plugin=plugin,
                )
                self._host.context.add_slot(esp_path, slot)
            finally:
                self._host.workbench.hide_step2_progress()

        def _on_error(msg: str):
            self._host.workbench.hide_step2_progress()

        w = ApiWorker(_do)
        w.result.connect(_on_done)
        w.error.connect(_on_error)
        w.start()
        self._host.workers.append(w)
