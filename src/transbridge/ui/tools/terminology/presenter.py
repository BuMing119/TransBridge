"""Qt-free orchestration and projection for the terminology workbench."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any, Protocol

from transbridge.application.contracts import JobRef, OperationOutcome, RequestContext
from transbridge.application.tasks import OwnerRef, TaskRuntime
from transbridge.application.terminology import BuildResult, PageRequest, TerminologyQueryPort
from transbridge.application.terminology.browse_queries import TerminologyBrowsePort
from transbridge.application.terminology.conflict_queries import ConflictQuery
from transbridge.application.terminology.conflict_review import TerminologyConflictDetails, TerminologyReviewCounts
from transbridge.application.terminology.conflicts import ConflictResolutionOperation
from transbridge.application.terminology.decisions import DecisionOperation
from transbridge.application.terminology.input_capture import BuildInputSnapshot, TerminologyBuildInputPort
from transbridge.application.terminology.models import DraftRef, TerminologyDraft
from transbridge.application.terminology.publication_review import TerminologyPublishPreview

from .task_adapter import TerminologyTaskAdapter, TerminologyTaskViewState
from .term_editing import TermEditingCommands
from .view_models import (
    SourceScopeViewState,
    TechnicalDetail,
    TerminologyNotice,
    TerminologyPreflightViewState,
    TerminologySummaryViewState,
    business_diagnostic,
    phase_label,
)


class TerminologyCommandPort(Protocol):
    """Narrow UI command boundary; application owns request construction."""

    def start_build(self, snapshot: BuildInputSnapshot, context: RequestContext) -> JobRef: ...

    def publish(self, context: RequestContext) -> JobRef: ...

    def preview_publish(self, context: RequestContext, config=None) -> TerminologyPublishPreview: ...

    def publish_preview(self, preview: TerminologyPublishPreview, context: RequestContext) -> JobRef: ...

    def active_draft(self, context: RequestContext) -> TerminologyDraft | None: ...

    def prepare_draft(self, context: RequestContext, *, config=None) -> TerminologyDraft | None: ...

    def edit_term(self, term_id: str, context: RequestContext, **values) -> TerminologyDraft: ...

    def batch_terms(
        self, term_ids: tuple[str, ...], operation: str, context: RequestContext, **values
    ) -> TerminologyDraft: ...

    def discard_draft(self, expected_ref: DraftRef, context: RequestContext) -> None: ...

    def review_counts(self, context: RequestContext) -> TerminologyReviewCounts: ...

    def conflict_details(self, conflict, context: RequestContext) -> TerminologyConflictDetails: ...

    def resolve_review(self, details: TerminologyConflictDetails, operation, context: RequestContext, **values): ...

    def task_failure(self, run_id: str, context: RequestContext) -> str | None: ...

    def task_result(self, run_id: str, context: RequestContext): ...

    def render_report(self, context: RequestContext, *, config=None) -> JobRef: ...

    def render_changelog(self, context: RequestContext, *, config=None) -> JobRef: ...

    def apply_decision(self, operation: DecisionOperation, context: RequestContext, **values): ...

    def resolve_conflict(self, conflict, operation: ConflictResolutionOperation, context: RequestContext, **values): ...

    def compare(self, version_ref, context: RequestContext, *, config=None) -> JobRef: ...

    def restore(self, version_ref, context: RequestContext) -> JobRef: ...

    def latest_build_result(self, project_id: str, variant_id: str) -> BuildResult | None: ...


class TerminologyPreflightError(RuntimeError):
    """Retain the failed capture projection when a build cannot be submitted."""

    def __init__(self, state: TerminologyPreflightViewState) -> None:
        super().__init__(state.message)
        self.state = state
        self.code = state.diagnostic_code or "TERMINOLOGY_PREFLIGHT_FAILED"


@dataclass(frozen=True, slots=True)
class TerminologyUiServices:
    build_inputs: TerminologyBuildInputPort | None = None
    queries: TerminologyQueryPort | None = None
    commands: TerminologyCommandPort | None = None
    runtime: TaskRuntime | None = None
    sync: object | None = None
    browsing: TerminologyBrowsePort | None = None

    @classmethod
    def from_runtime(cls, runtime: object, context: RequestContext | None = None) -> TerminologyUiServices:
        use_cases = getattr(runtime, "use_cases", None)
        names = set(use_cases.names()) if use_cases is not None else set()

        def optional(*candidates: str) -> object | None:
            for name in candidates:
                if name in names:
                    return use_cases.resolve(name)
            return None

        supplied = optional("terminology_ui_services")
        if isinstance(supplied, cls):
            return supplied
        factory = optional("terminology_ui_services_factory")
        services_for = getattr(factory, "services_for", None)
        if context is not None and callable(services_for):
            supplied = services_for(context)
            if not isinstance(supplied, cls):
                raise TypeError("terminology UI services factory returned an invalid bundle")
            return supplied
        return cls(
            build_inputs=optional("terminology_build_input", "terminology_build_inputs"),  # type: ignore[arg-type]
            queries=optional("terminology_queries", "terminology_query"),  # type: ignore[arg-type]
            commands=optional("terminology_ui_commands", "terminology_commands"),  # type: ignore[arg-type]
            runtime=getattr(runtime, "tasks", None),
            browsing=optional("terminology_browsing"),  # type: ignore[arg-type]
        )


class TerminologyPresenter:
    """Expose application use cases in user task language without business recomputation."""

    def __init__(self, services: TerminologyUiServices, context: RequestContext) -> None:
        self.services = services
        self.context = context
        self._snapshot: BuildInputSnapshot | None = None
        self._build_config: dict[str, Any] = {}
        self._task_adapter: TerminologyTaskAdapter | None = None
        self._closed = False
        self.term_editing = TermEditingCommands(self._short_command, context, lambda: deepcopy(self._build_config))

    @property
    def snapshot(self) -> BuildInputSnapshot | None:
        return self._snapshot

    @property
    def closed(self) -> bool:
        return self._closed

    def preflight(self, *, config: dict[str, Any] | None = None) -> TerminologyPreflightViewState:
        state, _snapshot = self._capture_preflight(config=config)
        return state

    def set_source_scope(self, source_ids) -> None:
        """Keep the explicitly repaired operation scope through build and publication."""
        self._build_config["source_ids"] = list(source_ids)

    def _capture_preflight(
        self, *, config: dict[str, Any] | None = None
    ) -> tuple[TerminologyPreflightViewState, BuildInputSnapshot | None]:
        self._require_open()
        if config is not None:
            self._build_config = deepcopy(config)
        self._snapshot = None
        if self.services.build_inputs is None:
            return TerminologyPreflightViewState.unavailable("术语构建服务尚未接入当前运行环境。"), None
        result = self.services.build_inputs.capture_build_input(self.context, config=deepcopy(self._build_config))
        if result.outcome is not OperationOutcome.COMPLETED or result.value is None:
            diagnostic = result.diagnostics[0] if result.diagnostics else None
            code = "TERMINOLOGY_PREFLIGHT_FAILED" if diagnostic is None else diagnostic.code
            notice = business_diagnostic(code, "" if diagnostic is None else diagnostic.message)
            details = () if diagnostic is None else _diagnostic_details(diagnostic)
            state = TerminologyPreflightViewState(
                False,
                notice.title,
                f"{notice.message} {notice.impact} {notice.recovery}".strip(),
                diagnostic_code=code,
                technical_details=details,
            )
            return state, None
        snapshot = result.value
        self._snapshot = snapshot
        sources = tuple(_source_view(source) for source in snapshot.sources)
        metadata = dict(self.context.metadata)
        project_name = metadata.get("project_name") or snapshot.project_id
        variant_name = metadata.get("variant_name") or snapshot.variant_id
        action_label = "更新术语库" if snapshot.effective_version_id else "创建术语库"
        details = (
            TechnicalDetail("工程修订", str(snapshot.project_revision)),
            TechnicalDetail("翻译版本修订", str(snapshot.variant_revision)),
            TechnicalDetail("当前术语版本", snapshot.effective_version_id or "尚无已发布版本"),
            TechnicalDetail("配置摘要", snapshot.config_digest),
        )
        state = TerminologyPreflightViewState(
            True,
            f"可以{action_label}",
            "将读取当前工程已登记且启用的来源，并包含当前翻译版本尚未写回的调整。",
            project_label=f"当前工程 · {snapshot.project_id}",
            variant_label=f"翻译版本 · {snapshot.variant_id}",
            scope_label=f"来源范围 · {len(sources)} 个已启用来源",
            current_version_label=_current_version_label(snapshot.effective_version_id),
            project_display_name=project_name,
            variant_display_name=variant_name,
            current_version_value=snapshot.effective_version_id or "",
            sources=sources,
            technical_details=details,
            action_label=action_label,
        )
        return state, snapshot

    def start_build(self) -> JobRef:
        state, snapshot = self._capture_preflight()
        if not state.ready:
            raise TerminologyPreflightError(state)
        assert snapshot is not None
        return self._command("start_build", snapshot, self.context)

    def publish(self) -> JobRef:
        if callable(getattr(self._commands(), "preview_publish", None)):
            return self.publish_preview(self.preview_publish())
        return self._command("publish", self.context)

    def preview_publish(self):
        return self._short_command("preview_publish", self.context, config=deepcopy(self._build_config))

    def publish_preview(self, preview) -> JobRef:
        return self._command("publish_preview", preview, self.context)

    def active_draft(self):
        return self._short_command("active_draft", self.context)

    def review_counts(self):
        return self._short_command("review_counts", self.context)

    def discard_draft(self, expected_ref) -> None:
        self._short_command("discard_draft", expected_ref, self.context)

    def conflict_details(self, conflict):
        return self._short_command("conflict_details", conflict, self.context)

    def resolve_review(self, details, operation, *, translation=None, plugin_id=None):
        return self._short_command(
            "resolve_review", details, operation, self.context, translation=translation, plugin_id=plugin_id
        )

    def task_failure(self, run_id: str) -> str | None:
        return self._short_command("task_failure", run_id, self.context)

    def task_result(self, run_id: str):
        return self._short_command("task_result", run_id, self.context)

    def render_report(self) -> JobRef:
        if self._build_config:
            return self._command("render_report", self.context, config=deepcopy(self._build_config))
        return self._command("render_report", self.context)

    def render_changelog(self) -> JobRef:
        if self._build_config:
            return self._command("render_changelog", self.context, config=deepcopy(self._build_config))
        return self._command("render_changelog", self.context)

    def retry_changelog(self) -> JobRef:
        commands = self._commands()
        method = getattr(commands, "retry_changelog", None)
        if callable(method):
            if self._build_config:
                return method(self.context, config=deepcopy(self._build_config))
            return method(self.context)
        return self.render_changelog()

    def add_term(self, original: str, translation: str, *, notes: str = "", scope=None, variants=()):
        return self.term_editing.add_term(original, translation, scope=scope, variants=variants, notes=notes)

    def change_translation(self, term_id: str, translation: str):
        return self.term_editing.change_translation(term_id, translation)

    def set_suppressed(self, term_id: str, *, suppressed: bool):
        return self.term_editing.set_suppressed(term_id, suppressed=suppressed)

    def resolve_conflict(
        self,
        conflict,
        operation: ConflictResolutionOperation,
        *,
        translation: str | None = None,
        plugin_id: str | None = None,
    ):
        return self._short_command(
            "resolve_conflict",
            conflict,
            operation,
            self.context,
            translation=translation,
            plugin_id=plugin_id,
        )

    def compare(self, version_ref) -> JobRef:
        if self._build_config:
            return self._command("compare", version_ref, self.context, config=deepcopy(self._build_config))
        return self._command("compare", version_ref, self.context)

    def restore(self, version_ref) -> JobRef:
        if self._build_config:
            return self._command("restore", version_ref, self.context, config=deepcopy(self._build_config))
        return self._command("restore", version_ref, self.context)

    def bind_tasks(self, on_change) -> TerminologyTaskAdapter | None:
        self._require_open()
        if self.services.runtime is None or self.context.project_id is None or self.context.variant_id is None:
            return None
        if self._task_adapter is not None:
            self._task_adapter.close()
        metadata = dict(self.context.metadata)
        owner = OwnerRef(
            self.context.owner_id,
            metadata.get("entrypoint", "gui"),
            project_id=self.context.project_id,
            variant_id=self.context.variant_id,
            permissions=self.context.permissions,
        )
        self._task_adapter = TerminologyTaskAdapter(self.services.runtime, owner, on_change)
        self._task_adapter.start()
        return self._task_adapter

    def cancel_task(self, ref: JobRef) -> bool:
        return self._task_adapter is not None and self._task_adapter.cancel(ref)

    @staticmethod
    def project_build(result: BuildResult) -> TerminologySummaryViewState:
        summary = result.summary
        partial = result.completeness.value == "partial"
        stale = result.freshness.value == "stale"
        decisions = (
            f"发现 {summary.conflict_count} 组同名异译需要决定。"
            if summary.conflict_count
            else "没有发现需要决定的同名异译。"
        )
        if stale:
            impact = "工程状态已变化，本次结果只能查看，不能发布。"
            next_action = "按当前工程状态重新构建。"
        elif partial:
            impact = "部分来源未完成；已有正式版本和历史保持不变。"
            next_action = "检查未完成来源，或先导出质量报告。"
        else:
            impact = "发布后，后续翻译将优先采用确认后的术语。"
            next_action = "先检查异译，再进行人工调整。"
        return TerminologySummaryViewState(
            "本次构建结果",
            f"从 {summary.source_count} 个来源整理出 {summary.candidate_count} 个术语候选。",
            decisions,
            impact,
            next_action,
            partial,
            stale,
            (
                TechnicalDetail("构建引用", result.ref.build_key),
                TechnicalDetail("内容摘要", result.ref.content_digest),
                TechnicalDetail("诊断代码", "\n".join(result.diagnostics) or "无"),
            ),
            summary.candidate_count,
            summary.conflict_count,
        )

    @staticmethod
    def project_task(state: TerminologyTaskViewState) -> tuple[str, str]:
        total = f"{state.completed}/{state.total}" if state.total else str(state.completed)
        current = f" · {state.current_object}" if state.current_object else ""
        return state.message, f"{phase_label(state.phase)} · {total}{current}"

    @staticmethod
    def notice(code: str, message: str = "") -> TerminologyNotice:
        return business_diagnostic(code, message)

    def page_loader(self, name: str):
        if name in {"list_effective_terms", "list_browsable_draft_terms"}:
            if self.services.browsing is None:
                raise RuntimeError("术语完整列表查询服务尚未接入")
            method = getattr(self.services.browsing, name)
            return lambda query, request=PageRequest(): method(query, request)
        queries = self.services.queries
        if queries is None:
            raise RuntimeError("术语分页查询服务尚未接入")
        method = getattr(queries, name)
        if name == "list_versions":
            return lambda ref, request=PageRequest(): method(ref[0], ref[1], request)
        if name == "list_conflicts":
            method = getattr(queries, "list_review_conflicts", method)
            return lambda ref, request=PageRequest(): (
                method(ref.build_ref, request, filters=ref.filters)
                if isinstance(ref, ConflictQuery)
                else method(ref, request)
            )
        return lambda ref, request=PageRequest(): method(ref, request)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._task_adapter is not None:
            self._task_adapter.close()
            self._task_adapter = None

    def _commands(self) -> TerminologyCommandPort:
        self._require_open()
        if self.services.commands is None:
            raise RuntimeError("术语命令服务尚未接入当前运行环境")
        return self.services.commands

    def _command(self, name: str, *args: object, **kwargs: object) -> JobRef:
        method = getattr(self._commands(), name, None)
        if not callable(method):
            raise RuntimeError(f"术语命令服务不支持：{name}")
        result = method(*args, **kwargs)
        if hasattr(result, "ref"):
            result = result.ref
        if not isinstance(result, JobRef):
            raise TypeError("术语命令必须返回 JobRef 或 Deferred[JobRef]")
        return result

    def _short_command(self, name: str, *args: object, **kwargs: object):
        method = getattr(self._commands(), name, None)
        if not callable(method):
            raise RuntimeError(f"术语命令服务不支持：{name}")
        return method(*args, **kwargs)

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("terminology presenter is closed")


def _source_view(source) -> SourceScopeViewState:
    registration = source.registration
    location = registration.location
    name = registration.display_name or PurePath(location.replace("\\", "/")).name or registration.source_id
    return SourceScopeViewState(
        registration.source_id,
        name,
        registration.format_id.value,
        (
            TechnicalDetail("来源标识", registration.source_id),
            TechnicalDetail("位置", location),
            TechnicalDetail("内容指纹", source.lease.actual_fingerprint),
            TechnicalDetail("适配器", f"{source.adapter_id} {source.adapter_version}"),
        ),
    )


def _current_version_label(version_id: str | None) -> str:
    return "当前版本 · 尚无已发布版本" if version_id is None else f"当前版本 · {version_id}"


def _diagnostic_details(diagnostic) -> tuple[TechnicalDetail, ...]:
    return (
        TechnicalDetail("诊断代码", diagnostic.code),
        TechnicalDetail("诊断信息", diagnostic.message),
        *(TechnicalDetail(str(key), str(value)) for key, value in diagnostic.details),
    )


__all__ = ["TerminologyCommandPort", "TerminologyPreflightError", "TerminologyPresenter", "TerminologyUiServices"]
