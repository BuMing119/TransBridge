"""Persist a candidate Project/Variant before publishing its working-copy state."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
import errno
import logging

from transbridge.application.contracts import DomainError, ErrorCategory, OperationResult, RequestContext
from transbridge.application.tasks import TaskCancelled
from transbridge.persistence.v2.ids import ProjectRef
from transbridge.persistence.v2.variant import VariantAggregate, VariantChangeSet

from .lifecycle import ProjectLifecycleService
from .models import LifecycleSave
from .ports import LifecycleUnitOfWorkFactoryPort

_logger = logging.getLogger(__name__)


class DurableVariantCommitService:
    """Write both durable documents under the lifecycle's final publication lock."""

    def __init__(
        self,
        lifecycle: ProjectLifecycleService,
        unit_of_work_factory: LifecycleUnitOfWorkFactoryPort,
        *,
        projects=None,
        variants=None,
    ):
        self._lifecycle = lifecycle
        self._unit_of_work_factory = unit_of_work_factory
        self._projects = projects
        self._variants = variants

    def commit(
        self,
        change_set: VariantChangeSet,
        context: RequestContext,
        *,
        expected_project_revision: int,
        commit_mutation: Callable[[Callable[[], None]], None] | None = None,
    ) -> OperationResult:
        generation = self._lifecycle.generation
        active = self._lifecycle.active
        if active is None or active.variant is None or active.formal_variant_ref is None:
            return _failed("ACTIVE_VARIANT_REQUIRED", "请先打开需要保存的工程版本。", context)
        snapshot = active.variant.snapshot()
        if change_set.ref != active.formal_variant_ref:
            return _failed("ACTIVE_VARIANT_IDENTITY_CHANGED", "当前翻译版本已切换，本次未保存。", context)
        if active.project.envelope.revision != expected_project_revision:
            return _failed("ACTIVE_PROJECT_REVISION_CHANGED", "工程内容已变化，请按当前内容重试保存。", context)
        if snapshot.revision != change_set.expected_revision:
            return _failed("ACTIVE_VARIANT_REVISION_CHANGED", "本地译文已变化，请按当前内容重试保存。", context)
        try:
            candidate = VariantAggregate(snapshot)
            if (
                change_set.entries != snapshot.entries
                or change_set.source_fingerprints != snapshot.source_fingerprints
                or change_set.label_library != snapshot.label_library
            ):
                candidate.commit(change_set, context)
            save = LifecycleSave.capture(replace(active, variant=candidate))
            written = False

            def persist():
                nonlocal written
                if written:
                    raise DomainError(ErrorCategory.INTERNAL, "DURABLE_SAVE_REPEATED", "当前保存请求已执行。")
                work = None
                try:
                    work = self._unit_of_work_factory.begin()
                    work.stage_save(save)
                    work.commit()
                    written = True
                except Exception as exc:
                    if work is not None:
                        try:
                            work.rollback()
                        except Exception:
                            _logger.exception("Failed to roll back automatic Variant save")
                    if self._persisted_candidate_matches(save):
                        written = True
                        _logger.warning("Automatic Variant save recovered an already-persisted candidate", exc_info=exc)
                        return
                    raise _save_error(exc) from exc

            def guarded_publish(publish):
                published = False

                def publish_once():
                    nonlocal published
                    if published:
                        raise DomainError(ErrorCategory.INTERNAL, "DURABLE_SAVE_REPEATED", "当前保存请求已执行。")
                    publish()
                    published = True

                try:
                    publish_once() if commit_mutation is None else commit_mutation(publish_once)
                except TaskCancelled as exc:
                    raise DomainError(
                        ErrorCategory.CANCELLED,
                        "DURABLE_SAVE_CANCELLED",
                        "下载已取消，当前版本未修改；本次选择已保留。",
                        cause=exc,
                    ) from exc
                if not published:
                    raise DomainError(
                        ErrorCategory.PERMISSION,
                        "DURABLE_SAVE_NOT_AUTHORIZED",
                        "当前保存请求未获执行，版本未修改；请重试。",
                    )

            return self._lifecycle.commit_active_content(
                save.project,
                save.variant,
                context,
                expected_project_revision=expected_project_revision,
                expected_variant_revision=snapshot.revision,
                before_publish=persist,
                persisted=True,
                expected_generation=generation,
                commit_mutation=guarded_publish,
            )
        except Exception as exc:
            return OperationResult.from_exception(exc, run_id=context.run_id)

    def _persisted_candidate_matches(self, save):
        if self._projects is None or self._variants is None or save.variant is None:
            return False
        try:
            lock = self._projects.mutation_lock
            if lock is not self._variants.mutation_lock:
                return False
            with lock.reading():
                project = self._projects.read_snapshot(ProjectRef(save.variant.ref.project_id))
                variant = self._variants.read_snapshot(save.formal_variant_ref)
                return project == save.project and variant == save.variant.to_dto()
        except Exception:
            _logger.exception("Unable to verify the automatic Variant save result; retry will verify it again")
            return False


def _failed(code, message, context):
    return OperationResult.failed(DomainError(ErrorCategory.CONFLICT, code, message), run_id=context.run_id)


def _save_error(exc):
    if isinstance(exc, DomainError):
        return exc
    _logger.error("Failed to persist downloaded Variant content", exc_info=exc)
    cause, visited = exc, set()
    while cause.__cause__ is not None and id(cause) not in visited:
        visited.add(id(cause))
        cause = cause.__cause__
    if isinstance(cause, PermissionError):
        reason = "没有写入工程目录的权限，或文件正被其他程序占用"
    elif isinstance(cause, OSError) and (cause.errno == errno.ENOSPC or getattr(cause, "winerror", None) == 112):
        reason = "磁盘空间不足"
    elif isinstance(cause, OSError):
        reason = f"磁盘读写失败：{cause}"
    else:
        reason = f"无法写入工程版本：{exc}"
    return DomainError(
        ErrorCategory.INTERNAL,
        "DURABLE_VARIANT_SAVE_FAILED",
        f"自动保存当前翻译版本未确认成功：{reason}。本次选择已保留，可在当前窗口重试并重新核验保存结果。",
        retryable=True,
        cause=exc,
    )
