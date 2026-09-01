"""Typed task-center actions over immutable history and recovery catalogs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import threading
from typing import Protocol

from transbridge.application.contracts import JobRef

from .activity import TaskActionAvailability, TaskNavigationIntent, TaskOwnerScope
from .history import TASKS_MANAGE_PERMISSION, TaskHistoryPort, TaskHistoryRecord
from .models import OwnerRef
from .recovery import RecoveryCatalog, TaskRecoveryAvailability
from .retry import TaskRetryContext, TaskRetryIntentRegistry


class TaskCenterAction(StrEnum):
    RECOVER = "recover"
    RETRY = "retry"
    OPEN_RESULT = "open_result"
    OPEN_LOG = "open_log"


class TaskCenterActionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class TaskCenterItem:
    """Safe UI projection; the underlying record remains behind the use case."""

    source: str
    key: str
    run_id: str | None
    display_name: str
    state_label: str
    revision: int | None
    available_actions: TaskActionAvailability
    reason_message: str = ""


@dataclass(frozen=True, slots=True)
class TaskCenterActionResult:
    job_ref: JobRef | None = None
    navigation: TaskNavigationIntent | None = None


class TaskRecoveryIntent(Protocol):
    def __call__(self, candidate: TaskRecoveryAvailability, actor: OwnerRef) -> JobRef: ...


class TaskHistoryNavigator(Protocol):
    def resolve(
        self,
        record: TaskHistoryRecord,
        actor: OwnerRef,
        action: TaskCenterAction,
    ) -> TaskNavigationIntent | None: ...


class TaskRecoveryIntentRegistry:
    """Feature-owned checkpoint recovery handlers keyed by authoritative job type."""

    def __init__(self) -> None:
        self._handlers: dict[str, TaskRecoveryIntent] = {}
        self._lock = threading.RLock()

    def register(self, job_type: str, handler: TaskRecoveryIntent) -> None:
        if not job_type.strip():
            raise ValueError("recovery job_type must not be empty")
        with self._lock:
            existing = self._handlers.get(job_type)
            if existing is not None and existing is not handler:
                raise ValueError(f"recovery intent for {job_type!r} is already registered")
            self._handlers[job_type] = handler

    def supports(self, job_type: str) -> bool:
        with self._lock:
            return job_type in self._handlers

    def recover(self, candidate: TaskRecoveryAvailability, actor: OwnerRef) -> JobRef:
        if not candidate.recoverable:
            raise TaskCenterActionError(
                candidate.reason_code or "checkpoint_not_recoverable",
                candidate.reason_message or "此检查点当前不可恢复。",
            )
        if candidate.owner is None or (
            TASKS_MANAGE_PERMISSION not in actor.permissions and candidate.owner != TaskOwnerScope.from_owner(actor)
        ):
            raise TaskCenterActionError("owner_mismatch", "当前任务所有者不能访问此检查点。")
        with self._lock:
            handler = self._handlers.get(candidate.job_type)
        if handler is None:
            raise TaskCenterActionError("recovery_intent_unregistered", "此任务没有已注册的恢复处理器。")
        ref = handler(candidate, actor)
        if candidate.owner is not None and ref.owner_id != candidate.owner.owner_id:
            raise TaskCenterActionError("recovery_owner_mismatch", "恢复任务必须留在原任务所有者范围内。")
        return ref


class TaskHistoryNavigationRegistry:
    """Resolves opaque artifacts to shell intents without interpreting paths."""

    def __init__(self) -> None:
        self._handlers: dict[str, TaskHistoryNavigator] = {}
        self._lock = threading.RLock()

    def register(self, job_type: str, navigator: TaskHistoryNavigator) -> None:
        if not job_type.strip():
            raise ValueError("navigation job_type must not be empty")
        with self._lock:
            existing = self._handlers.get(job_type)
            if existing is not None and existing is not navigator:
                raise ValueError(f"task navigator for {job_type!r} is already registered")
            self._handlers[job_type] = navigator

    def resolve(
        self,
        record: TaskHistoryRecord,
        actor: OwnerRef,
        action: TaskCenterAction,
    ) -> TaskNavigationIntent | None:
        if action not in {TaskCenterAction.OPEN_RESULT, TaskCenterAction.OPEN_LOG}:
            raise ValueError("navigation registry only accepts open actions")
        if not record.visible_to(actor):
            return None
        with self._lock:
            navigator = self._handlers.get(record.job_type)
        return None if navigator is None else navigator.resolve(record, actor, action)


class TaskCenterActions:
    """Projects and executes only actions proven by registered feature handlers."""

    def __init__(
        self,
        history: TaskHistoryPort,
        recovery: RecoveryCatalog,
        retry_intents: TaskRetryIntentRegistry,
        recovery_intents: TaskRecoveryIntentRegistry,
        navigators: TaskHistoryNavigationRegistry,
    ) -> None:
        self._history = history
        self._recovery = recovery
        self._retry_intents = retry_intents
        self._recovery_intents = recovery_intents
        self._navigators = navigators

    def list_history(
        self,
        actor: OwnerRef,
        *,
        retry_context: TaskRetryContext | None,
        limit: int = 100,
    ) -> tuple[TaskCenterItem, ...]:
        return tuple(
            self._history_item(record, actor, retry_context) for record in self._history.list(actor, limit=limit)
        )

    def list_recovery(self, actor: OwnerRef) -> tuple[TaskCenterItem, ...]:
        return tuple(self._recovery_item(candidate) for candidate in self._recovery.list(actor))

    def execute(
        self,
        item: TaskCenterItem,
        action: TaskCenterAction,
        actor: OwnerRef,
        *,
        retry_context: TaskRetryContext | None,
    ) -> TaskCenterActionResult:
        if action is TaskCenterAction.RECOVER:
            if item.source != "recovery":
                raise TaskCenterActionError("action_source_mismatch", "恢复操作只能用于恢复目录记录。")
            candidate = self._find_recovery(item, actor)
            if not self._recovery_item(candidate).available_actions.recover:
                raise TaskCenterActionError("action_unavailable", "此检查点已不可恢复，请刷新任务目录。")
            return TaskCenterActionResult(job_ref=self._recovery_intents.recover(candidate, actor))

        if item.source != "history":
            raise TaskCenterActionError("action_source_mismatch", "此操作只能用于不可变历史记录。")
        record = self._find_history(item, actor)
        projected = self._history_item(record, actor, retry_context)
        if action is TaskCenterAction.RETRY:
            if not projected.available_actions.retry or retry_context is None:
                raise TaskCenterActionError("action_unavailable", projected.reason_message or "此任务当前不可重试。")
            return TaskCenterActionResult(job_ref=self._retry_intents.retry(record, retry_context))
        if action in {TaskCenterAction.OPEN_RESULT, TaskCenterAction.OPEN_LOG}:
            intent = self._navigators.resolve(record, actor, action)
            if intent is None:
                raise TaskCenterActionError("navigation_unavailable", "结果或日志已不可用，请刷新任务目录。")
            return TaskCenterActionResult(navigation=intent)
        raise TaskCenterActionError("action_unknown", f"未知任务操作：{action}")

    def _history_item(
        self,
        record: TaskHistoryRecord,
        actor: OwnerRef,
        retry_context: TaskRetryContext | None,
    ) -> TaskCenterItem:
        result, result_reason = self._navigation_availability(record, actor, TaskCenterAction.OPEN_RESULT)
        log, log_reason = self._navigation_availability(record, actor, TaskCenterAction.OPEN_LOG)
        retry = retry_context is not None and self._retry_intents.supports(record.job_type)
        actions = TaskActionAvailability(retry=retry, open_result=result is not None, open_log=log is not None)
        reason = ""
        if not any((actions.retry, actions.open_result, actions.open_log)):
            navigation_reason = result_reason or log_reason
            if navigation_reason:
                reason = navigation_reason
            elif self._retry_intents.supports(record.job_type) and retry_context is None:
                reason = "当前上下文缺少安全重试所需的输入标识，且没有可用的结果或日志入口。"
            else:
                reason = "此历史记录没有已注册且当前可用的重试、结果或日志操作。"
        return TaskCenterItem(
            source="history",
            key=record.run_id,
            run_id=record.run_id,
            display_name=record.display_name,
            state_label=record.state.value,
            revision=record.terminal_revision,
            available_actions=actions,
            reason_message=reason,
        )

    def _navigation_availability(
        self,
        record: TaskHistoryRecord,
        actor: OwnerRef,
        action: TaskCenterAction,
    ) -> tuple[TaskNavigationIntent | None, str]:
        try:
            return self._navigators.resolve(record, actor, action), ""
        except Exception as exc:
            label = "结果" if action is TaskCenterAction.OPEN_RESULT else "日志"
            return None, f"{label}入口检查失败：{exc}"

    def _recovery_item(self, candidate: TaskRecoveryAvailability) -> TaskCenterItem:
        available = candidate.recoverable and self._recovery_intents.supports(candidate.job_type)
        reason = candidate.reason_message
        if candidate.recoverable and not available:
            reason = "检查点有效，但对应功能尚未注册恢复处理器。"
        return TaskCenterItem(
            source="recovery",
            key=candidate.storage_key,
            run_id=candidate.run_id,
            display_name=candidate.display_name,
            state_label="可恢复" if available else candidate.reason_code,
            revision=candidate.checkpoint_revision,
            available_actions=TaskActionAvailability(recover=available),
            reason_message=reason or candidate.reason_code,
        )

    def _find_history(self, item: TaskCenterItem, actor: OwnerRef) -> TaskHistoryRecord:
        for record in self._history.list(actor):
            if record.run_id == item.key:
                if item.revision != record.terminal_revision:
                    break
                return record
        raise TaskCenterActionError("history_stale", "历史记录已变化，请刷新任务目录后重试。")

    def _find_recovery(self, item: TaskCenterItem, actor: OwnerRef) -> TaskRecoveryAvailability:
        for candidate in self._recovery.list(actor):
            if candidate.storage_key == item.key:
                if item.revision != candidate.checkpoint_revision:
                    break
                return candidate
        raise TaskCenterActionError("recovery_stale", "恢复记录已变化，请刷新任务目录后重试。")


__all__ = [
    "TaskCenterAction",
    "TaskCenterActionError",
    "TaskCenterActionResult",
    "TaskCenterActions",
    "TaskCenterItem",
    "TaskHistoryNavigationRegistry",
    "TaskHistoryNavigator",
    "TaskRecoveryIntent",
    "TaskRecoveryIntentRegistry",
]
