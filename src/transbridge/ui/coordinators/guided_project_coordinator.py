"""Qt-free guided Project draft and two-phase provisioning coordinator."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from transbridge.application.projects import (
    ProjectProvisioningRequest,
    ProjectSourceRequest,
)


class GuidedDraftPhase(StrEnum):
    EDITING = "editing"
    PREPARING = "preparing"
    PREPARED = "prepared"
    COMMITTING = "committing"
    FAILED = "failed"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class GuidedProjectDraftState:
    source_path: str | None
    project_name: str
    default_variant_name: str = "默认"
    migration_sources: tuple[str, ...] = ()
    parse_options: tuple[tuple[str, Any], ...] = ()
    preview_token: str | None = None
    request_fingerprint: str | None = None
    preview_entry_count: int | None = None
    preview_source_count: int | None = None
    revision: int = 0
    phase: GuidedDraftPhase = GuidedDraftPhase.EDITING
    in_flight: bool = False
    diagnostic_code: str = ""
    diagnostic_message: str = ""

    def __post_init__(self) -> None:
        if self.revision < 0:
            raise ValueError("draft revision must not be negative")
        if self.preview_token is None and self.request_fingerprint is not None:
            raise ValueError("request fingerprint requires a prepared preview")
        if self.phase in {GuidedDraftPhase.PREPARING, GuidedDraftPhase.COMMITTING} and not self.in_flight:
            raise ValueError("active draft phases must be marked in-flight")
        if self.in_flight and self.phase not in {GuidedDraftPhase.PREPARING, GuidedDraftPhase.COMMITTING}:
            raise ValueError("only prepare/commit phases may be in-flight")

    @property
    def can_submit(self) -> bool:
        return bool(self.project_name.strip() and self.default_variant_name.strip())

    @property
    def summary(self) -> str:
        source_count = self.preview_source_count
        if source_count is None:
            source_count = (1 if self.source_path else 0) + len(self.migration_sources)
        kind = "空工程" if self.source_path is None else f"插件 {Path(self.source_path).name}"
        summary = (
            f"将创建本地工程“{self.project_name or '未命名'}”、翻译版本"
            f"“{self.default_variant_name or '未命名'}”，来源：{kind}。"
        )
        if self.preview_entry_count is not None:
            summary += f" 已验证 {source_count} 个来源、{self.preview_entry_count} 条内容。"
        return summary


class ProjectProvisioningCommands(Protocol):
    def prepare_create(self, request, context): ...

    def commit_create(self, token: str, context, *, request_fingerprint: str | None = None): ...

    def discard_create(self, token: str, context): ...


OperationDispatcher = Callable[
    [Callable[[], object], str, Callable[[object], None], Callable[[str], None]],
    bool,
]


def _direct_dispatch(
    operation: Callable[[], object],
    _message: str,
    on_result: Callable[[object], None],
    on_error: Callable[[str], None],
) -> bool:
    try:
        on_result(operation())
    except Exception as exc:  # direct mode is primarily for headless callers/tests
        on_error(str(exc))
    return True


class GuidedProjectCoordinator:
    """Own draft identity and submit each prepare/commit intent at most once."""

    def __init__(
        self,
        commands: ProjectProvisioningCommands,
        context: object,
        *,
        dispatch: OperationDispatcher | None = None,
        on_state: Callable[[GuidedProjectDraftState], None] | None = None,
        on_created: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._commands = commands
        self._context = context
        self._dispatch = dispatch or _direct_dispatch
        self._on_state = on_state or (lambda _state: None)
        self._on_created = on_created or (lambda _value: None)
        self._state = GuidedProjectDraftState(None, "")
        self._operation_generation = 0

    @property
    def state(self) -> GuidedProjectDraftState:
        return self._state

    def begin(self, source_path: str | None) -> GuidedProjectDraftState:
        self._invalidate_operation()
        self._discard_preview()
        normalized = None if source_path is None else str(Path(source_path))
        suggested = "新工程" if normalized is None else Path(normalized).stem
        self._state = GuidedProjectDraftState(normalized, suggested)
        self._publish()
        return self._state

    def set_project_name(self, value: str) -> bool:
        return self._edit(project_name=value)

    def set_variant_name(self, value: str) -> bool:
        return self._edit(default_variant_name=value)

    def set_migration_sources(self, values: tuple[str, ...]) -> bool:
        normalized = tuple(str(Path(value)) for value in values if str(value).strip())
        return self._edit(migration_sources=normalized)

    def set_parse_option(self, key: str, value: Any) -> bool:
        if not key.strip():
            raise ValueError("parse option key must not be empty")
        options = dict(self._state.parse_options)
        options[key] = value
        return self._edit(parse_options=tuple(sorted(options.items())))

    def prepare(self) -> bool:
        if self._state.in_flight or not self._state.can_submit:
            return False
        try:
            request = self._request()
        except Exception as exc:  # input remains editable and no application command is submitted
            self._fail("PROJECT_DRAFT_INVALID", str(exc))
            return False
        self._operation_generation += 1
        generation = self._operation_generation
        self._state = replace(
            self._state,
            phase=GuidedDraftPhase.PREPARING,
            in_flight=True,
            diagnostic_code="",
            diagnostic_message="",
        )
        self._publish()
        accepted = self._dispatch(
            lambda: self._commands.prepare_create(request, self._context),
            "正在检查插件并准备工程…",
            lambda result: self._finish_prepare(generation, result),
            lambda message: self._finish_transport_error(generation, message),
        )
        if not accepted and generation == self._operation_generation:
            self._fail("UI_OPERATION_BUSY", "另一项后台操作仍在进行，请稍候重试。")
        return accepted

    def commit(self) -> bool:
        state = self._state
        if state.in_flight or not state.preview_token or not state.request_fingerprint:
            return False
        self._operation_generation += 1
        generation = self._operation_generation
        token = state.preview_token
        fingerprint = state.request_fingerprint
        self._state = replace(
            state,
            phase=GuidedDraftPhase.COMMITTING,
            in_flight=True,
            diagnostic_code="",
            diagnostic_message="",
        )
        self._publish()
        accepted = self._dispatch(
            lambda: self._commands.commit_create(
                token,
                self._context,
                request_fingerprint=fingerprint,
            ),
            "正在创建本地翻译工程…",
            lambda result: self._finish_commit(generation, result),
            lambda message: self._finish_transport_error(generation, message),
        )
        if not accepted and generation == self._operation_generation:
            self._fail("UI_OPERATION_BUSY", "另一项后台操作仍在进行，请稍候重试。")
        return accepted

    def discard(self) -> None:
        self._invalidate_operation()
        self._discard_preview()
        self._state = replace(
            self._state,
            preview_token=None,
            request_fingerprint=None,
            preview_entry_count=None,
            preview_source_count=None,
            phase=GuidedDraftPhase.EDITING,
            in_flight=False,
            revision=self._state.revision + 1,
        )
        self._publish()

    def _edit(self, **changes) -> bool:
        if self._state.in_flight:
            return False
        unchanged = all(getattr(self._state, key) == value for key, value in changes.items())
        if unchanged:
            return False
        self._discard_preview()
        self._state = replace(
            self._state,
            **changes,
            preview_token=None,
            request_fingerprint=None,
            preview_entry_count=None,
            preview_source_count=None,
            phase=GuidedDraftPhase.EDITING,
            diagnostic_code="",
            diagnostic_message="",
            revision=self._state.revision + 1,
        )
        self._publish()
        return True

    def _request(self) -> ProjectProvisioningRequest:
        source = None if self._state.source_path is None else ProjectSourceRequest(self._state.source_path)
        migrations = tuple(ProjectSourceRequest(value) for value in self._state.migration_sources)
        return ProjectProvisioningRequest(
            project_name=self._state.project_name,
            default_variant_name=self._state.default_variant_name,
            source=source,
            migration_sources=migrations,
            parse_options=self._state.parse_options,
        )

    def _finish_prepare(self, generation: int, result: object) -> None:
        if generation != self._operation_generation:
            self._discard_late_preview(result)
            return
        if not getattr(result, "is_success", False) or getattr(result, "value", None) is None:
            self._fail_result(result)
            return
        value = dict(result.value)
        self._state = replace(
            self._state,
            preview_token=str(value["token"]),
            request_fingerprint=str(value["request_fingerprint"]),
            preview_entry_count=int(value.get("entry_count", 0)),
            preview_source_count=int(value.get("source_count", 0)),
            phase=GuidedDraftPhase.PREPARED,
            in_flight=False,
            revision=self._state.revision + 1,
        )
        self._publish()

    def _finish_commit(self, generation: int, result: object) -> None:
        if generation != self._operation_generation:
            return
        if not getattr(result, "is_success", False) or getattr(result, "value", None) is None:
            # S02 commit tokens are one-shot even when the atomic publication
            # returns a failure.  Keep editable inputs but require a new preview.
            self._state = replace(
                self._state,
                preview_token=None,
                request_fingerprint=None,
                preview_entry_count=None,
                preview_source_count=None,
            )
            self._fail_result(result)
            return
        value = dict(result.value)
        self._state = replace(
            self._state,
            phase=GuidedDraftPhase.COMPLETED,
            in_flight=False,
            preview_token=None,
            request_fingerprint=None,
            diagnostic_code="",
            diagnostic_message="",
            revision=self._state.revision + 1,
        )
        self._publish()
        self._on_created(value)

    def _finish_transport_error(self, generation: int, message: str) -> None:
        if generation == self._operation_generation:
            if self._state.phase is GuidedDraftPhase.COMMITTING:
                self._state = replace(
                    self._state,
                    preview_token=None,
                    request_fingerprint=None,
                    preview_entry_count=None,
                    preview_source_count=None,
                )
            self._fail("PROJECT_PROVISIONING_FAILED", message or "工程创建失败。")

    def _fail_result(self, result: object) -> None:
        diagnostics = tuple(getattr(result, "diagnostics", ()))
        if diagnostics:
            diagnostic = diagnostics[0]
            self._fail(str(diagnostic.code), str(diagnostic.message))
        else:
            self._fail("PROJECT_PROVISIONING_FAILED", "工程创建失败，请检查输入后重试。")

    def _fail(self, code: str, message: str) -> None:
        self._state = replace(
            self._state,
            phase=GuidedDraftPhase.FAILED,
            in_flight=False,
            diagnostic_code=code,
            diagnostic_message=message,
            revision=self._state.revision + 1,
        )
        self._publish()

    def _discard_preview(self) -> None:
        token = self._state.preview_token
        if token:
            self._commands.discard_create(token, self._context)

    def _discard_late_preview(self, result: object) -> None:
        if getattr(result, "is_success", False) and getattr(result, "value", None):
            token = result.value.get("token")
            if token:
                self._commands.discard_create(str(token), self._context)

    def _invalidate_operation(self) -> None:
        self._operation_generation += 1

    def _publish(self) -> None:
        self._on_state(self._state)


__all__ = [
    "GuidedDraftPhase",
    "GuidedProjectCoordinator",
    "GuidedProjectDraftState",
    "OperationDispatcher",
    "ProjectProvisioningCommands",
]
