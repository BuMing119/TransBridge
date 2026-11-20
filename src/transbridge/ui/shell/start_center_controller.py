# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Feature-local Qt composition for the guided start-center workflow."""

from __future__ import annotations

from dataclasses import replace
import logging

from PyQt6.QtWidgets import QFileDialog, QMessageBox

from transbridge.ui.coordinators.guided_project_coordinator import (
    GuidedDraftPhase,
    GuidedProjectCoordinator,
    GuidedProjectDraftState,
)
from transbridge.ui.coordinators.project_creation_operation import ProjectCreationOperation
from transbridge.ui.shell.action_catalog import IntentId
from transbridge.ui.workbench.file_source_formats import (
    FILE_FILTER_HINTS,
    INCOMPATIBLE_SOURCE_FILTER,
    PLUGIN_SOURCE_FILTER,
    TRANSLATION_FILE_FILTER,
    is_incompatible_source,
    is_plugin_source,
)

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
        view.add_sources_requested.connect(self._add_sources)
        view.add_incompatible_requested.connect(lambda: self._choose_source(incompatible=True, append=True))
        view.source_paths_changed.connect(self.guided_project.set_source_paths)
        view.source_format_changed.connect(self.guided_project.set_source_format)
        view.translation_options_changed.connect(lambda options: self.guided_project.set_translation_options(**options))
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
        """Choose plugin sources inside the unified Project creation flow."""

        self._choose_source()

    def choose_source_path(self, source_path: str) -> None:
        """Start the same guided draft with an already reviewed local path."""

        state = self._source_selection_draft()
        if state is not None:
            self._accept_sources((source_path,), state, incompatible=not is_plugin_source(source_path))

    def create_empty(self) -> None:
        """Create an empty Project from the advanced choice on the creation page."""

        self._begin_project(())

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
            mode = None if payload is None else payload.get("mode")
            if mode == "plugin":
                return self.choose_source()
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

    def _source_selection_draft(self) -> GuidedProjectDraftState | None:
        if self.guided_project is None:
            self._host.show_message("PROJECT_PROVISIONING_UNAVAILABLE: 建项服务不可用。")
            return None
        state = self.guided_project.state
        return None if state.in_flight else state

    def _choose_source(self, *, incompatible: bool = False, append: bool = False) -> None:
        state = self._source_selection_draft()
        if state is None:
            return
        title = "添加来源文件（可多选）" if append else "选择来源文件（可多选）"
        if incompatible:
            title = "不安全导入（可多选）"
        paths, selected_filter = QFileDialog.getOpenFileNames(
            self._host,
            title,
            "",
            INCOMPATIBLE_SOURCE_FILTER if incompatible else PLUGIN_SOURCE_FILTER,
        )
        if paths:
            self._accept_sources(
                tuple(paths),
                state,
                incompatible=incompatible,
                append=append,
                format_hint=FILE_FILTER_HINTS.get(selected_filter) if incompatible else None,
            )

    def _add_sources(self) -> None:
        self._choose_source(append=True)

    def _accept_sources(
        self,
        paths: tuple[str, ...],
        state: GuidedProjectDraftState,
        *,
        incompatible: bool,
        append: bool = False,
        format_hint: str | None = None,
    ) -> None:
        guided = self.guided_project
        if guided is None or guided.state is not state or getattr(self._host, "close_pending", False):
            return
        allowed = is_incompatible_source if incompatible else is_plugin_source
        if not all(allowed(path) for path in paths):
            self._host.show_message(
                "不安全导入仅支持非插件来源文件，如 EET、SST、XML、JSON 或 Strings。"
                if incompatible
                else "常规来源仅支持 ESP、ESM、ESL 插件。其他受支持的格式请在创建工程表单中使用“不安全导入”。"
            )
            return
        if incompatible and not self._confirm_incompatible_sources():
            return
        if guided.state is not state or getattr(self._host, "close_pending", False):
            return
        if append:
            if not incompatible:
                format_hint = state.source_format_hint
            elif any(is_incompatible_source(path) for path in state.source_paths):
                format_hint = None
            guided.set_source_paths((*state.source_paths, *paths), source_format_hint=format_hint)
        else:
            self._begin_project(paths, source_format_hint=format_hint)

    def _confirm_incompatible_sources(self) -> bool:
        yes, no = QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No
        dialog = QMessageBox(
            QMessageBox.Icon.Warning,
            "不安全导入",
            "非插件文件缺少完整的插件结构。作为工程来源导入后，任务与对话树、直接写回插件等多项功能"
            "对这些来源不可用，其他功能也可能受文件格式限制。建议优先使用 ESP、ESM、ESL。\n\n"
            "如果只想导入已有译文，请使用建项表单中的“同时导入已有译文”。是否继续？",
            yes | no,
            self._view,
        )
        dialog.button(yes).setText("继续导入")
        dialog.button(no).setText("取消")
        dialog.setDefaultButton(no)
        dialog.setEscapeButton(no)
        try:
            return dialog.exec() == yes
        finally:
            dialog.deleteLater()

    def _begin_project(self, paths: tuple[str, ...], *, source_format_hint: str | None = None) -> None:
        if self.guided_project is None:
            self._host.show_message("PROJECT_PROVISIONING_UNAVAILABLE: 建项服务不可用。")
            return
        self._leave_landing()
        self.guided_project.begin_sources(paths, source_format_hint=source_format_hint)
        self._show_start_center_view()

    def _show_start_center_view(self) -> None:
        mode_tabs = getattr(self._host, "mode_tabs", None)
        if mode_tabs is not None and hasattr(mode_tabs, "setCurrentWidget"):
            mode_tabs.setCurrentWidget(self._view)
        else:
            self._host.central_stack.setCurrentWidget(self._view)

    def _choose_migrations(self) -> None:
        if self.guided_project is None or self.guided_project.state.in_flight:
            return
        state = self.guided_project.state
        hint = state.translation_format_hint
        if hint == "strings-directory":
            path = QFileDialog.getExistingDirectory(self._host, "选择 Strings 目录", state.translation_path or "")
            paths = (path,) if path else ()
        else:
            paths, selected_filter = QFileDialog.getOpenFileNames(
                self._host, "选择已有译文（可多选）", state.translation_path or "", TRANSLATION_FILE_FILTER
            )
            hint = FILE_FILTER_HINTS.get(selected_filter)
        if paths:
            self.guided_project.set_translation_options(
                enabled=True,
                paths=tuple(paths),
                format_hint=hint,
                replace_existing=state.replace_existing,
                strings_lang=state.strings_lang,
            )

    def _on_created(self, _value: dict) -> None:
        if self.guided_project is None:
            return
        state = self.guided_project.state
        self.show_workbench()
        self._host.show_message("\n".join((f"本地工程“{state.project_name}”已创建", *state.completion_notes)))

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
