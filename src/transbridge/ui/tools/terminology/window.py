"""Top-level terminology workbench and its narrow shell launcher."""

from __future__ import annotations

from dataclasses import replace
import threading

from PyQt6.QtCore import QObject, QRunnable, Qt, QThreadPool, pyqtSignal
from PyQt6.QtWidgets import QInputDialog, QMessageBox, QVBoxLayout, QWidget

from transbridge.application.contracts import JobRef, RequestContext
from transbridge.application.tasks import JobState
from transbridge.application.terminology import Page
from transbridge.application.terminology.conflicts import ConflictResolutionOperation
from transbridge.application.terminology.workloads import TerminologyWorkloadType
from transbridge.ui.windowing import show_and_activate

from .build_view import BuildView
from .conflicts_view import ConflictsView
from .draft_view import DraftView
from .history_view import HistoryView
from .object_views import TermsView, VersionsView
from .paged_models import KeysetPagedTableModel, PagedColumn
from .presenter import TerminologyPresenter, TerminologyUiServices
from .reports_view import ReportsView
from .task_adapter import TerminologyTaskViewState
from .view_models import TerminologyArea, TerminologyPreflightViewState, business_diagnostic, phase_label
from .workbench_shell import TerminologyWorkbenchShell


class _CallSignals(QObject):
    succeeded = pyqtSignal(int, object)
    failed = pyqtSignal(int, object)


class _CallRunnable(QRunnable):
    def __init__(self, generation: int, call, signals: _CallSignals, cancelled: threading.Event) -> None:
        super().__init__()
        self._generation = generation
        self._call = call
        self._signals = signals
        self._cancelled = cancelled

    def run(self) -> None:
        if self._cancelled.is_set():
            return
        try:
            result = self._call()
        except Exception as exc:  # noqa: BLE001 - application adapter boundary
            if not self._cancelled.is_set():
                self._signals.failed.emit(self._generation, exc)
            return
        if not self._cancelled.is_set():
            self._signals.succeeded.emit(self._generation, result)


def _empty_page(_ref: object, _request) -> Page[object]:
    return Page((), "terminology-ui-unavailable", total=0)


