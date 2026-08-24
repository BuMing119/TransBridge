"""Feature-local Qt composition for the guided start-center workflow."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import QFileDialog

from transbridge.ui.coordinators.guided_project_coordinator import GuidedProjectCoordinator
from transbridge.ui.shell.action_catalog import IntentId

from .start_center import (
    RecentProjectViewState,
    RecoveryItemViewState,
    StartCenterViewState,
    StartCenterWidget,
    StartDestinationState,
)


class StartCenterController:
    """Wire shell intents to public application/UI ports without business logic."""

    def __init__(self, host, view: StartCenterWidget, *, dispatch=None) -> None:
        self._host = host
        self._view = view
        self._revision = 0
        self._intent_dispatch = dispatch
        self.guided_project: GuidedProjectCoordinator | None = None

    def start(self) -> None:
        view = self._view
        host = self._host
        view.choose_plugin_requested.connect(lambda: self._submit(IntentId.SOURCE_PARSE))
        view.open_project_requested.connect(lambda: self._submit(IntentId.PROJECT_OPEN))
        view.open_recent_requested.connect(lambda path: self._submit(IntentId.PROJECT_OPEN, {"path": path}))
        view.create_empty_requested.connect(lambda: self._submit(IntentId.PROJECT_CREATE))
        view.open_fomod_requested.connect(lambda: self._submit(IntentId.PUBLISH_FOMOD))
        view.return_to_current_requested.connect(self.show_workbench)
        view.return_to_landing_requested.connect(lambda: self.show(user_requested=bool(host.context.project_name)))
        view.recovery_details_requested.connect(self._show_recovery_details)
        if host.project_commands is None or host.runtime_context is None:
            view.choose_plugin_button.setEnabled(False)
            view.choose_plugin_button.setToolTip("建项服务不可用")
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
        view.prepare_requested.connect(self.guided_project.prepare)
        view.commit_requested.connect(self.guided_project.commit)

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
        self._host.central_stack.setCurrentWidget(self._host.mode_tabs)

    def choose_source(self) -> None:
        """Public SOURCE_PARSE intent target shared by button/menu/palette."""

        self._choose_source()

    def choose_source_path(self, source_path: str) -> None:
        """Start the same guided draft with an already reviewed local path."""

        self._begin_project(source_path)

    def create_empty(self) -> None:
        """Public PROJECT_CREATE intent target for guided empty projects."""

        self._begin_project(None)

    def _submit(self, intent_id: IntentId, payload=None) -> object:
        if self._intent_dispatch is not None:
            return self._intent_dispatch(intent_id, payload)
        if intent_id is IntentId.SOURCE_PARSE:
            return self._choose_source()
        if intent_id is IntentId.PROJECT_OPEN:
            path = None if payload is None else payload.get("path")
            return (
                self._host.project_coordinator.open_project_path(path)
                if path
                else self._host.project_coordinator.open_project()
            )
        if intent_id is IntentId.PROJECT_CREATE:
            return self._begin_project(None)
        if intent_id is IntentId.PUBLISH_FOMOD:
            return self._host.tool_windows.open_fomod()
        raise KeyError(intent_id)

    def _dispatch(self, operation, message, on_result, on_error) -> bool:
        return self._host.start_foreground_task(
            operation,
            message=message,
            on_result=on_result,
            on_error=on_error,
            disable_workbench=False,
        )

    def _choose_source(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self._host,
            "选择插件开始翻译",
            "",
            "Bethesda 插件 (*.esp *.esm *.esl);;所有文件 (*)",
        )
        if path:
            self._begin_project(path)

    def _begin_project(self, source_path: str | None) -> None:
        if self.guided_project is None:
            self._host.show_message("PROJECT_PROVISIONING_UNAVAILABLE: 建项服务不可用。")
            return
        self.guided_project.begin(source_path)

    def _choose_migrations(self) -> None:
        if self.guided_project is None:
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self._host,
            "选择已有译文",
            "",
            "支持的翻译来源 (*.eet *.json *.xlsx *.xls *.esp *.esm *.esl);;所有文件 (*)",
        )
        if paths:
            self.guided_project.set_migration_sources(tuple(paths))

    def _on_created(self, _value: dict) -> None:
        if self.guided_project is None:
            return
        state = self.guided_project.state
        # S02 already parsed and published the authoritative baseline.  This
        # shell must not create a second legacy collection by reparsing here.
        if state.source_path is not None:
            project_id = str(_value.get("project_id", ""))
            hydration = self._host.project_commands.consume_create_hydration(
                project_id,
                self._host.runtime_context,
            )
            if not hydration.is_success or hydration.value is None:
                diagnostic = hydration.diagnostics[0]
                self._host.show_message(f"{diagnostic.code}: {diagnostic.message}")
                return
            self._hydrate_collection(hydration.value.source)
        self.show_workbench()
        self._host.show_message(f"本地工程“{state.project_name}”已创建")

    def _hydrate_collection(self, source) -> None:
        from transbridge.converter.translation_entry import TranslationEntry
        from transbridge.converter.translation_entry_collection import TranslationEntryCollection
        from transbridge.ui.projection_types import CollectionSlot

        entries = tuple(
            TranslationEntry(
                id=item.legacy_id,
                key=item.entry_key.local_key,
                original=item.original,
                translation=item.translation,
                stage=item.stage,
                context=item.context,
                entry_key=item.entry_key,
                external_refs=item.external_refs,
                revision=item.revision,
                provenance=item.provenance,
                metadata=item.metadata,
            )
            for item in source.entries
        )
        collection = TranslationEntryCollection(entries)
        self._host.context.add_slot(
            source.location,
            CollectionSlot(
                label=Path(source.location).stem,
                collection=collection,
                esp_path=source.location,
                source_snapshot=source.source_snapshot,
                format_id=source.format_id,
            ),
        )

    def _show_recovery_details(self, storage_key: str) -> None:
        recovery = next(
            (item for item in self._recovery_projection() if item.storage_key == storage_key),
            None,
        )
        if recovery is not None:
            self._host.show_message(recovery.reason or "该恢复项可在任务中心继续。")

    def _render(
        self,
        destination: StartDestinationState,
        *,
        diagnostic_code: str = "",
        diagnostic_message: str = "",
    ) -> None:
        self._revision += 1
        context = self._host.context
        state = StartCenterViewState(
            destination=destination,
            revision=self._revision,
            recent_projects=self._recent_project_projection(),
            recovery_items=self._recovery_projection(),
            active_project_name=context.project_name,
            dirty=context.dirty,
            diagnostic_code=diagnostic_code,
            diagnostic_message=diagnostic_message,
        )
        self._view.render(state)
        self._host.central_stack.setCurrentWidget(self._view)

    def _recent_project_projection(self) -> tuple[RecentProjectViewState, ...]:
        runtime = self._host.app_runtime
        if runtime is not None and "project_catalog" in runtime.use_cases.names():
            snapshot = runtime.use_cases.resolve("project_catalog").list_projects()
            return tuple(
                RecentProjectViewState(
                    project_key=item.project_id,
                    name=item.name,
                    path=item.path,
                    available=item.available,
                    reason=item.reason or "",
                    active=item.active,
                )
                for item in snapshot.projects
            )
        workspace = self._host.context.workspace
        if workspace is None:
            return ()
        active = workspace.active_project
        output = []
        for name, raw_path in workspace.projects.items():
            available = Path(raw_path).is_file()
            output.append(
                RecentProjectViewState(
                    project_key=name,
                    name=name,
                    path=str(raw_path),
                    available=available,
                    reason="" if available else "工程记录不存在或不可访问",
                    active=name == active,
                )
            )
        return tuple(output)

    def _recovery_projection(self) -> tuple[RecoveryItemViewState, ...]:
        runtime = self._host.app_runtime
        context = self._host.runtime_context
        if runtime is None or context is None:
            return ()
        try:
            from transbridge.application.tasks import OwnerRef

            metadata = dict(context.metadata)
            actor = OwnerRef(
                owner_id=context.owner_id,
                entrypoint=metadata.get("entrypoint", "gui"),
                project_id=context.project_id,
                variant_id=context.variant_id,
                session_id=context.session_id,
                permissions=context.permissions,
            )
            values = runtime.use_cases.resolve("task_recovery").list(actor)
        except Exception:
            return ()
        return tuple(
            RecoveryItemViewState(
                storage_key=item.storage_key,
                title=item.display_name,
                recoverable=item.recoverable,
                reason=item.reason_message or item.reason_code,
                run_id=item.run_id,
            )
            for item in values
        )


__all__ = ["StartCenterController"]
