"""Run ownership and late-result guards for AI translator modes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from itertools import count
from typing import Literal, TypeVar
from uuid import uuid4

from PyQt6 import sip

from transbridge.application.contracts import JobRef
from transbridge.application.tasks import OwnerRef, TaskRuntime
from transbridge.application.translation.ai_request_budget import AiRequestBudget
from transbridge.ui.foundation.adapters import ThemeView
from transbridge.ui.windowing import show_and_activate

from .run_spec import AiRunSpec, FrozenExecutionConfig, build_run_spec

EntryT = TypeVar("EntryT")
RunMode = Literal["translate", "mixed", "polish", "batch"]


@dataclass(frozen=True, slots=True)
class TranslationRunRequest[EntryT]:
    spec: AiRunSpec
    config_snapshot: FrozenExecutionConfig
    entries: tuple[EntryT, ...]
    request_budget: AiRequestBudget
    runtime_ref: JobRef | None = None

    @property
    def mode(self) -> RunMode:
        return self.spec.mode  # type: ignore[return-value]

    @property
    def owner_id(self) -> str:
        return self.spec.owner.owner_id

    @property
    def run_id(self) -> str:
        return self.spec.run_id

    @property
    def generation(self) -> int:
        return self.spec.generation

    @property
    def config(self) -> object:
        """Return a private copy; preference edits cannot mutate this run."""

        return self.config_snapshot.copy()


class RunAlreadyActiveError(RuntimeError):
    """Raised when a second run is requested for the same window owner."""


@dataclass(slots=True)
class _ActiveRun:
    request: TranslationRunRequest
    worker: object | None = None
    progress: object | None = None
    activity: object | None = None


class RunController:
    """Owns a single run and releases/cancels its worker and progress UI."""

    def __init__(self, owner_id: str | None = None, *, task_runtime: TaskRuntime | None = None) -> None:
        self._owner_id = owner_id or uuid4().hex
        self._task_runtime = task_runtime
        self._ids = count(1)
        self._active: _ActiveRun | None = None
        self._closed = False

    def begin(
        self,
        mode: RunMode,
        config: object,
        entries: list[EntryT],
        *,
        overwrite: bool = False,
        esp_path: str | None = None,
        project_id: str | None = None,
        variant_id: str | None = None,
        session_id: str | None = None,
        project_revision: int | None = None,
        run_id: str | None = None,
    ) -> TranslationRunRequest[EntryT]:
        if self._closed:
            raise RuntimeError("AI translator run owner is closed")
        if self._active is not None:
            raise RunAlreadyActiveError("AI translator already has an active run")
        config_snapshot = FrozenExecutionConfig(config)
        execution_config = config_snapshot.copy()
        try:
            max_concurrent = getattr(execution_config, "max_concurrent")
        except AttributeError as exc:
            raise ValueError("config.max_concurrent must be a positive integer") from exc
        try:
            request_budget = AiRequestBudget(max_concurrent)
        except ValueError as exc:
            raise ValueError("config.max_concurrent must be a positive integer") from exc
        generation = next(self._ids)
        run_id = run_id or f"ai-{uuid4().hex}"
        owner = OwnerRef(
            owner_id=self._owner_id,
            entrypoint="ui.ai-translator",
            project_id=project_id,
            variant_id=variant_id,
            session_id=session_id,
        )
        spec = build_run_spec(
            run_id=run_id,
            generation=generation,
            mode=mode,
            owner=owner,
            config=execution_config,
            entries=entries,
            esp_path=esp_path,
            overwrite=overwrite,
            project_revision=project_revision,
        )
        runtime_ref = None
        if self._task_runtime is not None:
            from .task_adapter import runtime_job_spec

            runtime_ref = self._task_runtime.submit(runtime_job_spec(spec), owner).ref
            run_id = runtime_ref.run_id or runtime_ref.job_id
            spec = build_run_spec(
                run_id=run_id,
                generation=generation,
                mode=mode,
                owner=owner,
                config=execution_config,
                entries=entries,
                esp_path=esp_path,
                overwrite=overwrite,
                project_revision=project_revision,
            )
            self._task_runtime.start(runtime_ref, owner)
        request = TranslationRunRequest(
            spec=spec,
            config_snapshot=config_snapshot,
            entries=tuple(entries),
            request_budget=request_budget,
            runtime_ref=runtime_ref,
        )
        self._active = _ActiveRun(request)
        return request

    def create_activity(self, request: TranslationRunRequest):
        """Return the one activity bridge owned by this run."""
        if self._active is not None and self._active.activity is not None:
            return self._active.activity
        from .task_adapter import LegacyAiTaskAdapter, TaskRuntimeAiTaskAdapter

        if self._task_runtime is not None and request.runtime_ref is not None:
            activity = TaskRuntimeAiTaskAdapter(
                self._task_runtime,
                request.runtime_ref,
                request.spec.owner,
                request.spec,
            )
        else:
            activity = LegacyAiTaskAdapter(request.spec)
        if self._active is not None and self.accepts(request.run_id):
            self._active.activity = activity
        return activity

    @property
    def active_request(self) -> TranslationRunRequest | None:
        return None if self._active is None else self._active.request

    @property
    def is_running(self) -> bool:
        return self._active is not None

    def attach(
        self,
        run_id: str,
        *,
        worker: object,
        progress: object | None = None,
        activity: object | None = None,
    ) -> None:
        if not self.accepts(run_id) or self._active is None:
            self._cancel(worker)
            self._close_progress(progress)
            return
        self._active.worker = worker
        self._active.progress = progress
        self._active.activity = activity
        destroyed = getattr(worker, "destroyed", None)
        connect_destroyed = getattr(destroyed, "connect", None)
        if callable(connect_destroyed):
            connect_destroyed(lambda *_args: self.finish(run_id))
        bind_worker = getattr(activity, "bind_worker", None)
        if callable(bind_worker):
            bind_worker(worker)

    def accepts(self, run_id: str) -> bool:
        return not self._closed and self._active is not None and self._active.request.run_id == run_id

    def guard(self, run_id: str, callback: Callable) -> Callable:
        def guarded(*args: object, **kwargs: object):
            if self.accepts(run_id):
                return callback(*args, **kwargs)
            return None

        return guarded

    def terminal_guard(self, run_id: str, callback: Callable) -> Callable:
        def guarded(*args: object, **kwargs: object):
            if not self.accepts(run_id):
                return None
            self.finish(run_id)
            return callback(*args, **kwargs)

        return guarded

    def finish(self, run_id: str) -> None:
        if not self.accepts(run_id):
            return
        active = self._active
        self._active = None
        if active is not None:
            self._close_progress(active.progress)

    def cancel(self, run_id: str) -> None:
        if not self.accepts(run_id):
            return
        active = self._active
        self._active = None
        if active is not None:
            self._request_cancel(active.activity)
            self._cancel(active.worker)
            self._close_progress(active.progress)

    def close(self) -> None:
        self._closed = True
        active = self._active
        self._active = None
        if active is not None:
            self._request_cancel(active.activity)
            self._cancel(active.worker)
            self._close_progress(active.progress)

    @staticmethod
    def _cancel(worker: object | None) -> None:
        if worker is None or _qt_object_deleted(worker):
            return
        try:
            cancel = getattr(worker, "cancel", None)
            stop = getattr(worker, "stop", None)
            if callable(cancel):
                cancel()
            elif callable(stop):
                stop()
        except RuntimeError:
            if not _qt_object_deleted(worker):
                raise

    @staticmethod
    def _request_cancel(activity: object | None) -> None:
        request = getattr(activity, "request_cancel", None)
        if callable(request):
            request()

    @staticmethod
    def _close_progress(progress: object | None) -> None:
        if progress is None or _qt_object_deleted(progress):
            return
        try:
            close = getattr(progress, "close", None)
            if callable(close):
                close()
        except RuntimeError:
            if not _qt_object_deleted(progress):
                raise


def _qt_object_deleted(value: object) -> bool:
    try:
        return sip.isdeleted(value)
    except TypeError:
        return False


def try_begin_run(
    controller: RunController,
    mode: RunMode,
    config: object,
    entries: list,
    on_busy: Callable[[], None],
    **identity: object,
) -> TranslationRunRequest | None:
    try:
        return controller.begin(mode, config, entries, **identity)
    except RunAlreadyActiveError:
        on_busy()
        return None


def start_translation_run(
    controller: RunController,
    ctx: object,
    request: TranslationRunRequest,
    *,
    progress_created: Callable[[object], None],
    entry_activated: Callable[[str], None],
    theme_view: ThemeView | None = None,
) -> object:
    """Compose and start the existing single-plugin translation runtime."""
    from transbridge.ai_translator.translator import AutoTranslator, ProgressCheckpoint, TranslatorConfig

    from ._translation_progress_window import _TranslationProgressWindow
    from ._translation_worker import _TranslationWorker

    config = request.config
    checkpoint = ProgressCheckpoint.load(ctx.esp_path)
    if checkpoint is not None and checkpoint.run_id != request.run_id:
        checkpoint = None
    translator_config = TranslatorConfig(
        llm_config=config,
        esp_path=ctx.esp_path,
        overwrite=request.spec.overwrite,
    )
    from transbridge.ui.paratranz.target_context import bound_paratranz_project

    remote_project = bound_paratranz_project(ctx)
    project_id = None if remote_project is None else remote_project["id"]
    paratranz_client = None
    if remote_project:
        from transbridge.paratranz.api.paratranz_terms_api import ParatranzTermsAPI

        paratranz_client = ParatranzTermsAPI(ctx.config)
    from transbridge.ui.tools.ai_translator.workflow_log_store import WorkflowLogStore
    from transbridge.ui.tools.ai_translator.workflow_logging_client import WorkflowLoggingLLMClient

    log_store = WorkflowLogStore(ctx.esp_path, workflow="translation")
    translator = AutoTranslator(
        translator_config,
        paratranz_client,
        project_id,
        run_id_factory=lambda: request.run_id,
        request_budget=request.request_budget,
        llm_client_wrapper=lambda client: WorkflowLoggingLLMClient(
            client,
            log_store,
            channel_prefix="translate_call",
        ),
        term_llm_client_wrapper=lambda client: WorkflowLoggingLLMClient(
            client,
            log_store,
            channel_prefix="term_call",
        ),
    )
    worker = _TranslationWorker(
        translator,
        ctx.collection,
        [entry.id for entry in request.entries],
        checkpoint,
        esp_path=ctx.esp_path,
        log_store=log_store,
    )
    activity = controller.create_activity(request)
    activity.bind_worker(worker)
    progress = _TranslationProgressWindow(
        worker,
        ctx,
        entry_activated=entry_activated,
        activity=activity,
        theme_view=theme_view,
    )
    progress_created(progress)
    worker.start()
    show_and_activate(progress, deferred=True)
    return progress


def start_mixed_run(
    controller: RunController,
    request: TranslationRunRequest,
    ctx: object,
    config: object,
    translate_entries: list,
    polish_entries: list,
    *,
    finished: Callable,
    error: Callable,
    parent: object | None = None,
    progress_created: Callable[[object], None] | None = None,
    theme_view: ThemeView | None = None,
) -> object:
    from ._mixed_worker import _MixedWorker
    from .run_view import AiMixedProgressWindow
    from .task_adapter import AiLegacyRunState

    worker = _MixedWorker(
        cfg=config,
        translate_entries=translate_entries,
        polish_entries=polish_entries,
        execution_order=config.mixed_execution_order,
        ctx=ctx,
        run_id=request.run_id,
        run_spec=request.spec,
        request_budget=request.request_budget,
    )
    run_id = request.run_id
    activity = controller.create_activity(request)
    progress = AiMixedProgressWindow(
        worker,
        activity,
        parent=parent,
        profile=request.spec.execution_profile,
        theme_view=theme_view,
    )
    controller.attach(run_id, worker=worker, activity=activity)
    if progress_created is not None:
        progress_created(progress)

    def on_activity_progress(value: object) -> None:
        if hasattr(value, "overall_current"):
            activity.progress(
                int(getattr(value, "overall_current")),
                int(getattr(value, "overall_total", 1000)),
                str(getattr(value, "message", "执行中")),
            )
            return
        current = int(getattr(value, "translate_done", 0)) + int(getattr(value, "polish_done", 0))
        total = int(getattr(value, "translate_total", 0)) + int(getattr(value, "polish_total", 0))
        activity.progress(current, total, str(getattr(value, "stage", "执行中")))

    worker.progress.connect(on_activity_progress)

    def project_result(result: object) -> None:
        register_mixed_result_actions(progress, request.spec, result)

    worker.finished.connect(project_result)
    worker.finished.connect(
        lambda _result: activity.finish(cancelled=activity.activity.state is AiLegacyRunState.CANCELLING)
    )
    worker.cancelled.connect(controller.terminal_guard(run_id, lambda: activity.finish(cancelled=True)))
    worker.error.connect(activity.fail)
    worker.finished.connect(controller.terminal_guard(run_id, finished))
    worker.error.connect(controller.terminal_guard(run_id, error))
    worker.finished.connect(lambda _result: worker.deleteLater())
    worker.error.connect(lambda _message: worker.deleteLater())
    worker.cancelled.connect(worker.deleteLater)
    try:
        worker.start()
        show_and_activate(progress, deferred=True)
    except Exception:
        controller.cancel(run_id)
        activity.finish(cancelled=True)
        raise
    return progress


def register_mixed_result_actions(progress: object, spec: AiRunSpec, result: object) -> None:
    """Project mixed failures and its durable Excel artifact onto the progress view."""
    from types import SimpleNamespace

    from .result_actions import AiResultNavigator, result_action_state

    failed: list[str] = []
    translate = result.get("translate") if isinstance(result, dict) else None
    failed.extend(str(value) for value in getattr(translate, "failed_entries", ()) if str(value))
    polish = result.get("polish") if isinstance(result, dict) else None
    for detail in getattr(polish, "details", ()) or ():
        if not detail.get("success", False):
            failed.append(str(detail.get("entry_id") or detail.get("key") or ""))
    artifacts = result.get("artifacts") if isinstance(result, dict) else None
    navigator = AiResultNavigator()
    artifact = navigator.register_report(spec, getattr(artifacts, "excel_path", None))
    progress.result_navigator = navigator
    progress.set_result_actions(
        result_action_state(
            spec,
            result=SimpleNamespace(failed_entries=tuple(value for value in failed if value)),
            report=artifact,
        )
    )


def show_polish_progress(
    controller: RunController,
    request: TranslationRunRequest,
    parent: object,
    worker: object,
    entries: list,
    *,
    on_results: Callable[[object], None],
    preview: bool,
    theme_view: ThemeView | None = None,
) -> object:
    """Compose the detailed standalone-proofreading progress surface."""
    from .workflow_progress_runtime import show_polish_progress as show_progress

    return show_progress(
        controller,
        request,
        parent,
        worker,
        entries,
        on_results=on_results,
        preview=preview,
        theme_view=theme_view,
    )
