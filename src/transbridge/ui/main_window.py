import logging

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QMainWindow, QMessageBox

from transbridge import __version__

from .coordinators import (
    OperationCoordinator,
    ParseCoordinator,
    ProjectCoordinator,
    ProjectManagementCoordinator,
    ProjectTransferCoordinator,
    SourceUpdateCoordinator,
    VariantCoordinator,
)
from .foundation.adapters import ThemeView
from .foundation.components import ComponentKind, ComponentStyle
from .shell.action_catalog import IntentId
from .shell.intent_composition import ShellIntentComposition
from .shell.menu_builder import MenuBuilder
from .shell.navigation_rail import WorkspaceShell
from .shell.progressive_menu_bar import ProgressiveMenuBar
from .shell.start_center import StartCenterWidget
from .shell.start_center_controller import StartCenterController
from .shell.status_presenter import StatusPresenter
from .shell.window_lifecycle import WindowLifecycle
from .workbench.widget import WorkbenchWidget
from .workers import ApiWorker, get_http_error_bus


def _composition_port(field: str) -> property:
    """Expose one documented MainWindow composition dependency."""

    return property(
        lambda instance: getattr(instance, field),
        lambda instance, value: setattr(instance, field, value),
    )


class MainWindow(QMainWindow):
    context = _composition_port("_ctx")
    workbench = _composition_port("_workbench")
    app_runtime = _composition_port("_app_runtime")
    runtime_context = _composition_port("_runtime_context")
    project_commands = _composition_port("_project_commands")
    current_project_opener = _composition_port("_current_project_opener")
    operation_menu = _composition_port("_menu")
    upload_card = _composition_port("_card_upload")
    download_card = _composition_port("_card_download")
    write_card = _composition_port("_card_write")
    mode_tabs = _composition_port("_mode_tabs")
    paratranz_widget = _composition_port("_pt_widget")
    session_commands = _composition_port("_session_commands")
    session_projection = _composition_port("_session_projection")
    workers = _composition_port("_workers")
    foreground_worker = _composition_port("_foreground_worker")
    project_open_worker = _composition_port("_project_open_worker")
    save_worker = _composition_port("_save_worker")
    project_coordinator = _composition_port("_project_coordinator")
    project_management_coordinator = _composition_port("_project_management_coordinator")
    source_update_coordinator = _composition_port("_source_update_coordinator")
    parse_coordinator = _composition_port("_parse_coordinator")
    operation_coordinator = _composition_port("_operation_coordinator")
    operation_plan_facade = _composition_port("_operation_plan_facade")
    project_transfer_coordinator = _composition_port("_project_transfer_coordinator")
    variant_coordinator = _composition_port("_variant_coordinator")
    tool_windows = _composition_port("_tool_windows")
    status_presenter = _composition_port("_status_presenter")
    close_pending = _composition_port("_close_pending")
    close_ready = _composition_port("_close_ready")
    start_center = _composition_port("_start_center")
    central_stack = _composition_port("_central_stack")
    guided_project_coordinator = _composition_port("_guided_project_coordinator")
    start_center_controller = _composition_port("_start_center_controller")
    ui_foundation = _composition_port("_ui_foundation")
    theme_view = _composition_port("_theme_view")

    def __init__(
        self,
        app_context=None,
        runtime=None,
        runtime_context=None,
        ui_foundation=None,
        initial_project_path: str | None = None,
        initial_import_path: str | None = None,
    ):
        super().__init__()
        self.setWindowTitle("TransBridge")
        self.resize(1280, 820)

        if app_context is None or not app_context.uses_authoritative_projection:
            raise ValueError("MainWindow requires the current project projection")
        if runtime is None or runtime_context is None:
            raise ValueError("MainWindow requires the application runtime and request context")
        self._ctx = app_context
        if (
            app_context is not None
            and hasattr(app_context, "parent")
            and app_context.parent() is None
            and hasattr(app_context, "setParent")
        ):
            app_context.setParent(self)
        self._app_runtime = runtime
        self._runtime_context = runtime_context
        self._ui_foundation = ui_foundation
        self._theme_view = (
            None
            if ui_foundation is None
            else ThemeView(ui_foundation.theme, domain_brush_cache=ui_foundation.domain_brush_cache)
        )
        self._project_commands = None if runtime is None else runtime.use_cases.resolve("gui_project_commands")
        self._current_project_opener = None if runtime is None else runtime.use_cases.resolve("current_project_opener")
        self._session_commands = None if runtime is None else runtime.use_cases.resolve("gui_session_commands")
        self._session_projection = None if runtime is None else runtime.use_cases.resolve("session_projection")
        self._terminology_profile_factory = None
        if runtime is not None and "terminology_profile_service_factory" in runtime.use_cases.names():
            self._terminology_profile_factory = runtime.use_cases.resolve("terminology_profile_service_factory")
        self._workers: list[ApiWorker] = []
        self._foreground_worker: ApiWorker | None = None
        self._project_open_worker: ApiWorker | None = None
        self._save_worker: ApiWorker | None = None
        self._save_callbacks: list = []
        self._close_pending = False
        self._close_ready = False
        from .shell.tool_windows import ToolWindows

        self._tool_windows = ToolWindows(self)
        self._parse_coordinator = ParseCoordinator(self)
        self._operation_coordinator = OperationCoordinator(self)
        self._project_coordinator = ProjectCoordinator(self)
        self._project_management_coordinator = ProjectManagementCoordinator(self)
        self._source_update_coordinator = SourceUpdateCoordinator(self)
        self._variant_coordinator = VariantCoordinator(self)
        self._project_transfer_coordinator = ProjectTransferCoordinator(self)
        self._operation_plan_facade = None
        if runtime is not None and runtime_context is not None:
            from .operations.production import build_operation_plan_facade

            self._operation_plan_facade = build_operation_plan_facade(runtime, runtime_context)
        self._intent_composition = ShellIntentComposition(self)

        self._setup_op_cards()
        progressive_menu = ProgressiveMenuBar(self)
        self.setMenuBar(progressive_menu)
        self._menu_builder = MenuBuilder(
            self,
            self._intent_composition.menu_callbacks(),
        )
        self._menu = self._menu_builder.build()
        ComponentStyle.apply_static(progressive_menu, ComponentKind.MENU)
        progressive_menu.bind_existing_menus()
        self._init_central()
        self._init_start_center_controller()
        self._status_presenter = StatusPresenter(self, self._ctx)
        self._status_presenter.start()
        self._mode_tabs.navigation.set_user(self._ctx.current_user)
        self._ctx.user_changed.connect(self._mode_tabs.navigation.set_user)
        self._intent_composition.start()
        self._user_label = self._status_presenter.user_label
        self._project_label = self._status_presenter.project_label
        self._api_indicator = self._status_presenter.api_indicator
        self._msg_label = self._status_presenter.message_label

        self._ctx.collection_changed.connect(self._on_collection_changed)
        self._ctx.collection_list_changed.connect(self._on_collection_list_changed)
        self._ctx.project_selected.connect(lambda _project: self._operation_coordinator.update_operation_menu_state())
        self._ctx.navigate_to.connect(self._on_navigate_to)

        get_http_error_bus().http_error.connect(self._on_http_error)

        # Optional remote-service configuration must never block the local
        # startup/restore journey.  Relevant actions expose their own repair UI.
        if self._ctx.config.token:
            self._tool_windows.load_current_user()

        self._window_lifecycle = WindowLifecycle(self)
        self._window_lifecycle.restore_state()
        self._project_coordinator.init_workspace(initial_project_path=initial_project_path)
        if initial_import_path:
            QTimer.singleShot(0, lambda: self._open_initial_import_when_idle(initial_import_path))

        # 自动保存 — 编辑操作触发防抖
        self._window_lifecycle.start()
        self._auto_saver = self._window_lifecycle.auto_saver
        self._ctx.dirty_changed.connect(lambda: self._workbench.project_bar.set_save_dirty(self._ctx.dirty))

    def _open_initial_import_when_idle(self, path: str) -> None:
        workers = (self._foreground_worker, self._save_worker, self._project_open_worker)
        if getattr(self._ctx, "project_opening", False) or any(worker is not None for worker in workers):
            QTimer.singleShot(100, lambda: self._open_initial_import_when_idle(path))
            return
        self._project_transfer_coordinator.import_transbridge(path)

    def closeEvent(self, event):
        if not self._close_pending and not self._dialogue_editor.can_close():
            event.ignore()
            return
        if self._window_lifecycle.close_event(event):
            project_saves = getattr(self, "_project_save_coordinator", None)
            if project_saves is not None:
                project_saves.close()
            self._dialogue_editor.close()
            self._intent_composition.close()
            if self._theme_view is not None:
                self._theme_view.close()
            super().closeEvent(event)

    # ── Operation cards (hidden, logic-only) ───────────────────

    def _setup_op_cards(self):
        from .workbench.cards.download_card import DownloadCard
        from .workbench.cards.upload_card import UploadCard
        from .workbench.cards.write_card import WriteCard

        self._card_upload = UploadCard(
            self._ctx,
            self._operation_coordinator.run_worker,
            parent=self,
            theme_view=self._theme_view,
        )
        self._card_upload.setVisible(False)
        self._card_download = DownloadCard(
            self._ctx,
            self._operation_coordinator.run_worker,
            parent=self,
            theme_view=self._theme_view,
        )
        self._card_download.setVisible(False)
        self._card_write = WriteCard(
            self._ctx,
            self._operation_coordinator.run_worker,
            parent=self,
            theme_view=self._theme_view,
        )
        self._card_write.setVisible(False)
        if self._operation_plan_facade is not None:
            self._card_upload.bind_operation_plan_facade(self._operation_plan_facade)
            self._card_download.bind_operation_plan_facade(self._operation_plan_facade)
            self._card_write.bind_operation_plan_facade(self._operation_plan_facade)

    # ── Central widget ────────────────────────────────────────

    def _init_central(self):
        from .paratranz.widget import ParaTranzWidget
        from .tools.ai_translator.task_registry import project_directory

        self._mode_tabs = WorkspaceShell(self, theme_view=self._theme_view)
        self._mode_tabs.intent_requested.connect(self._intent_composition.dispatch)

        self._workbench = WorkbenchWidget(
            self._ctx,
            theme_view=self._theme_view,
            terminology_profile_factory=self._terminology_profile_factory,
            project_directory_provider=lambda: project_directory(self._ctx, self._app_runtime, self._runtime_context),
        )
        self._pt_widget = ParaTranzWidget(self._ctx, theme_view=self._theme_view)

        # 连接 ProjectBar 信号
        pb = self._workbench.project_bar
        pb.new_project_requested.connect(self._intent_composition.callback(IntentId.PROJECT_CREATE))
        pb.open_project_requested.connect(self._intent_composition.callback(IntentId.PROJECT_OPEN))
        pb.source_update_requested.connect(self._intent_composition.callback(IntentId.PROJECT_SOURCE_UPDATE))
        pb.variant_switch_requested.connect(self._variant_coordinator.switch_variant)
        pb.save_requested.connect(self._intent_composition.callback(IntentId.PROJECT_SAVE))
        pb.variant_add_requested.connect(self._intent_composition.callback(IntentId.PROJECT_VARIANT_CREATE))
        pb.variant_copy_requested.connect(self._intent_composition.callback(IntentId.PROJECT_VARIANT_COPY))
        pb.variant_delete_requested.connect(self._variant_coordinator.delete_variant)
        pb.project_rename_requested.connect(
            lambda name: self._intent_composition.dispatch(IntentId.PROJECT_RENAME, {"name": name})
        )
        pb.project_delete_requested.connect(
            lambda project_id, name: self._intent_composition.dispatch(
                IntentId.PROJECT_DELETE,
                {"project_id": project_id, "name": name},
            )
        )
        pb.snapshot_delete_requested.connect(self._intent_composition.callback(IntentId.PROJECT_SNAPSHOT_DELETE))

        self._mode_tabs.addTab(self._workbench, "工作台")
        self._mode_tabs.addTab(self._pt_widget, "ParaTranz 管理")
        self._start_center = StartCenterWidget(self)
        self._mode_tabs.addTab(self._start_center, "开始")
        from .dialogue.controller import DialogueEditorController

        self._dialogue_editor = DialogueEditorController(
            self._ctx,
            self,
            self._workbench.preview,
            self._workers,
            projection=(self._app_runtime.use_cases.resolve("project_projection") if self._app_runtime else None),
        )
        # Compatibility port for callers that still need the underlying page
        # stack; WorkspaceShell remains the one visible application shell.
        self._central_stack = self._mode_tabs.pages
        self.setCentralWidget(self._mode_tabs)

    def _init_start_center_controller(self) -> None:
        self._start_center_controller = StartCenterController(
            self,
            self._start_center,
            dispatch=self._intent_composition.dispatch,
        )
        self._start_center_controller.start()
        self._mode_tabs.start_requested.connect(
            lambda: self._start_center_controller.show(user_requested=bool(self._ctx.project_name))
        )
        self._guided_project_coordinator = self._start_center_controller.guided_project

    def show_start_center_restoring(self) -> None:
        self._start_center_controller.show_restoring()

    def show_start_center_empty(self) -> None:
        self._start_center_controller.show_empty()

    def show_start_center_recovery_failed(self, code: str, message: str) -> None:
        self._start_center_controller.show_recovery_failed(code, message)

    def show_start_center(self, *, user_requested: bool = False) -> None:
        self._start_center_controller.show(user_requested=user_requested)

    def show_workbench(self) -> None:
        self._start_center_controller.show_workbench()

    def show_project_open_progress(self, message: str) -> None:
        self._start_center.set_project_opening(message)

    def hide_project_open_progress(self) -> None:
        self._start_center.set_project_opening(None)

    # ── Status bar ────────────────────────────────────────────

    # ── Context signal handlers ───────────────────────────────

    def _on_http_error(self, status: int, message: str):
        if status == 401:
            self.show_message("Token 已失效，请重新配置")
            self._tool_windows.show_config()
        elif status == 403:
            self.show_message("权限不足，无法执行此操作")

    def _on_navigate_to(self, index: int):
        self.show_workbench()
        self._mode_tabs.setCurrentIndex(index)
        if index == 1:
            self._pt_widget.switch_to_mine()

    def _on_collection_changed(self, collection):
        if collection:
            self.show_message(f"集合已加载，共 {len(collection)} 条词条")
        self._menu.migrate.setEnabled(bool(self._ctx.slots))
        self._operation_coordinator.update_operation_menu_state()

    def _on_collection_list_changed(self):
        self._menu.migrate.setEnabled(bool(self._ctx.slots))
        self._operation_coordinator.update_operation_menu_state()

    def show_message(self, msg: str, *, error=False, retry=None, retry_label="重试"):
        self._status_presenter.show_message(msg, error=error, retry=retry, retry_label=retry_label)

    # ── Parse actions ─────────────────────────────────────────

    # ── Parse implementation ──────────────────────────────────

    # ── Migration implementation ───────────────────────────────

    # ── Operation menu state ──────────────────────────────────

    # ── Operation actions ─────────────────────────────────────

    # ── Operation worker helper (proxies to Step2 progress) ────

    # ── Existing actions ──────────────────────────────────────

    # ── Smart Assistant ───────────────────────────────────────

    # ── 持久化：工作区管理（S03） ─────────────────────────────

    def start_foreground_task(
        self,
        fn,
        *,
        message: str,
        on_result=None,
        on_error=None,
        on_finished=None,
        before_start=None,
        disable_workbench: bool = True,
    ) -> bool:
        """Run one visible disk task without blocking the GUI event loop."""

        if (
            (self._foreground_worker is not None and self._foreground_worker.isRunning())
            or self._save_worker is not None
            or self._project_open_worker is not None
            or getattr(self._ctx, "project_opening", False)
            or getattr(self._ctx, "project_view_failed", False)
        ):
            self.show_message("另一项后台操作仍在进行，请稍候。")
            return False
        if before_start is not None:
            before_start()
        self._workbench.show_step2_progress(0, message)
        if disable_workbench:
            self._workbench.setEnabled(False)
        worker = None
        outcome: dict[str, object] = {}

        def _cleanup() -> None:
            if worker is None or self._foreground_worker is worker:
                self._foreground_worker = None
                self._workbench.hide_step2_progress()
                if disable_workbench:
                    self._workbench.setEnabled(True)
            if "error" in outcome:
                error = str(outcome["error"])
                if on_error is not None:
                    on_error(error)
                else:
                    self.show_message(f"后台操作失败：{error}", error=True)
            elif "result" in outcome and on_result is not None:
                on_result(outcome["result"])
            if on_finished is not None:
                on_finished()

        try:
            worker = ApiWorker(fn, route_http_errors=False)
            self._foreground_worker = worker
            worker.result.connect(lambda result: outcome.__setitem__("result", result))
            worker.error.connect(lambda error: outcome.__setitem__("error", error))
            worker.finished.connect(_cleanup)
            worker.start()
        except Exception as exc:
            logging.getLogger(__name__).exception("Cannot start foreground worker")
            outcome["error"] = f"无法启动后台操作：{exc}"
            _cleanup()
            if worker is not None:
                worker.deleteLater()
            return True
        self._workers.append(worker)
        return True

    def _save_current_project(self):
        """同步保存实现；仅允许从后台 worker 调用。"""
        projection_diverged = getattr(self._ctx, "authoritative_projection_diverged", None)
        if callable(projection_diverged) and projection_diverged():
            from transbridge.application.contracts import DomainError, ErrorCategory, OperationResult

            return OperationResult.failed(
                DomainError(
                    ErrorCategory.CONFLICT,
                    "PROJECTION_AUTHORITY_DIVERGED",
                    "工作台内容尚未提交到当前工程版本；已拒绝显示为保存成功。请重试刚才的操作。",
                ),
                run_id=None if self._runtime_context is None else self._runtime_context.run_id,
            )
        if self._project_commands is not None and self._runtime_context is not None:
            return self._project_commands.save(self._runtime_context)
        return None

    def save_current_project_async(self, *, automatic: bool = False, on_finished=None) -> bool:
        """Queue one Project save and coalesce callers while it is active."""
        from .shell.project_save import ProjectSaveCoordinator

        coordinator = getattr(self, "_project_save_coordinator", None)
        if coordinator is None:
            coordinator = self._project_save_coordinator = ProjectSaveCoordinator(self)
        return coordinator.request(automatic=automatic, on_finished=on_finished, worker_factory=ApiWorker)

    def show_about(self):
        QMessageBox.about(
            self,
            "关于 TransBridge",
            f"TransBridge v{__version__}\n\nESP 插件翻译辅助工具，对接 ParaTranz 平台。",
        )
