"""Qt shell wiring for the canonical intent router and command discovery."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QDockWidget, QMessageBox

from transbridge.converter.translation_entry import STAGE_QUESTIONABLE
from transbridge.ui.drop_binding import SafeDropBinding
from transbridge.ui.drop_review import DropReviewDialog
from transbridge.ui.drop_router import DropResolutionStatus
from transbridge.ui.guidance.qt import GuidanceBinding

from .action_catalog import DEFAULT_ACTION_CATALOG, IntentId
from .command_palette import (
    CommandCandidateKind,
    CommandIntentRequest,
    CommandPaletteController,
    CommandPaletteModel,
    DynamicCommandCandidate,
)
from .command_palette_qt import CommandPaletteDialog
from .context_help import DEFAULT_CONTEXT_HELP, ContextHelpController
from .context_help_qt import ContextHelpPanel
from .intent_router import IntentDispatchResult, IntentRouter
from .menu_builder import MenuCallbacks
from .overlay_geometry import workspace_overlay_rect
from .task_center import TaskCenterController, TaskCenterPanel


def _call(callback: Callable[[], object]) -> Callable[[Mapping[str, str]], object]:
    return lambda _payload: callback()


class ShellIntentComposition:
    """Keep intent ownership out of MainWindow and expose one dispatch path."""

    def __init__(self, host) -> None:
        self._host = host
        self.router = IntentRouter()
        self._palette: CommandPaletteDialog | None = None
        self._shortcut: QShortcut | None = None
        self._help_dock: QDockWidget | None = None
        self._help_overlay_host_geometry: QRect | None = None
        self._guidance: GuidanceBinding | None = None
        self._task_dock: QDockWidget | None = None
        self._task_center: TaskCenterController | None = None
        self._task_overlay_host_geometry: QRect | None = None
        self._drop_binding: SafeDropBinding | None = None
        self._drop_review: DropReviewDialog | None = None
        self._register_handlers()

    def menu_callbacks(self) -> MenuCallbacks:
        callback = self.callback
        return MenuCallbacks(
            new_project=callback(IntentId.PROJECT_CREATE),
            open_project=callback(IntentId.PROJECT_OPEN),
            prepare_content=callback(IntentId.WORKBENCH_CONTENT_PREPARE),
            migrate=callback(IntentId.SOURCE_MIGRATE),
            upload=callback(IntentId.SYNC_UPLOAD),
            batch_upload=callback(IntentId.SYNC_UPLOAD_BATCH),
            download=callback(IntentId.SYNC_DOWNLOAD),
            batch_download=callback(IntentId.SYNC_DOWNLOAD_BATCH),
            write=callback(IntentId.PUBLISH_WRITE),
            batch_write=callback(IntentId.PUBLISH_WRITE_BATCH),
            new_variant=callback(IntentId.PROJECT_VARIANT_CREATE),
            copy_variant=callback(IntentId.PROJECT_VARIANT_COPY),
            save_snapshot=callback(IntentId.PROJECT_SNAPSHOT_SAVE),
            load_snapshot=callback(IntentId.PROJECT_SNAPSHOT_LOAD),
            delete_snapshot=callback(IntentId.PROJECT_SNAPSHOT_DELETE),
            export_transbridge=callback(IntentId.PROJECT_EXPORT),
            import_transbridge=callback(IntentId.PROJECT_IMPORT),
            refresh_projects=callback(IntentId.PROJECT_REFRESH),
            show_appearance=callback(IntentId.SETTINGS_APPEARANCE),
            show_config=callback(IntentId.SETTINGS_SERVICES),
            open_ai_translator=callback(IntentId.TRANSLATION_AI),
            toggle_smart_assistant=callback(IntentId.VIEW_SMART_ASSISTANT),
            open_dictionary=callback(IntentId.TRANSLATION_DICTIONARY),
            open_terminology=callback(IntentId.TERMINOLOGY_WORKBENCH),
            open_fomod=callback(IntentId.PUBLISH_FOMOD),
            show_user=callback(IntentId.SETTINGS_ACCOUNT),
            show_mails=callback(IntentId.SETTINGS_MESSAGES),
            show_about=callback(IntentId.HELP_ABOUT),
            manual_save=callback(IntentId.PROJECT_SAVE),
            show_context_help=callback(IntentId.HELP_CONTEXT),
            show_task_activity=callback(IntentId.TASK_OPEN_ACTIVITY),
            rename_project=callback(IntentId.PROJECT_RENAME),
            delete_project=callback(IntentId.PROJECT_DELETE),
            exit_app=callback(IntentId.APP_EXIT),
        )

    def start(self) -> None:
        self._host.workbench.intent_requested.connect(self.dispatch)
        model = CommandPaletteModel(
            self.router.all_availability,
            dynamic_source=self._dynamic_candidates,
        )
        self._palette = CommandPaletteDialog(CommandPaletteController(model), self._host)
        self._palette.intent_requested.connect(self._on_palette_request)
        self._shortcut = QShortcut(QKeySequence("Ctrl+K"), self._host)
        self._shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self._shortcut.activated.connect(self._palette.open_palette)
        runtime = self._host.app_runtime
        preferences = None
        if runtime is not None and "ui_preferences" in runtime.use_cases.names():
            preferences = runtime.use_cases.resolve("ui_preferences")
        self._guidance = GuidanceBinding(
            self._host.context,
            self._host.workbench.guidance_banner,
            self.dispatch,
            preferences=preferences,
            parent=self._host,
        )
        self._drop_binding = SafeDropBinding(self._host, parent=self._host)
        self._drop_binding.resolution_ready.connect(self._on_drop_resolution)
        self._drop_binding.intent_confirmed.connect(lambda intent_id, payload: self.dispatch(intent_id, payload))

    def callback(self, intent_id: IntentId) -> Callable[[], None]:
        return lambda: self.dispatch(intent_id)

    def dispatch(
        self,
        intent_id: IntentId | str,
        payload: Mapping[str, str] | None = None,
        *,
        confirmed: bool = True,
    ) -> IntentDispatchResult:
        result = self.router.dispatch(intent_id, payload, confirmed=confirmed)
        if not result.accepted and result.reason and not result.requires_confirmation:
            self._host.show_message(result.reason)
        return result

    def close(self) -> None:
        if self._palette is not None:
            self._palette.close()
        if self._help_dock is not None:
            self._help_dock.close()
            self._help_dock.deleteLater()
            self._help_dock = None
            self._help_overlay_host_geometry = None
        if self._guidance is not None:
            self._guidance.close()
        if self._task_center is not None:
            self._task_center.close()
        if self._task_dock is not None:
            self._task_dock.close()
            self._task_dock.deleteLater()
            self._task_dock = None
            self._task_overlay_host_geometry = None
        if self._drop_binding is not None:
            self._drop_binding.close()
        if self._drop_review is not None:
            self._drop_review.close()
        self.router.close()

    def _register_handlers(self) -> None:
        host = self._host
        register = self.router.register
        register(IntentId.PROJECT_CREATE, self._create_project)
        register(IntentId.PROJECT_OPEN, self._open_project)
        register(IntentId.PROJECT_SAVE, _call(host.variant_coordinator.manual_save), availability=self._has_project)
        register(IntentId.PROJECT_REFRESH, _call(host.tool_windows.refresh_projects))
        register(IntentId.PROJECT_RENAME, self._rename_project, availability=self._has_project)
        register(IntentId.PROJECT_DELETE, self._delete_project)
        register(
            IntentId.PROJECT_VARIANT_CREATE, _call(host.variant_coordinator.new_variant), availability=self._has_project
        )
        register(
            IntentId.PROJECT_VARIANT_COPY, _call(host.variant_coordinator.copy_variant), availability=self._has_project
        )
        register(
            IntentId.PROJECT_SNAPSHOT_SAVE,
            _call(host.project_transfer_coordinator.save_snapshot),
            availability=self._has_project,
        )
        register(
            IntentId.PROJECT_SNAPSHOT_LOAD,
            _call(host.project_transfer_coordinator.load_snapshot),
            availability=self._has_project,
        )
        register(
            IntentId.PROJECT_SNAPSHOT_DELETE,
            _call(host.project_transfer_coordinator.delete_snapshot),
            availability=self._has_project,
        )
        register(
            IntentId.PROJECT_EXPORT,
            _call(host.project_transfer_coordinator.export_transbridge),
            availability=self._has_project,
        )
        register(IntentId.PROJECT_IMPORT, self._import_project)
        register(IntentId.SOURCE_MIGRATE, self._migrate_source, availability=self._has_collection)
        register(
            IntentId.TRANSLATION_AI, _call(host.tool_windows.open_ai_translator), availability=self._has_collection
        )
        register(
            IntentId.TRANSLATION_AI_BATCH,
            _call(host.tool_windows.open_batch_ai_translation),
            availability=self._has_collection,
        )
        register(IntentId.TRANSLATION_DICTIONARY, _call(host.tool_windows.open_dictionary))
        register(
            IntentId.TERMINOLOGY_WORKBENCH,
            _call(host.tool_windows.open_terminology),
            availability=self._has_project,
        )
        register(IntentId.TRANSLATION_REVIEW, self._review, availability=self._has_review)
        register(IntentId.SYNC_UPLOAD, _call(host.operation_coordinator.upload), availability=self._has_cloud_context)
        register(
            IntentId.SYNC_UPLOAD_BATCH,
            _call(host.operation_coordinator.batch_upload),
            availability=self._has_cloud_context,
        )
        register(
            IntentId.SYNC_DOWNLOAD, _call(host.operation_coordinator.download), availability=self._has_cloud_context
        )
        register(
            IntentId.SYNC_DOWNLOAD_BATCH,
            _call(host.operation_coordinator.batch_download),
            availability=self._has_cloud_context,
        )
        register(IntentId.PUBLISH_WRITE, _call(host.operation_coordinator.write), availability=self._has_collection)
        register(
            IntentId.PUBLISH_WRITE_BATCH,
            _call(host.operation_coordinator.batch_write),
            availability=self._has_collection,
        )
        register(IntentId.PUBLISH_FOMOD, self._open_fomod)
        register(IntentId.WORKBENCH_MANAGE, self._manage_content, availability=self._has_project)
        register(
            IntentId.WORKBENCH_CONTENT_PREPARE,
            _call(host.parse_coordinator.parse_plugin),
            availability=self._has_project,
        )
        register(IntentId.VIEW_SMART_ASSISTANT, _call(host.tool_windows.toggle_smart_assistant))
        register(IntentId.SETTINGS_APPEARANCE, _call(host.tool_windows.show_ui_settings))
        register(IntentId.SETTINGS_SERVICES, _call(lambda: host.tool_windows.show_ui_settings("ai_service")))
        register(IntentId.SETTINGS_ACCOUNT, _call(host.tool_windows.show_user), availability=self._has_current_user)
        register(IntentId.SETTINGS_MESSAGES, _call(host.tool_windows.show_mails), availability=self._has_current_user)
        register(IntentId.TASK_OPEN_ACTIVITY, _call(self._show_task_center), availability=self._has_task_runtime)
        register(IntentId.TASK_RETRY, _call(self._show_task_center), availability=self._retry_from_task_center)
        register(IntentId.HELP_CONTEXT, _call(self._show_context_help))
        register(IntentId.HELP_ABOUT, _call(host.show_about))
        register(IntentId.APP_EXIT, _call(host.close))

    def _open_project(self, payload: Mapping[str, str]) -> object:
        path = payload.get("path")
        if path:
            return self._host.project_coordinator.open_project_path(path)
        return self._host.project_coordinator.open_project()

    def _rename_project(self, payload: Mapping[str, str]) -> object:
        return self._host.project_management_coordinator.rename_current(payload.get("name"))

    def _delete_project(self, payload: Mapping[str, str]) -> object:
        return self._host.project_management_coordinator.delete_project(
            payload.get("project_id"),
            payload.get("name"),
        )

    def _create_project(self, payload: Mapping[str, str]) -> object:
        path = payload.get("path")
        if path:
            return self._host.start_center_controller.choose_source_path(path)
        if payload.get("mode") == "plugin":
            return self._host.start_center_controller.choose_source()
        return self._host.start_center_controller.begin_creation()

    def _migrate_source(self, payload: Mapping[str, str]) -> object:
        path = payload.get("path")
        if path:
            return self._host.parse_coordinator.apply_migration(
                path,
                payload.get("drop_kind"),
                payload.get("format_id"),
            )
        return self._host.parse_coordinator.apply_migration()

    def _import_project(self, payload: Mapping[str, str]) -> object:
        path = payload.get("path")
        if path:
            return self._host.project_transfer_coordinator.import_transbridge(path)
        return self._host.project_transfer_coordinator.import_transbridge()

    def _open_fomod(self, payload: Mapping[str, str]) -> object:
        path = payload.get("path")
        if path:
            return self._host.tool_windows.open_fomod(path)
        return self._host.tool_windows.open_fomod()

    def _review(self, _payload: Mapping[str, str]) -> None:
        self._host.context.set_filter(stage=[STAGE_QUESTIONABLE])

    def _manage_content(self, payload: Mapping[str, str]) -> None:
        content_id = payload.get("content_id")
        if content_id and content_id in self._host.context.slots:
            self._host.context.activate_slot(content_id)
            return
        self._host.workbench.open_management_menu()

    def _has_project(self) -> tuple[bool, str | None]:
        enabled = bool(self._host.context.project_name)
        return enabled, None if enabled else "请先打开本地翻译工程"

    def _has_collection(self) -> tuple[bool, str | None]:
        enabled = self._host.context.collection is not None
        return enabled, None if enabled else "请先选择可编辑的翻译内容"

    def _has_review(self) -> tuple[bool, str | None]:
        collection = self._host.context.collection
        enabled = collection is not None and any(entry.stage == STAGE_QUESTIONABLE for entry in collection)
        return enabled, None if enabled else "当前没有待检查词条"

    def _has_cloud_project(self) -> tuple[bool, str | None]:
        project = self._host.context.current_project
        if project is None:
            return False, "请先选择 ParaTranz 云端项目"
        mine_ids = getattr(self._host.context, "mine_project_ids", ())
        if mine_ids and project.get("id") not in mine_ids:
            return False, "当前账户不是该 ParaTranz 项目的成员"
        return True, None

    def _has_current_user(self) -> tuple[bool, str | None]:
        enabled = self._host.context.current_user is not None
        return enabled, None if enabled else "请先配置 API Token 并验证 ParaTranz 账户"

    def _has_cloud_context(self) -> tuple[bool, str | None]:
        content, content_reason = self._has_collection()
        if not content:
            return False, content_reason
        return True, None

    def _has_task_runtime(self) -> tuple[bool, str | None]:
        enabled = self._host.app_runtime is not None and self._host.runtime_context is not None
        return enabled, None if enabled else "当前入口未连接任务运行时"

    @staticmethod
    def _retry_from_task_center() -> tuple[bool, str | None]:
        return False, "请在任务活动中选择明确支持重试的失败任务"

    def _dynamic_candidates(self) -> tuple[DynamicCommandCandidate, ...]:
        output: list[DynamicCommandCandidate] = []
        runtime = self._host.app_runtime
        if runtime is not None and "project_catalog" in runtime.use_cases.names():
            for project in runtime.use_cases.resolve("project_catalog").list_projects().projects:
                output.append(
                    DynamicCommandCandidate(
                        f"project:{project.project_id}",
                        CommandCandidateKind.RECENT_PROJECT,
                        f"继续 {project.name}",
                        IntentId.PROJECT_OPEN,
                        aliases=("最近工程", project.name),
                        payload={"path": project.path},
                        stale_reason=project.reason,
                    )
                )
        for content_id, slot in self._host.context.slots.items():
            output.append(
                DynamicCommandCandidate(
                    f"content:{content_id}",
                    CommandCandidateKind.TRANSLATION_CONTENT,
                    f"打开 {slot.label or content_id}",
                    IntentId.WORKBENCH_MANAGE,
                    aliases=("翻译内容",),
                    payload={"content_id": content_id},
                )
            )
        return tuple(output)

    def _on_palette_request(self, request: CommandIntentRequest) -> None:
        confirmed = True
        if request.requires_confirmation:
            descriptor = DEFAULT_ACTION_CATALOG.get(request.intent_id)
            confirmed = (
                QMessageBox.question(
                    self._host,
                    "确认打开操作",
                    f"要继续“{descriptor.label}”并检查影响范围吗？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                is QMessageBox.StandardButton.Yes
            )
        if confirmed:
            self.dispatch(request.intent_id, request.payload, confirmed=True)

    def _show_context_help(self) -> None:
        if self._help_dock is None:
            controller = ContextHelpController(DEFAULT_CONTEXT_HELP)
            panel = ContextHelpPanel(controller)
            dock = QDockWidget("功能与术语帮助", self._host)
            dock.setObjectName("context-help")
            flags = dock.windowFlags()
            flags &= ~Qt.WindowType.WindowType_Mask
            dock.setWindowFlags(flags | Qt.WindowType.Window)
            dock.setAllowedAreas(Qt.DockWidgetArea.NoDockWidgetArea)
            dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable)
            dock.setWidget(panel)
            panel.close_requested.connect(dock.hide)
            self._help_dock = dock
        panel = self._help_dock.widget()
        topic = "plugin" if self._host.context.collection is not None else "local-project"
        context_id = self._host.context.active_project_id or "start-center"
        panel.show_topic(topic, context_identity=str(context_id))

        host_geometry = QRect(self._host.frameGeometry())
        if self._help_overlay_host_geometry != host_geometry:
            overlay_rect = workspace_overlay_rect(self._host.rect())
            self._help_dock.resize(overlay_rect.size())
            self._help_dock.move(self._host.mapToGlobal(overlay_rect.topLeft()))
            self._help_overlay_host_geometry = host_geometry
        self._help_dock.show()
        self._help_dock.raise_()
        self._help_dock.activateWindow()

    def _show_task_center(self) -> None:
        if self._task_dock is None:
            panel = TaskCenterPanel()
            controller = TaskCenterController(
                self._host.app_runtime,
                self._host.runtime_context,
                panel,
                parent=self._host,
            )
            dock = QDockWidget("任务活动", self._host)
            dock.setObjectName("task-activity")
            flags = dock.windowFlags()
            flags &= ~Qt.WindowType.WindowType_Mask
            dock.setWindowFlags(flags | Qt.WindowType.Window)
            dock.setAllowedAreas(Qt.DockWidgetArea.NoDockWidgetArea)
            dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable)
            dock.setWidget(panel)
            self._task_dock = dock
            self._task_center = controller
            controller.navigation_requested.connect(self._dispatch_task_navigation)
            controller.start()
        else:
            self._task_center.refresh_catalogs()

        host_geometry = QRect(self._host.frameGeometry())
        if self._task_overlay_host_geometry != host_geometry:
            overlay_rect = workspace_overlay_rect(self._host.rect())
            self._task_dock.resize(overlay_rect.size())
            self._task_dock.move(self._host.mapToGlobal(overlay_rect.topLeft()))
            self._task_overlay_host_geometry = host_geometry
        self._task_dock.show()
        self._task_dock.raise_()
        self._task_dock.activateWindow()

    def _dispatch_task_navigation(self, intent) -> None:
        try:
            self.dispatch(intent.target, dict(intent.parameters))
        except (KeyError, TypeError, ValueError) as exc:
            self._host.show_message(f"任务结果入口当前不可用：{exc}")

    def _on_drop_resolution(self, resolution) -> None:
        if resolution.status not in {
            DropResolutionStatus.CANDIDATE,
            DropResolutionStatus.REJECTED,
            DropResolutionStatus.NEEDS_CHOICE,
        }:
            return
        if self._drop_review is None:
            self._drop_review = DropReviewDialog(self._host)
            self._drop_review.confirm_requested.connect(self._drop_binding.confirm)
            self._drop_review.dismiss_requested.connect(self._drop_binding.dismiss)
        self._drop_review.review(resolution)
        self._drop_review.exec()


__all__ = ["ShellIntentComposition"]
