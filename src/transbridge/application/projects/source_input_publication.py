# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Shared atomic publication of explicitly selected source inputs."""

from dataclasses import dataclass
import logging

from transbridge.application.contracts import (
    Diagnostic,
    DiagnosticSeverity,
    DomainError,
    ErrorCategory,
    OperationResult,
)
from transbridge.application.contracts.errors import map_exception
from transbridge.application.io.identity import SourceNamespace
from transbridge.persistence.v2.ids import ProjectId, ProjectRef
from transbridge.persistence.v2.models import ProjectDto

from .provisioning import ProjectSourceRequest
from .provisioning_models import PreparedProjectSource
from .source_content import source_content_identity
from .source_update_models import SourceUpdateResult
from .storage_contracts import ProjectReadToken

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PreparedSourceInput:
    request: ProjectSourceRequest
    source: PreparedProjectSource
    old_namespace: SourceNamespace | None
    expected_reference_path: str | None = None


@dataclass(frozen=True)
class PreparedSourcePublication:
    owner: str
    generation: int
    expected: object
    project: ProjectDto
    variants: tuple
    inputs: tuple[PreparedSourceInput, ...]
    path: str
    backup_name: str


def publish_source_input(
    candidate, token, context, *, lifecycle, store, baselines, preparer, cancelled=None, recover_replacement=False
):
    """Revalidate every selected input before publishing the prepared changes."""
    refreshed = []
    for item in candidate.inputs:
        check_source_update_cancelled(cancelled)
        current = parse_source_update_input(preparer, item.request, context)
        if current != item.source:
            raise ValueError(f"所选源文件在预览后发生变化，请重新预览。\n{item.request.location}")
        refreshed.append(current)

    def persist():
        check_source_update_cancelled(cancelled)
        try:
            return store.commit_batch(
                candidate.expected,
                candidate.project,
                candidate.variants,
                token,
                sources=tuple(refreshed),
                replaces_namespaces=tuple(item.old_namespace for item in candidate.inputs),
                expected_reference_paths=tuple(item.expected_reference_path for item in candidate.inputs),
                backup_name=candidate.backup_name,
            )
        except Exception:
            if not recover_replacement:
                raise
            backup = store.committed_backup(candidate.project, candidate.variants, token)
            if backup is None:
                raise
            _logger.warning("Recovered a committed source replacement from its backup", exc_info=True)
            return backup

    return _publish_source_revision(candidate, refreshed, context, lifecycle, baselines, persist, recover_replacement)


def parse_source_update_input(preparer, request, context):
    try:
        return preparer.prepare_source(request, context, role="primary", common_options=())
    except (DomainError, OSError, ValueError) as exc:
        error = map_exception(exc)
        raise DomainError(
            error.category,
            error.code,
            f"{error.message}\n文件：{request.location}",
            retryable=error.retryable,
            details=error.details,
            cause=exc,
        ) from exc


def _publish_source_revision(candidate, sources, context, lifecycle, baselines, persist, recover_replacement):
    ref = ProjectRef(ProjectId(candidate.project.envelope.identity))
    backup = []
    diagnostics = []
    active_token = None

    def publish():
        backup.append(persist())

    active = lifecycle.active
    if active is not None and active.project_ref == ref:
        if active.dirty or active.variant is None:
            raise ValueError("预览后工程有未保存修改，请保存并重新预览。")
        variant = next(item for item in candidate.variants if item.ref == active.formal_variant_ref)
        expected_variant = next(item for item in candidate.expected.variants if item.ref == variant.ref)
        current_baselines = baselines.provide(active.project, active.formal_variant_ref, context)
        new_baselines = tuple(source.baseline for source in sources)
        new_namespaces = {item.fingerprint.namespace for item in new_baselines}
        retained_namespaces = {
            source_content_identity(source) for source in candidate.project.envelope.data.get("sources", ())
        }
        retained_baselines = tuple(
            item
            for item in current_baselines
            if item.fingerprint.namespace.value in retained_namespaces
            and item.fingerprint.namespace not in new_namespaces
        )

        def publish_active():
            publish()
            variant_refs = tuple(item.ref for item in candidate.variants)
            if recover_replacement:
                diagnostics.extend(
                    publish_source_baselines(baselines, ref, variant_refs, (*retained_baselines, *new_baselines))
                )
            else:
                baselines.replace_many(ref, variant_refs, (*retained_baselines, *new_baselines))

        result = lifecycle.commit_active_content(
            candidate.project,
            variant,
            context,
            expected_project_revision=candidate.expected.project.envelope.revision,
            expected_variant_revision=expected_variant.revision,
            expected_generation=candidate.generation,
            before_publish=publish_active,
            persisted=True,
            missing_inputs=(),
        )
        if not result.is_success:
            return result
        diagnostics.extend(result.diagnostics)
        if recover_replacement:
            active_token = ProjectReadToken(
                ref.identity, variant.ref.identity, candidate.project.envelope.revision, variant.revision
            )
    else:
        lifecycle.commit_detached_content(ref, candidate.generation, publish)
        for variant in candidate.variants:
            baselines.remove(ref, variant.ref)
    return OperationResult.completed(
        SourceUpdateResult(candidate.path, backup[0], active_token),
        diagnostics=tuple(diagnostics),
        run_id=context.run_id,
    )


def publish_source_baselines(registry, project_ref, variant_refs, baselines, *, allow_empty=False):
    """Refresh a cache after the SQLite source revision is durable."""
    try:
        registry.replace_many(project_ref, variant_refs, baselines, allow_empty=allow_empty)
    except Exception:
        _logger.exception("Committed source baselines could not be cached")
        for variant_ref in variant_refs:
            registry.remove(project_ref, variant_ref)
        return (
            Diagnostic(
                "SOURCE_UPDATE_BASELINES_RELOAD", "来源基准将在下次读取时从工程重建。", DiagnosticSeverity.WARNING
            ),
        )
    return ()


def check_source_update_cancelled(cancelled):
    if cancelled is not None and cancelled():
        raise DomainError(ErrorCategory.CANCELLED, "SOURCE_UPDATE_CANCELLED", "已取消更新来源。")