class TerminologyWindow(QWidget):
    """One Project/Variant-scoped window; closing detaches UI ownership only."""

    task_changed = pyqtSignal(object)

    def __init__(self, presenter: TerminologyPresenter, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.presenter = presenter
        self.setWindowTitle("项目术语工作台")
        self.resize(1180, 760)
        self.setMinimumSize(920, 620)
        self.setAccessibleName("项目术语工作台")
        self._pool = QThreadPool.globalInstance()
        self._calls = _CallSignals()
        self._calls.succeeded.connect(self._preflight_ready, Qt.ConnectionType.QueuedConnection)
        self._calls.failed.connect(self._preflight_failed, Qt.ConnectionType.QueuedConnection)
        self._command_calls = _CallSignals()
        self._command_calls.succeeded.connect(self._command_ready, Qt.ConnectionType.QueuedConnection)
        self._command_calls.failed.connect(self._command_failed, Qt.ConnectionType.QueuedConnection)
        self._call_generation = 0
        self._cancelled = threading.Event()
        self._command_generation = 0
        self._command_cancelled: dict[int, threading.Event] = {}
        self._command_messages: dict[int, str] = {}
        self._task_refs: dict[str, JobRef] = {}
        self._models: list[KeysetPagedTableModel] = []
        self._closed = False
        self._init_ui()
        self.task_changed.connect(self._on_task_change, Qt.ConnectionType.QueuedConnection)
        presenter.bind_tasks(self.task_changed.emit)

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.workspace = TerminologyWorkbenchShell(self)
        layout.addWidget(self.workspace, 1)

        self.conflicts_model = self._model(
            "list_conflicts",
            (
                PagedColumn("original", "原名", lambda item: getattr(item, "normalized_original", "")),
                PagedColumn("variants", "不同译法", lambda item: len(getattr(item, "variants", ()))),
                PagedColumn("risk", "处理优先级", lambda item: _risk_label(getattr(item, "risk", ""))),
                PagedColumn("status", "状态", lambda item: _status_label(getattr(item, "status", ""))),
            ),
        )
        self.conflicts_view = ConflictsView(self.conflicts_model, self)
        self.conflicts_view.query_changed.connect(self._replace_conflict_query)
        self.conflicts_view.review_requested.connect(self._review_conflict)

        self.draft_model = self._model(
            "list_draft_terms",
            (
                PagedColumn("original", "原名", lambda item: getattr(item, "original", "")),
                PagedColumn("translation", "推荐译名", lambda item: getattr(item, "translation", "")),
                PagedColumn("scope", "使用范围", lambda item: _scope_label(getattr(item, "scope", None))),
                PagedColumn(
                    "state", "状态", lambda item: "不再使用" if getattr(item, "suppressed", False) else "使用中"
                ),
            ),
        )
        self.draft_view = DraftView(self.draft_model, self)
        self.draft_view.add_requested.connect(self._add_term)
        self.draft_view.edit_requested.connect(self._edit_term)
        self.draft_view.suppress_requested.connect(self._toggle_suppression)

        self.history_model = self._model(
            "list_versions",
            (
                PagedColumn("version", "版本", lambda item: getattr(item, "version_id", "")),
                PagedColumn("digest", "内容", lambda _item: "已发布术语库"),
            ),
        )
        self.history_view = HistoryView(self.history_model, self)
        self.history_view.compare_requested.connect(self._compare_history)
        self.history_view.restore_requested.connect(self._restore_history)

        self.build_view = BuildView(self)
        self.build_view.preflight_requested.connect(self.run_preflight)
        self.build_view.build_requested.connect(self._start_build)
        self.build_view.cancel_requested.connect(self._cancel_latest)
        self.build_view.terms_requested.connect(lambda: self.workspace.set_current_area(TerminologyArea.TERMS))
        self.build_view.versions_requested.connect(lambda: self.workspace.set_current_area(TerminologyArea.VERSIONS))
        self.terms_view = TermsView(self.draft_view, self.conflicts_view, self)
        self.versions_view = VersionsView(self.history_view, self)
        self.versions_view.publish_requested.connect(self._publish)
        self.draft_view.publish_requested.connect(lambda: self.workspace.set_current_area(TerminologyArea.VERSIONS))
        self.publish_status = self.versions_view.publish_status
        self.publish_details = self.versions_view.publish_details
        self.reports_view = ReportsView(self)
        self.reports_view.quality_report_requested.connect(self._render_report)
        self.reports_view.changelog_requested.connect(self._render_changelog)
        self.reports_view.retry_requested.connect(self._retry_changelog)
        self.workspace.add_area(TerminologyArea.OVERVIEW, self.build_view)
        self.workspace.add_area(TerminologyArea.TERMS, self.terms_view)
        self.workspace.add_area(TerminologyArea.VERSIONS, self.versions_view)
        self.workspace.add_area(TerminologyArea.REPORTS, self.reports_view)

    def _model(self, method: str, columns: tuple[PagedColumn, ...]) -> KeysetPagedTableModel:
        try:
            loader = self.presenter.page_loader(method)
        except RuntimeError:
            loader = _empty_page
        model = KeysetPagedTableModel(loader, columns, self, page_size=100, max_cached_pages=3)
        self._models.append(model)
        return model

    def run_preflight(self) -> None:
        if self._closed:
            return
        self._call_generation += 1
        self._cancelled.set()
        self._cancelled = threading.Event()
        generation = self._call_generation
        self.build_view.preflight_button.setEnabled(False)
        self.build_view.message.setText("正在检查当前工程、翻译版本和来源…")
        self._pool.start(_CallRunnable(generation, self.presenter.preflight, self._calls, self._cancelled))

    def bind_page(self, section: str, snapshot_ref: object, *, query_fingerprint: str = "all") -> None:
        models = {
            "conflicts": self.conflicts_model,
            "draft": self.draft_model,
            "history": self.history_model,
        }
        models[section].set_query(snapshot_ref, query_fingerprint=query_fingerprint)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        if self.presenter.snapshot is None and self._call_generation == 0:
            self.run_preflight()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if not self._closed:
            self._closed = True
            self._call_generation += 1
            self._cancelled.set()
            for cancelled in self._command_cancelled.values():
                cancelled.set()
            self._command_cancelled.clear()
            self._command_messages.clear()
            for model in self._models:
                model.close()
            self.presenter.close()
        super().closeEvent(event)

    def _preflight_ready(self, generation: int, state: object) -> None:
        if self._closed or generation != self._call_generation:
            return
        self.build_view.preflight_button.setEnabled(True)
        self.build_view.set_preflight(state)
        self.workspace.set_context(state.project_display_name, state.variant_display_name, len(state.sources))
        self.versions_view.set_context(state.current_version_value)
        self._bind_existing_assets()

    def _preflight_failed(self, generation: int, error: object) -> None:
        if self._closed or generation != self._call_generation:
            return
        self.build_view.preflight_button.setEnabled(True)
        notice = self.presenter.notice("TERMINOLOGY_PREFLIGHT_FAILED", str(error))
        self.build_view.set_preflight(TerminologyPreflightViewState.unavailable(f"{notice.message} {notice.recovery}"))

    def _bind_existing_assets(self) -> None:
        project_id = self.presenter.context.project_id
        variant_id = self.presenter.context.variant_id
        if project_id is None or variant_id is None:
            return
        self.bind_page("history", (project_id, variant_id))
        commands = self.presenter.services.commands
        if commands is None:
            return
        latest = getattr(commands, "latest_build_ref", lambda *_args: None)(project_id, variant_id)
        if latest is not None:
            self.bind_page("conflicts", latest)
        try:
            draft = getattr(commands, "active_draft", lambda *_args: None)(self.presenter.context)
        except (PermissionError, RuntimeError):
            draft = None
        if draft is not None:
            self.bind_page("draft", draft.ref)

    def _start_build(self) -> None:
        self._run_command(self.presenter.start_build, "构建任务已开始，可在此窗口或任务中心查看进度。")

    def _publish(self) -> None:
        self._run_command(self.presenter.publish, "发布任务已开始。")

    def _render_report(self) -> None:
        self._run_command(self.presenter.render_report, "质量报告生成任务已开始。")

    def _render_changelog(self) -> None:
        self._run_command(self.presenter.render_changelog, "更新日志生成任务已开始。")

    def _retry_changelog(self) -> None:
        self._run_command(self.presenter.retry_changelog, "正在从已保存的发布记录重新生成更新日志。")

    def _add_term(self) -> None:
        original, accepted = QInputDialog.getText(self, "新增术语", "原名")
        if not accepted or not original.strip():
            return
        translation, accepted = QInputDialog.getText(self, "新增术语", "推荐译名")
        if not accepted or not translation.strip():
            return
        self._run_draft_command(lambda: self.presenter.add_term(original.strip(), translation.strip()))

    def _edit_term(self, decision: object) -> None:
        term_identity = str(getattr(decision, "term_id", ""))
        if not term_identity:
            return
        current = str(getattr(decision, "translation", ""))
        translation, accepted = QInputDialog.getText(self, "调整译名", "推荐译名", text=current)
        if accepted and translation.strip() and translation.strip() != current:
            self._run_draft_command(lambda: self.presenter.change_translation(term_identity, translation.strip()))

    def _toggle_suppression(self, decision: object) -> None:
        term_identity = str(getattr(decision, "term_id", ""))
        if not term_identity:
            return
        suppressed = bool(getattr(decision, "suppressed", False))
        action = "重新启用" if suppressed else "不再使用"
        answer = QMessageBox.question(
            self,
            action,
            f"确定要{action}“{getattr(decision, 'original', '')}”吗？",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._run_draft_command(lambda: self.presenter.set_suppressed(term_identity, suppressed=not suppressed))

    def _review_conflict(self, conflict: object) -> None:
        choices = ("统一译名", "按插件分别使用", "暂不处理")
        choice, accepted = QInputDialog.getItem(self, "处理异译", "处理方式", choices, 0, False)
        if not accepted:
            return
        if choice == "暂不处理":
            self._run_draft_command(
                lambda: self.presenter.resolve_conflict(conflict, ConflictResolutionOperation.IGNORE)
            )
            return
        variants = tuple(str(getattr(item, "normalized_translation", "")) for item in getattr(conflict, "variants", ()))
        translation, accepted = QInputDialog.getItem(self, "处理异译", "采用译名", variants, 0, True)
        if not accepted or not translation.strip():
            return
        if choice == "统一译名":
            self._run_draft_command(
                lambda: self.presenter.resolve_conflict(
                    conflict,
                    ConflictResolutionOperation.UNIFY,
                    translation=translation.strip(),
                )
            )
            return
        plugin_id, accepted = QInputDialog.getText(self, "插件特例", "插件标识")
        if accepted and plugin_id.strip():
            self._run_draft_command(
                lambda: self.presenter.resolve_conflict(
                    conflict,
                    ConflictResolutionOperation.PLUGIN_EXCEPTION,
                    translation=translation.strip(),
                    plugin_id=plugin_id.strip(),
                )
            )

    def _compare_history(self, version_ref: object) -> None:
        self._run_command(lambda: self.presenter.compare(version_ref), "版本比较任务已开始。")
        self.history_view.set_status("正在比较所选历史版本与当前版本…")

    def _restore_history(self, version_ref: object) -> None:
        answer = QMessageBox.question(
            self,
            "恢复历史内容",
            "恢复会创建一个新版本；当前版本和中间历史都会保留。是否继续？",
        )
        if answer is QMessageBox.StandardButton.Yes:
            self._run_command(lambda: self.presenter.restore(version_ref), "历史内容恢复任务已开始。")

    def _run_draft_command(self, command) -> None:
        try:
            result = command()
        except Exception as exc:  # noqa: BLE001 - command boundary is projected to the user
            self._show_command_error(exc)
            return
        draft = getattr(result, "draft", result)
        ref = getattr(draft, "ref", None)
        if ref is not None:
            self.bind_page("draft", ref)
        self.publish_status.setText("人工调整已保存，并已记录到当前草稿。")

    def _run_command(self, command, message: str) -> None:
        if self._closed:
            return
        self._command_generation += 1
        generation = self._command_generation
        cancelled = threading.Event()
        self._command_cancelled[generation] = cancelled
        self._command_messages[generation] = message
        self.build_view.message.setText("正在准备并提交任务…")
        self._pool.start(_CallRunnable(generation, command, self._command_calls, cancelled))

    def _command_ready(self, generation: int, value: object) -> None:
        if self._closed or generation not in self._command_cancelled:
            return
        self._command_cancelled.pop(generation, None)
        message = self._command_messages.pop(generation, "任务已开始。")
        if not isinstance(value, JobRef):
            self._show_command_error(TypeError("术语命令必须返回任务引用"))
            return
        ref = value
        run_id = ref.run_id or ref.job_id
        self._task_refs[run_id] = ref
        self.build_view.message.setText(message)

    def _command_failed(self, generation: int, error: object) -> None:
        if self._closed or generation not in self._command_cancelled:
            return
        self._command_cancelled.pop(generation, None)
        self._command_messages.pop(generation, None)
        self._show_command_error(error if isinstance(error, Exception) else RuntimeError(str(error)))

    def _show_command_error(self, error: Exception) -> None:
        notice = business_diagnostic(str(getattr(error, "code", "TERMINOLOGY_COMMAND_FAILED")), str(error))
        self.reports_view.set_notice(notice)
        self.publish_status.setText(f"{notice.title}：{notice.message} {notice.recovery}")
        self.publish_details.set_details(notice.technical_details)

    def _cancel_latest(self) -> None:
        if self._task_refs:
            self.presenter.cancel_task(next(reversed(self._task_refs.values())))

    def _on_task_change(self, value: object) -> None:
        if self._closed or not isinstance(value, TerminologyTaskViewState):
            return
        status = value.message
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
        if value.is_terminal:
            self._task_refs.pop(value.run_id, None)
        if value.state is JobState.FAILED and value.workload_type is TerminologyWorkloadType.CHANGELOG_RENDER:
            self.reports_view.set_notice(business_diagnostic("CHANGELOG_RENDER_FAILED"))
        if value.state is JobState.COMPLETED:
            commands = self.presenter.services.commands
            project_id = self.presenter.context.project_id
            variant_id = self.presenter.context.variant_id
            if commands is None or project_id is None or variant_id is None:
                return
            if value.workload_type is TerminologyWorkloadType.BUILD:
                latest = getattr(commands, "latest_build_ref", lambda *_args: None)(project_id, variant_id)
                if latest is not None:
                    self.bind_page("conflicts", latest)
                result = getattr(commands, "latest_build_result", lambda *_args: None)(project_id, variant_id)
                if result is not None:
                    self.build_view.set_summary(self.presenter.project_build(result))
                draft = getattr(commands, "active_draft", lambda *_args: None)(self.presenter.context)
                if draft is not None:
                    self.bind_page("draft", draft.ref)
            elif value.workload_type is TerminologyWorkloadType.PUBLISH:
                latest = getattr(commands, "latest_version_ref", lambda *_args: None)(project_id, variant_id)
                if latest is not None:
                    self.draft_model.clear()
                    self.bind_page("history", (project_id, variant_id))
            elif value.workload_type is TerminologyWorkloadType.HISTORY_COMPARE:
                comparison = getattr(commands, "latest_comparison", lambda *_args: None)(project_id, variant_id)
                if comparison is not None:
                    self.history_view.set_status(f"比较完成：共 {len(comparison.changes)} 项变化。")

    def _replace_conflict_query(self, search: str, risk: str) -> None:
        query = getattr(self.conflicts_model, "_query", None)
        if query is None:
            return
        fingerprint = f"search={search.casefold()}|risk={risk}"
        self.conflicts_model.set_query(query.snapshot_ref, query_fingerprint=fingerprint)


class TerminologyLauncher:
    """Resolve a narrow service bundle and retain one window per shell host."""

    def __init__(self, host: object) -> None:
        self._host = host
        self._window: TerminologyWindow | None = None

    @property
    def window(self) -> TerminologyWindow | None:
        return self._window

    def open(self) -> TerminologyWindow | None:
        if self._window is not None and not self._window.presenter.closed:
            return show_and_activate(self._window)
        runtime = getattr(self._host, "app_runtime", None)
        if runtime is None:
            getattr(self._host, "show_message")("术语工作台需要应用运行服务，当前不可用。")
            return None
        context = _terminology_context(self._host, runtime)
        services = TerminologyUiServices.from_runtime(runtime, context)
        parent = self._host if isinstance(self._host, QWidget) else None
        window = TerminologyWindow(TerminologyPresenter(services, context), parent)
        identity = id(window)
        window.destroyed.connect(lambda _obj=None: self._clear(identity))
        self._window = window
        show_and_activate(window)
        return window

    def close(self) -> None:
        if self._window is not None:
            self._window.close()
            self._window.deleteLater()
            self._window = None

    def _clear(self, identity: int) -> None:
        if self._window is not None and id(self._window) == identity:
            self._window = None


def _terminology_context(host: object, runtime: object) -> RequestContext:
    ui_context = getattr(host, "context", None)
    identity = None if ui_context is None else getattr(ui_context, "active_version_identity", None)
    project_id, variant_id = identity if identity is not None else (None, None)
    existing = getattr(host, "runtime_context", None)
    if isinstance(existing, RequestContext):
        metadata = dict(existing.metadata)
        metadata.setdefault("entrypoint", "gui")
        metadata.setdefault("manual_actor_id", existing.owner_id)
        _add_display_context(metadata, ui_context)
        return replace(
            existing,
            project_id=project_id,
            variant_id=variant_id,
            metadata=tuple(sorted(metadata.items())),
        )
    metadata = {"entrypoint": "gui", "manual_actor_id": "local-gui-user"}
    _add_display_context(metadata, ui_context)
    return runtime.context(
        "terminology-workbench",
        project_id=project_id,
        variant_id=variant_id,
        metadata=tuple(sorted(metadata.items())),
    )


def _add_display_context(metadata: dict[str, str], ui_context: object | None) -> None:
    if ui_context is None:
        return
    project_name = getattr(ui_context, "project_name", None)
    if project_name:
        metadata.setdefault("project_name", str(project_name))
    variants = tuple(getattr(ui_context, "project_variants", ()))
    active = next((item for item in variants if item.get("active")), None)
    variant_name = None if active is None else active.get("name")
    if variant_name:
        metadata.setdefault("variant_name", str(variant_name))


def _risk_label(value: object) -> str:
    return {"high": "优先处理", "medium": "一般", "low": "较低"}.get(str(value), str(value))


def _status_label(value: object) -> str:
    return {
        "unresolved": "待决定",
        "unified": "已统一",
        "plugin_exception": "按插件分别使用",
        "ignored": "暂不处理",
    }.get(str(value), str(value))


def _scope_label(scope: object) -> str:
    plugin_id = getattr(scope, "plugin_id", None)
    return "当前工程" if plugin_id is None else f"仅在 {plugin_id} 中使用"


__all__ = ["TerminologyLauncher", "TerminologyWindow"]
