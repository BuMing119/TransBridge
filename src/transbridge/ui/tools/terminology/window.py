"""Top-level terminology workbench and its narrow shell launcher."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from transbridge.application.contracts import JobRef
from transbridge.application.tasks import JobState, OwnerRef
from transbridge.application.terminology.conflict_queries import ConflictFilter, ConflictQuery
from transbridge.application.terminology.workloads import TerminologyWorkloadType
from transbridge.ui.source_input_state import SourceInputScope, ensure_source_input

from .asset_presentation import WorkbenchAssetPresentation
from .asset_refresh import AssetRefreshCoordinator
from .async_actions import AsyncActionRunner
from .build_view import BuildView
from .conflicts_view import ConflictsView
from .context_binding import TerminologyWindowContext
from .draft_interactions import DraftInteractionController
from .draft_view import DraftView
from .draft_write_queue import DraftWriteQueue
from .effective_view import EffectiveTermsView
from .history_view import HistoryView
from .launcher import TerminologyLauncher
from .object_views import TermsView, VersionsView
from .operation_notice import OperationNotice
from .presenter import TerminologyPresenter
from .reports_view import ReportsView
from .save_close import DraftSaveCloseGuard
from .schemes_controller import TerminologySchemesController
from .schemes_view import TerminologySchemesView
from .sync_presenter import TerminologySyncPresenter
from .sync_task_adapter import TerminologySyncTaskAdapter
from .sync_view import TerminologySyncPanel
from .table_models import decision_status_label as _decision_status_label, make_table_model
from .task_adapter import TerminologyTaskViewState
from .term_browsing import TermBrowsingController
from .version_interactions import VersionInteractionController
from .view_models import TerminologyArea, TerminologyPreflightViewState, business_diagnostic, phase_label
from .workbench_shell import TerminologyWorkbenchShell
from .workflow_controller import TerminologyWorkflowController


class TerminologyWindow(QWidget):
    """One Project/Variant-scoped window; closing detaches UI ownership only."""

    task_changed = pyqtSignal(object)
    closed = pyqtSignal()
    context_invalidated = pyqtSignal()

    def __init__(
        self,
        presenter: TerminologyPresenter,
        parent: QWidget | None = None,
        *,
        ui_context=None,
        profile_controller=None,
    ) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.presenter = presenter
        self.setWindowTitle("项目术语")
        self.setProperty("tbTaskDialog", True)
        self.resize(1180, 760)
        self.setMinimumSize(760, 560)
        self.setAccessibleName("项目术语工作台")
        self._actions = AsyncActionRunner(self, on_error=self._show_command_error)
        self._preflight_started = False
        self._task_refs: dict[str, JobRef] = {}
        self._displayed_run_id: str | None = None
        self._finished_runs: set[str] = set()
        self._conflict_filter = ConflictFilter()
        self._closed = False
        self._invalidated = False
        self._sync_tasks: TerminologySyncTaskAdapter | None = None
        self._ui_context = ui_context
        self._input_source_keys = None
        self._context_binding = TerminologyWindowContext(
            self, ui_context, profile_controller, (presenter.context.project_id, presenter.context.variant_id)
        )
        self._profile_controller = self._context_binding.profiles
        self._draft_actions = DraftInteractionController(
            self,
            presenter,
            self._run_draft_command,
            self._run_read,
            on_prepared=lambda draft: self._workflow.prepared(draft),
            on_build_required=lambda: self._workflow.build_required(),
        )
        self._version_actions = VersionInteractionController(
            self, presenter, self._run_read, self._run_command, self._bind_existing_assets
        )
        self._init_ui()
        self._asset_view = WorkbenchAssetPresentation(self)
        self._asset_refresh = AssetRefreshCoordinator(
            self,
            presenter.services.commands,
            presenter.context,
            browsing=presenter.services.browsing,
            on_ready=self._asset_view.apply_refresh,
            on_error=self._show_command_error,
        )
        self._draft_writes = DraftWriteQueue(
            self,
            on_started=lambda: self.operation_notice.show_message("正在保存人工调整…"),
            on_saved=self._draft_saved,
            on_error=self._show_command_error,
        )
        self._workflow = TerminologyWorkflowController(self)
        self._save_close = DraftSaveCloseGuard(self, self._draft_writes)
        self._context_binding.invalidated.connect(self.invalidate_context)
        self.task_changed.connect(self._on_task_change, Qt.ConnectionType.QueuedConnection)
        presenter.bind_tasks(self.task_changed.emit)

    @property
    def context_is_current(self) -> bool:
        return not self._closed and self._context_binding.identity is not None

    def invalidate_context(self) -> None:
        if self._invalidated:
            return
        self._invalidated = True
        self._context_binding.dispose()
        self._actions.close()
        self._asset_refresh.close()
        self.context_invalidated.emit()
        self._save_close.context_changed()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.workspace = TerminologyWorkbenchShell(self)
        layout.addWidget(self.workspace, 1)
        self.operation_notice = OperationNotice(self.workspace)
        self.workspace.set_operation_notice(self.operation_notice)

        self.conflicts_model = make_table_model(self.presenter, "conflicts", self)
        self.draft_model = make_table_model(self.presenter, "draft", self)
        self.effective_model = make_table_model(self.presenter, "effective", self)
        self.history_model = make_table_model(self.presenter, "history", self)
        self._models = [self.conflicts_model, self.draft_model, self.effective_model, self.history_model]
        self._browsing = TermBrowsingController(self.presenter.context, self.effective_model, self.draft_model)
        self.conflicts_view = ConflictsView(self.conflicts_model, self)
        self.conflicts_view.query_changed.connect(self._replace_conflict_query)
        self.conflicts_view.review_requested.connect(self._draft_actions.review)

        self.effective_view = EffectiveTermsView(self.effective_model, self)
        self.effective_view.query_changed.connect(lambda filters: self._browsing.replace_filter("effective", filters))
        self.draft_view = DraftView(self.draft_model, self)
        self.draft_view.query_changed.connect(lambda filters: self._browsing.replace_filter("draft", filters))
        self.draft_view.add_requested.connect(self._draft_actions.add)
        self.draft_view.edit_requested.connect(self._draft_actions.edit)
        self.draft_view.suppress_requested.connect(self._draft_actions.toggle_suppression)

        self.history_view = HistoryView(self.history_model, self)
        self.history_view.compare_requested.connect(self._compare_history)
        self.history_view.restore_requested.connect(self._restore_history)

        self.build_view = BuildView(self)
        self.build_view.preflight_requested.connect(self.run_preflight)
        self.build_view.build_requested.connect(self._start_build)
        self.build_view.cancel_requested.connect(self._cancel_displayed)
        self.build_view.terms_requested.connect(lambda: self.workspace.set_current_area(TerminologyArea.TERMS))
        self.build_view.versions_requested.connect(lambda: self.workspace.set_current_area(TerminologyArea.VERSIONS))
        self.terms_view = TermsView(
            self.draft_view, self.conflicts_view, self.build_view, self, effective=self.effective_view
        )
        self.schemes_view = TerminologySchemesView(self)
        self._schemes = TerminologySchemesController(
            self.schemes_view,
            self._ui_context,
            self._profile_controller,
            self,
        )
        if self._profile_controller is not None:
            self._profile_controller.state_changed.connect(self._profile_changed)
            self.terms_view.render_source(self._profile_controller.state)
        self.workspace.set_scheme_switcher(self.schemes_view)
        self.versions_view = VersionsView(self.history_view, self)
        presenter = self.presenter
        sync_service = presenter.services.sync
        if sync_service is not None:
            owner = OwnerRef(
                presenter.context.owner_id,
                "gui",
                presenter.context.project_id,
                presenter.context.variant_id,
                presenter.context.session_id,
                presenter.context.permissions,
            )
            sync_presenter = TerminologySyncPresenter(sync_service, presenter.context, owner)
            self.sync_view = TerminologySyncPanel(sync_presenter, self.versions_view)
            self.sync_view.draft_committed.connect(lambda _ref: self._bind_existing_assets())
            self.versions_view.set_sync_panel(self.sync_view)
            if presenter.services.runtime is not None:
                self._sync_tasks = TerminologySyncTaskAdapter(
                    presenter.services.runtime,
                    owner,
                    self.sync_view.activity_sink,
                )
                self.sync_view.set_cancel_handler(self._sync_tasks.cancel)
                self.destroyed.connect(self._sync_tasks.close)
                self._sync_tasks.start()
        self.versions_view.publish_requested.connect(self._publish)
        self.versions_view.discard_requested.connect(self._version_actions.discard)
        self.publish_status = self.versions_view.publish_status
        self.publish_details = self.versions_view.publish_details
        self.reports_view = ReportsView(self)
        self.reports_view.quality_report_requested.connect(self._render_report)
        self.reports_view.changelog_requested.connect(self._render_changelog)
        self.reports_view.retry_requested.connect(self._retry_changelog)
        self.workspace.add_area(TerminologyArea.TERMS, self.terms_view)
        self.workspace.add_area(TerminologyArea.VERSIONS, self.versions_view)
        self.workspace.add_area(TerminologyArea.REPORTS, self.reports_view)

    def run_preflight(self, *, repair=True) -> None:
        if not self.context_is_current:
            return
        if repair:
            if self._input_source_keys is None:
                self._input_source_keys = tuple(getattr(self._ui_context, "slots", {}))
            scope = SourceInputScope(self._ui_context, self._input_source_keys)
            if not ensure_source_input(
                self._ui_context, "template", source_keys=tuple(scope.slots), resume=self.run_preflight, parent=self
            ):
                return
            if self._ui_context is not None:
                self.presenter.set_source_scope(
                    tuple(dict.fromkeys(slot.source_id for slot in scope.slots.values() if slot.source_id))
                )
        self._preflight_started = True
        self.build_view.preflight_button.setEnabled(False)
        self.operation_notice.show_message("正在检查当前工程、翻译版本和来源…")
        self._actions.run(
            self.presenter.preflight, self._preflight_ready, key="preflight", on_error=self._preflight_failed
        )

    def bind_page(self, section: str, snapshot_ref: object, *, query_fingerprint: str = "all") -> None:
        if section in {"draft", "effective"}:
            self._browsing.refresh()
            return
        models = {
            "conflicts": self.conflicts_model,
            "history": self.history_model,
        }
        if section == "conflicts":
            snapshot_ref = ConflictQuery(snapshot_ref, self._conflict_filter)
        model = models[section]
        current = getattr(model, "_query", None)
        if (
            current is not None
            and current.snapshot_ref == snapshot_ref
            and current.query_fingerprint == query_fingerprint
        ):
            model.refresh()
        else:
            model.set_query(snapshot_ref, query_fingerprint=query_fingerprint)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        if self.presenter.snapshot is None and not self._preflight_started:
            self.run_preflight(repair=False)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if not self._closed and not self._save_close.allow_close():
            event.ignore()
            return
        if not self._closed:
            self._closed = True
            self._context_binding.dispose()
            self._schemes.close()
            self._draft_writes.close()
            self._asset_refresh.close()
            self._actions.close()
            for model in self._models:
                model.close()
            if self._sync_tasks is not None:
                self._sync_tasks.close()
            if hasattr(self, "sync_view"):
                self.sync_view.dispose()
            self.presenter.close()
            self.closed.emit()
        super().closeEvent(event)

    def _preflight_ready(self, state: object) -> None:
        if self._closed:
            return
        self.build_view.preflight_button.setEnabled(True)
        self.build_view.set_preflight(state)
        self.operation_notice.show_message(state.message, details=state.technical_details if not state.ready else ())
        self.workspace.set_context(state.project_display_name, state.variant_display_name, len(state.sources))
        self.versions_view.set_context(state.current_version_value)
        self._bind_existing_assets()

    def _preflight_failed(self, error: object) -> None:
        if self._closed:
            return
        self.build_view.preflight_button.setEnabled(True)
        notice = self.presenter.notice("TERMINOLOGY_PREFLIGHT_FAILED", str(error))
        self.build_view.set_preflight(TerminologyPreflightViewState.unavailable(f"{notice.message} {notice.recovery}"))
        self.operation_notice.show_error(notice)

    def _bind_existing_assets(self, workload=None, run_id=None) -> None:
        if not self.context_is_current:
            return
        self._asset_refresh.request(workload, run_id)

    def _profile_changed(self, state) -> None:
        if self._closed:
            return
        self._browsing.refresh_effective()
        self.terms_view.render_source(state)
        self._bind_existing_assets()

    def _apply_assets(self, assets, workload=None) -> None:
        self._asset_view.apply(assets, workload)

    def _start_build(self) -> None:
        self._workflow.follow_build = True
        self._run_command(self.presenter.start_build, "构建任务已开始，可在此窗口或任务中心查看进度。")

    def _publish(self) -> None:
        self._version_actions.publish()

    def _render_report(self) -> None:
        self._run_command(self.presenter.render_report, "质量报告生成任务已开始。")

    def _render_changelog(self) -> None:
        self._run_command(self.presenter.render_changelog, "更新日志生成任务已开始。")

    def _retry_changelog(self) -> None:
        self._run_command(self.presenter.retry_changelog, "正在从已保存的发布记录重新生成更新日志。")

    def _compare_history(self, version_ref: object) -> None:
        self._run_command(lambda: self.presenter.compare(version_ref), "版本比较任务已开始。")
        self.history_view.set_status("正在比较所选历史版本与当前版本…")

    def _restore_history(self, version_ref: object) -> None:
        self._version_actions.restore(version_ref)

    def _run_draft_command(self, command, on_saved=None, on_error=None) -> None:
        if not self.context_is_current:
            error = RuntimeError("工程或翻译版本已变化，请切回原版本并重新打开术语工作台。")
            (on_error or self._show_command_error)(error)
            return
        self._draft_writes.submit(command, on_saved=on_saved, on_error=on_error)

    def _draft_saved(self, _result) -> None:
        if not self.context_is_current:
            return
        self.operation_notice.show_message("人工调整已保存到草稿；发布后生效。")
        self._bind_existing_assets()

    def _run_read(self, command, on_success, message: str) -> None:
        if not self.context_is_current:
            return
        self.operation_notice.show_message(message)
        self._actions.run(command, lambda value: self._read_ready(value, on_success))

    def _read_ready(self, value, callback) -> None:
        self.operation_notice.show_message("检查完成。")
        callback(value)

    def _run_command(self, command, message: str) -> None:
        if not self.context_is_current:
            return
        self.operation_notice.show_message("正在准备并提交任务…")
        self._actions.run(command, lambda value: self._command_ready(value, message))

    def _command_ready(self, value: object, message: str) -> None:
        if not isinstance(value, JobRef):
            self._show_command_error(TypeError("术语命令必须返回任务引用"))
            return
        run_id = value.run_id or value.job_id
        if run_id in self._finished_runs:
            return
        self._task_refs[run_id] = value
        if run_id == self._displayed_run_id:
            self.build_view.cancel_button.setEnabled(True)
        self.operation_notice.show_message(message)

    def _show_command_error(self, error: Exception) -> None:
        state = getattr(error, "state", None)
        if isinstance(state, TerminologyPreflightViewState):
            self.build_view.set_preflight(state)
        notice = business_diagnostic(str(getattr(error, "code", "TERMINOLOGY_COMMAND_FAILED")), str(error))
        self.operation_notice.show_error(notice)
        self.reports_view.set_notice(notice)
        self.publish_status.setText(f"{notice.title}：{notice.message} {notice.recovery}")
        self.publish_details.set_details(notice.technical_details)

    def _cancel_displayed(self) -> None:
        ref = self._task_refs.get(self._displayed_run_id)
        if self.context_is_current and ref is not None:
            self.presenter.cancel_task(ref)

    def _on_task_change(self, value: object) -> None:
        if not self.context_is_current or not isinstance(value, TerminologyTaskViewState):
            return
        if value.run_id in self._finished_runs:
            return
        self._displayed_run_id = None if value.is_terminal else value.run_id
        operation = {
            TerminologyWorkloadType.BUILD: "来源整理",
            TerminologyWorkloadType.PUBLISH: "术语发布",
            TerminologyWorkloadType.HISTORY_COMPARE: "版本比较",
            TerminologyWorkloadType.REPORT_RENDER: "质量报告生成",
            TerminologyWorkloadType.CHANGELOG_RENDER: "更新日志生成",
        }.get(value.workload_type, "术语任务")
        status = f"{operation}：{value.message}"
        self.operation_notice.show_message(status)
        detail = f"{phase_label(value.phase)}"
        if value.current_object:
            detail += f" · {value.current_object}"
        self.build_view.set_task_progress(
            status,
            detail,
            completed=value.completed,
            total=value.total,
            terminal=value.is_terminal,
        )
        self.build_view.cancel_button.setEnabled(self._displayed_run_id in self._task_refs)
        if value.is_terminal:
            self._task_refs.pop(value.run_id, None)
            self._finished_runs.add(value.run_id)
        if value.state is JobState.FAILED:
            commands = self.presenter.services.commands
            failure = getattr(commands, "task_failure", None)
            if callable(failure):
                self._actions.run(
                    lambda: failure(value.run_id, self.presenter.context),
                    lambda reason: self._show_task_failure(value, reason),
                )
            else:
                self._show_task_failure(value, None)
        if value.state is JobState.COMPLETED:
            self._bind_existing_assets(value.workload_type, value.run_id)

    def _show_task_failure(self, task, reason):
        code = (
            "CHANGELOG_RENDER_FAILED"
            if task.workload_type is TerminologyWorkloadType.CHANGELOG_RENDER
            else "TERMINOLOGY_TASK_FAILED"
        )
        self._show_command_error(RuntimeError(reason or business_diagnostic(code).message))
        if code == "CHANGELOG_RENDER_FAILED":
            self.reports_view.set_notice(business_diagnostic(code, reason or ""))

    def _replace_conflict_query(self, search: str, risk: str) -> None:
        query = getattr(self.conflicts_model, "_query", None)
        if query is None:
            return
        ref = query.snapshot_ref
        build_ref = ref.build_ref if isinstance(ref, ConflictQuery) else ref
        self._conflict_filter = ConflictFilter(search, None if risk == "all" else risk)
        self.bind_page("conflicts", build_ref)


__all__ = ["TerminologyLauncher", "TerminologyWindow", "_decision_status_label"]
