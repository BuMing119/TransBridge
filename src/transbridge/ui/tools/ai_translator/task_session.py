"""Version transactions for explicitly selected, detached AI task results."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from transbridge.application.io.identity import EntryKey
from transbridge.application.translation.result_merge import ResultConflictPolicy
from transbridge.application.translation.task_merge_evidence import TaskMergeEvidenceStore
from transbridge.application.translation.task_scope import SourceTask
from transbridge.config.task_results import TaskResultPreferences
from transbridge.ui.version_persistence import VersionPersistence
from transbridge.ui.workers import ApiWorker

from .task_apply_preparation import ApplyDelivery
from .task_session_merge import accept_result, apply_result, freeze_application, freeze_changes
from .task_session_preparation import (
    SessionCapture,
    SessionPreparationJob,
)
from .version_operations import require_success


class TaskSession:
    """Publish selected drafts atomically while guarding against external version edits."""

    def __init__(
        self, ctx: object, tasks: tuple[SourceTask, ...], spec: object, *, project_dir: Path | None = None
    ) -> None:
        capture = SessionCapture(ctx, tasks, spec)
        for _ in capture.steps():
            pass
        prepared = capture.prepare()
        capture.require_current()
        self._initialize(ctx, spec, capture, prepared, project_dir=project_dir)

    @classmethod
    def create_async(
        cls, ctx, tasks, spec, *, project_dir=None, on_success, on_error, parent=None, selection_intent=None
    ):
        """Capture GUI inputs in slices, then build drafts and initial results off-thread."""
        capture = SessionCapture(ctx, tasks, spec, selection_intent=selection_intent)

        def assemble(prepared, captured):
            session = cls.__new__(cls)
            session._initialize(ctx, spec, captured, prepared, project_dir=project_dir)
            return session

        return SessionPreparationJob(capture, assemble, on_success, on_error, parent=parent)

    def _initialize(self, ctx, spec, capture, prepared, *, project_dir):
        self._ctx = ctx
        self._identity = capture.identity
        self.version_label = next(
            (
                str(item.get("name") or item.get("id"))
                for item in getattr(ctx, "project_variants", ())
                if self._identity is not None and str(item.get("id")) == str(self._identity[1])
            ),
            str(self._identity[1]) if self._identity is not None else "",
        )
        if self._identity is None:
            raise RuntimeError("请先打开一个项目版本，再创建 AI 任务。")
        self._project_dir = Path(project_dir) if project_dir is not None else None
        self._persistence = VersionPersistence(ctx, self._identity)
        provider = getattr(ctx, "task_result_preferences_provider", None) or TaskResultPreferences
        self.policy = ResultConflictPolicy(provider().ai_policy)
        self.merge_evidence_writer = (
            TaskMergeEvidenceStore(self._project_dir / "ai-task-recovery", str(getattr(spec, "run_id", "run")))
            if self._project_dir is not None
            else None
        )
        self._worker: ApiWorker | None = None
        self._apply_delivery: ApplyDelivery | None = None
        self._captured = self._completed = self._saved = self._discarded = False
        self._commit_result = None
        self.commit_mutation: Callable[[Callable[[], None]], None] | None = None
        self._applied_keys: set[EntryKey] = set()
        self._applied_values = {}
        self._states = prepared.states
        self._draft = prepared.draft
        self.initial_entry_results = prepared.initial_entry_results
        self.selection_intent = None if capture.selection_capture is None else capture.selection_capture.intent
        recovery = None if self.selection_intent is None else self.selection_intent.recovery
        if recovery is not None and not recovery[1]:
            self.policy = ResultConflictPolicy(recovery[0].get("conflict_policy", self.policy))
        self.tasks = self._draft.tasks

    @property
    def is_busy(self) -> bool:
        return self._worker is not None

    @property
    def completed(self) -> bool:
        return self._completed

    @property
    def saved(self) -> bool:
        return self._saved

    @property
    def project_saved(self) -> bool:
        return (self._saved or bool(self._persistence.project_saved)) and self._applied_values_match()

    @property
    def project_saved_signal(self):
        """Project dirty-state notifications; callers must verify with observe_project_saved."""
        return getattr(self._ctx, "dirty_changed", None)

    def observe_project_saved(self) -> bool:
        """Latch a verified external save without treating a different clean version as saved."""
        if not self._applied_keys or self.project_saved or getattr(self._ctx, "dirty", None) is not False:
            return False
        try:
            self._require_current(allow_discarded=True)
        except RuntimeError:
            # A different revision/project cannot establish durability of our captured result.
            return False
        if not self._applied_values_match():
            return False
        self._persistence.mark_project_saved()
        return True

    @property
    def version_identity(self) -> tuple[str, str]:
        return self._identity

    @property
    def project_dir(self) -> Path | None:
        return self._project_dir

    @property
    def history_dir(self) -> Path | None:
        return None if self._project_dir is None else self._project_dir / "ai-task-history"

    @property
    def can_save(self) -> bool:
        return bool(self._applied_keys) and not self._saved

    @property
    def applied_keys(self) -> frozenset[EntryKey]:
        return frozenset(self._applied_keys)

    def require_current(self) -> None:
        """Validate the current project inputs before applying or dispatching retries."""
        self._require_current()

    def bind_run_identity(self, run_id: str) -> None:
        """Bind every output to the admitted runtime ID before any task writes."""
        self._require_current()
        if self._captured or self.is_busy or self._applied_keys:
            raise RuntimeError("任务已经开始，不能重新绑定执行身份。")
        self.initial_entry_results.run_id = run_id
        self.merge_evidence_writer = (
            TaskMergeEvidenceStore(self._project_dir / "ai-task-recovery", run_id)
            if self._project_dir is not None
            else None
        )

    def capture_before(
        self, *, on_success: Callable, on_error: Callable[[str], None], background_validate: Callable | None = None
    ) -> None:
        self._require_current()
        self._captured = True
        on_success({"task_inputs_captured": True})

    def finish(self) -> None:
        """Seal a task after its ledger has no remaining executable entries."""
        self._require_current()
        self._completed = True

    def apply_entries(self, keys: set[EntryKey]) -> object:
        """Commit an explicit subset once without ending the remaining task."""
        keys = self._application_keys(keys)
        if not keys:
            return self._commit_result
        return apply_result(self, freeze_changes(self._draft, keys))

    def _application_keys(self, keys: set[EntryKey]) -> set[EntryKey]:
        self._require_current()
        if self.is_busy:
            raise RuntimeError("版本快照操作正在进行，请稍候。")
        if not self._captured:
            raise RuntimeError("执行前版本快照尚未完成，不能提交 AI 结果。")
        keys = set(keys)
        self._draft.require_keys(keys)
        keys.difference_update(self._applied_keys)
        if not keys:
            return keys
        if self._completed:
            raise RuntimeError("AI 任务已经结束，不能继续应用结果。")
        return keys

    def apply_entries_async(
        self,
        keys: set[EntryKey],
        *,
        on_success: Callable,
        on_error: Callable[[str], None],
        validate: Callable | None = None,
        background_validate: Callable | None = None,
    ) -> None:
        """Prepare detached inputs off-thread, then validate and commit on the GUI thread."""
        keys = self._application_keys(keys)
        if not keys:
            on_success(self._commit_result)
            return
        if validate is not None:
            validate()
        frozen = freeze_changes(self._draft, keys)
        merge_operation = freeze_application(self, frozen)

        def operation():
            if background_validate is not None:
                background_validate()
            return require_success(merge_operation())

        worker = ApiWorker(operation, route_http_errors=False)

        def completed(entries, error):
            # finished has arrived: callbacks may safely begin a subsequent save.
            self._worker = None
            self._apply_delivery = None
            try:
                if error is not None:
                    raise RuntimeError(error)
                if entries is None:
                    raise RuntimeError("应用准备线程未返回结果。")
                if self._ctx.active_version_identity == self._identity:
                    self._persistence.acknowledge_merge(entries)
                result = accept_result(self, entries.value, frozen)
            except Exception as exc:
                on_error(str(exc))
                return
            on_success(result)

        self._worker = worker
        self._apply_delivery = ApplyDelivery(worker, completed)
        worker.start()

    def save_translation(
        self, *, on_success: Callable, on_error: Callable[[str], None], background_validate: Callable | None = None
    ) -> None:
        self._require_current(allow_discarded=True)
        if not self._applied_keys:
            raise RuntimeError("任务结果尚未应用，不能保存翻译。")
        if self._saved:
            on_success({"already_saved": True})
            return
        operation = self._persistence.freeze_save_project()

        def saved(result):
            try:
                self._require_current(allow_discarded=True)
                self._persistence.acknowledge_save()
                if not self._applied_values_match():
                    raise RuntimeError("当前项目已保存，但部分任务结果已被后续编辑替换；AI 成果仍保留。")
            except RuntimeError as exc:
                on_error(str(exc))
                return
            self._saved = True
            on_success(result)

        def failed(message):
            try:
                self._require_current(allow_discarded=True)
                self._persistence.acknowledge_save()
            except RuntimeError:
                # A different version must not inherit this operation's clean flag.
                pass
            on_error(message)

        writer = self.merge_evidence_writer

        def persist():
            if writer is not None:
                writer.retry_receipts()
            return require_success(operation())

        self._run(
            persist,
            on_success=saved,
            on_error=failed,
            background_validate=background_validate,
        )

    def rollback_uncommitted(self) -> None:
        if not self._completed:
            self._discarded = True

    def retry_entries(self, keys: set[EntryKey]) -> tuple[SourceTask, ...]:
        """Return only selected execution inputs; prior successful drafts remain intact."""
        keys = self._retry_keys(keys)
        selected = self._draft.retry(keys)
        self.tasks = self._draft.tasks
        return selected

    def _retry_keys(self, keys):
        self._require_current()
        if self.is_busy:
            raise RuntimeError("版本快照操作正在进行，请稍候。")
        if self._completed:
            raise RuntimeError("AI 任务已经提交，不能重试条目。")
        keys = set(keys)
        if keys & self._applied_keys:
            raise ValueError("已应用的条目不能重试。")
        self._draft.require_keys(keys)
        return keys

    def retry_entries_async(self, keys, *, on_success, on_error):
        """Prepare a new draft privately, then swap it after a final GUI guard."""
        keys = self._retry_keys(keys)
        draft = self._draft

        def prepared(result):
            try:
                self._require_current()
                self._draft, selected = result
                self.tasks = self._draft.tasks
            except Exception as exc:
                on_error(str(exc))
                return
            on_success(selected)

        self._run(lambda: draft.prepare_retry(keys), on_success=prepared, on_error=on_error)

    def _require_current(self, *, allow_discarded: bool = False) -> None:
        if self._discarded and not allow_discarded:
            raise RuntimeError("AI 任务已取消，可在结果记录中处理已保存的成果。")
        if self._ctx.active_version_identity != self._identity:
            raise RuntimeError("活动项目或版本已变化，不能提交本次 AI 结果。")

    def _applied_values_match(self):
        if self._ctx.active_version_identity != self._identity:
            return False
        for key, expected in self._applied_values.items():
            current = next(
                (slot.collection.get(key) for slot in self._ctx.slots.values() if slot.collection.get(key)), None
            )
            if (
                current is None
                or (current.original, current.translation, current.stage, current.context or "") != expected
            ):
                return False
        return True

    def _run(
        self, operation: Callable, *, on_success: Callable, on_error: Callable[[str], None], background_validate=None
    ):
        if self._worker is not None:
            raise RuntimeError("版本快照操作正在进行，请稍候。")

        def validated_operation():
            if background_validate is not None:
                background_validate()
            result = operation()
            if background_validate is not None:
                background_validate()
            return result

        worker = ApiWorker(validated_operation, route_http_errors=False)
        self._worker = worker

        def completed(result, error):
            self._worker = None
            self._apply_delivery = None
            if error is not None:
                on_error(error)
            else:
                on_success(result)

        self._apply_delivery = ApplyDelivery(worker, completed)
        worker.start()


__all__ = ["TaskSession"]
