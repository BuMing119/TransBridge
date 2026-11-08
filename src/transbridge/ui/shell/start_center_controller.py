"""Feature-local Qt composition for the guided start-center workflow."""

from __future__ import annotations

from dataclasses import replace
import logging

from PyQt6.QtWidgets import QFileDialog

from transbridge.ui.coordinators.guided_project_coordinator import GuidedDraftPhase, GuidedProjectCoordinator
from transbridge.ui.coordinators.project_creation_operation import ProjectCreationOperation
from transbridge.ui.shell.action_catalog import IntentId

from .project_window_launcher import launch_project_in_new_window
from .start_center import StartCenterViewState, StartCenterWidget, StartDestinationState
from .start_center_directory import StartCenterDirectoryLoader, StartCenterDirectorySnapshot

logger = logging.getLogger(__name__)


class StartCenterController:
    """Wire shell intents to public application/UI ports without business logic."""

    def __init__(self, host, view: StartCenterWidget, *, dispatch=None, project_window_launcher=None) -> None:
        self._host = host
        self._view = view
        self._revision = 0
        self._intent_dispatch = dispatch
        self._project_window_launcher = project_window_launcher or launch_project_in_new_window
        self._directory = StartCenterDirectorySnapshot()
        self._landing_state: StartCenterViewState | None = None
        self._directory_loader = StartCenterDirectoryLoader(
            self._apply_directory, is_closing=lambda: getattr(host, "close_pending", False)
        )
        self._creation_operation = ProjectCreationOperation(host)
        self.guided_project: GuidedProjectCoordinator | None = None

    def start(self) -> None:
        view = self._view
        host = self._host
        view.choose_plugin_requested.connect(lambda: self._submit(IntentId.PROJECT_CREATE, {"mode": "plugin"}))
        view.open_project_requested.connect(lambda: self._submit(IntentId.PROJECT_OPEN))
        view.open_recent_requested.connect(lambda path: self._submit(IntentId.PROJECT_OPEN, {"path": path}))
        view.open_recent_in_new_window_requested.connect(self._open_recent_in_new_window)
        view.delete_project_requested.connect(
            lambda project_id, name: self._submit(
                IntentId.PROJECT_DELETE,
                {"project_id": project_id, "name": name},
            )
        )
        view.create_empty_requested.connect(self.create_empty)
        view.open_fomod_requested.connect(lambda: self._submit(IntentId.PUBLISH_FOMOD))
        view.task_center_requested.connect(lambda: self._submit(IntentId.TASK_OPEN_ACTIVITY))
        view.return_to_current_requested.connect(self.show_workbench)
        view.return_to_landing_requested.connect(lambda: self.show(user_requested=bool(host.context.project_name)))
        if (close_cancelled := getattr(host, "close_cancelled", None)) is not None:
            close_cancelled.connect(self._request_directory)
        if host.project_commands is None or host.runtime_context is None:
            view.set_creation_available(False, "建项服务不可用")
            return
        self.guided_project = GuidedProjectCoordinator(
            host.project_commands,
            host.runtime_context,
            dispatch=self._dispatch,
            on_state=view.render_draft,
            on_created=self._on_created,
        )
        view.project_name_changed.connect(self.guided_project.set_project_name)
        view.variant_name_changed.connect(self.guided_project.set_variant_name)
        view.skip_empty_changed.connect(lambda value: self.guided_project.set_parse_option("skip_empty", value))
        view.choose_migration_requested.connect(self._choose_migrations)
        view.prepare_requested.connect(self.guided_project.create)

    def show_restoring(self) -> None:
        self._render(StartDestinationState.RESTORING_LAST)

    def show_empty(self) -> None:
        self._render(StartDestinationState.START_CENTER_EMPTY)

    def show_recovery_failed(self, code: str, message: str) -> None:
        self._render(
            StartDestinationState.START_CENTER_RECOVERY_FAILED,
            diagnostic_code=code,
            diagnostic_message=message,
        )

    def show(self, *, user_requested: bool = False) -> None:
        destination = (
            StartDestinationState.START_CENTER_USER_REQUESTED
            if user_requested
            else StartDestinationState.START_CENTER_EMPTY
        )
        self._render(destination)

    def show_workbench(self) -> None:
        self._leave_landing()
        mode_tabs = getattr(self._host, "mode_tabs", None)
        if mode_tabs is not None and hasattr(mode_tabs, "setCurrentIndex"):
            mode_tabs.setCurrentIndex(0)
            return
        self._host.central_stack.setCurrentWidget(self._host.mode_tabs)

    def choose_source(self) -> None:
        """Choose the plugin source inside the unified Project creation flow."""

        self._choose_source()

    def choose_source_path(self, source_path: str) -> None:
        """Start the same guided draft with an already reviewed local path."""

        self._begin_project(source_path)

    def create_empty(self) -> None:
        """Create an empty Project from the advanced choice on the creation page."""

        self._begin_project(None)

    def begin_creation(self) -> None:
        """Open the single public Project creation entry page."""

        self.show(user_requested=True)

    def _open_recent_in_new_window(self, path: str) -> None:
        try:
            started = self._project_window_launcher(path)
        except Exception as exc:  # noqa: BLE001 - process adapters can fail at the OS boundary
            logger.exception("Failed to launch a Project in a detached GUI process")
            self._host.show_message(f"PROJECT_WINDOW_LAUNCH_FAILED: {exc}")
            return
        if not started:
            self._host.show_message("PROJECT_WINDOW_LAUNCH_FAILED: 操作系统未能启动新窗口。")
            return
        self._host.show_message("已请求在新窗口打开工程。")

    def _submit(self, intent_id: IntentId, payload=None) -> object:
        if self._intent_dispatch is not None:
            return self._intent_dispatch(intent_id, payload)
        if intent_id is IntentId.PROJECT_CREATE:
            if payload and payload.get("mode") == "plugin":
                return self._choose_source()
            return self.begin_creation()
        if intent_id is IntentId.PROJECT_OPEN:
            path = None if payload is None else payload.get("path")
            return (
                self._host.project_coordinator.open_project_path(path)
                if path
                else self._host.project_coordinator.open_project()
            )
        if intent_id is IntentId.PUBLISH_FOMOD:
            return self._host.tool_windows.open_fomod()
        raise KeyError(intent_id)

    def _dispatch(self, operation, message, on_result, on_error) -> bool:
        if self.guided_project is not None and self.guided_project.state.phase is GuidedDraftPhase.COMMITTING:
            return self._creation_operation.start(
                operation,
                message,
                on_result,
                on_error,
                has_sources=bool(self.guided_project.state.source_paths or self.guided_project.state.migration_sources),
                preview_token=self.guided_project.state.preview_token,
            )
        return self._host.start_foreground_task(
            operation,
            message=message,
            on_result=on_result,
            on_error=on_error,
            disable_workbench=False,
        )

    def _choose_source(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self._host,
            "选择要翻译的插件（可多选）",
            "",
            "Bethesda 插件 (*.esp *.esm *.esl);;所有文件 (*)",
        )
        if paths:
            self._begin_project(tuple(paths))

    def _begin_project(self, source_path: str | tuple[str, ...] | None) -> None:
        if self.guided_project is None:
            self._host.show_message("PROJECT_PROVISIONING_UNAVAILABLE: 建项服务不可用。")
            return
        self._leave_landing()
        if isinstance(source_path, tuple):
            self.guided_project.begin_sources(source_path)
        else:
            self.guided_project.begin(source_path)
        self._show_start_center_view()

    def _show_start_center_view(self) -> None:
        mode_tabs = getattr(self._host, "mode_tabs", None)
        if mode_tabs is not None and hasattr(mode_tabs, "setCurrentWidget"):
            mode_tabs.setCurrentWidget(self._view)
        else:
            self._host.central_stack.setCurrentWidget(self._view)

    def _choose_migrations(self) -> None:
        if self.guided_project is None:
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self._host,
            "选择已有译文",
            "",
            "支持的翻译来源 (*.xml *.eet *.json *.sst *.esp *.esm *.esl);;"
            "ESP-ESM Translator EET (*.eet);;ESP-ESM Translator XML (*.xml);;xTranslator XML (*.xml);;"
            "xTranslator SST (*.sst);;JSON (*.json);;所有文件 (*)",
        )
        if paths:
            self.guided_project.set_migration_sources(tuple(paths))

    def _on_created(self, _value: dict) -> None:
        if self.guided_project is None:
            return
        state = self.guided_project.state
        self.show_workbench()
        self._host.show_message(f"本地工程“{state.project_name}”已创建")

    def _render(
        self,
        destination: StartDestinationState,
        *,
        diagnostic_code: str = "",
        diagnostic_message: str = "",
    ) -> None:
        self._revision += 1
        context = self._host.context
        self._landing_state = StartCenterViewState(
            destination=destination,
            revision=self._revision,
            active_project_name=context.project_name,
            dirty=context.dirty,
            diagnostic_code=diagnostic_code,
            diagnostic_message=diagnostic_message,
        )
        self._render_directory()
        self._show_start_center_view()
        self._request_directory()

    def _leave_landing(self) -> None:
        self._landing_state = None
        self._directory_loader.invalidate()

    def _request_directory(self) -> None:
        runtime = self._host.app_runtime
        if runtime is not None and self._landing_state is not None:
            self._directory_loader.request(self._landing_state.revision, runtime.use_cases, self._host.runtime_context)

    def _apply_directory(self, revision: int, snapshot: StartCenterDirectorySnapshot) -> None:
        if self._landing_state is None or self._landing_state.revision != revision:
            return
        stack = getattr(self._host, "central_stack", None)
        if stack is not None and stack.currentWidget() is not self._view:
            return
        self._directory = snapshot
        self._render_directory()

    def _render_directory(self) -> None:
        context = self._host.context
        self._view.render(
            replace(
                self._landing_state,
                recent_projects=self._directory.project_rows(getattr(context, "active_project_id", None)),
                recovery_items=self._directory.recovery,
                recovery_diagnostic_message=self._directory.diagnostic,
            )
        )


__all__ = ["StartCenterController"]
