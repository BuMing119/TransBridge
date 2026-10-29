"""Shared atomic publication of explicitly selected source inputs."""

from dataclasses import dataclass

from transbridge.application.contracts import DomainError, OperationResult
from transbridge.application.io.identity import SourceNamespace
from transbridge.persistence.v2.ids import ProjectId, ProjectRef
from transbridge.persistence.v2.models import ProjectDto

from .provisioning import ProjectSourceRequest
from .source_content import authoritative_baseline_sources, source_content_identity
from .source_inputs import missing_inputs_for
from .source_update_models import SourceUpdateResult


@dataclass(frozen=True)
class PreparedSourcePublication:
    owner: str
    generation: int
    expected: object
    project: ProjectDto
    variants: tuple
    request: ProjectSourceRequest
    source: object
    old_namespace: SourceNamespace | None
    path: str
    preview: object
    missing_inputs: tuple | None = None


def publish_source_input(candidate, token, context, *, lifecycle, store, baselines, preparer):
    """Reuse source-update's existing input, concurrency and transaction guarantees."""
    refreshed = preparer.prepare_source(candidate.request, context, role="primary", common_options=())
    if refreshed != candidate.source:
        raise ValueError("所选源文件在预览后发生变化，请重新预览。")
    ref = ProjectRef(ProjectId(candidate.project.envelope.identity))
    backup = []

    def publish():
        backup.append(
            store.commit(
                candidate.expected,
                candidate.project,
                candidate.variants,
                token,
                backup_metadata={
                    "source": candidate.preview.source_name,
                    "replacement": candidate.preview.replacement_path,
                },
            )
        )

    active = lifecycle.active
    if active is not None and active.project_ref == ref:
        if active.dirty or active.variant is None:
            raise ValueError("预览后工程有未保存修改，请保存并重新预览。")
        variant = next(item for item in candidate.variants if item.ref == active.formal_variant_ref)
        current_baselines = baselines.provide(active.project, active.formal_variant_ref, context)
        retained_namespaces = {
            source_content_identity(source) for source in candidate.project.envelope.data.get("sources", ())
        }
        new_baselines = tuple(
            item
            for item in current_baselines
            if item.fingerprint.namespace.value in retained_namespaces
            and item.fingerprint.namespace != refreshed.baseline.fingerprint.namespace
        )

        def publish_active():
            publish()
            baselines.replace_many(
                ref, tuple(item.ref for item in candidate.variants), (*new_baselines, refreshed.baseline)
            )

        missing = candidate.missing_inputs
        if missing is None:
            missing = _missing_after_publication(candidate.project, variant, preparer)
        result = lifecycle.commit_active_content(
            candidate.project,
            variant,
            context,
            expected_project_revision=candidate.expected.project.envelope.revision,
            expected_variant_revision=variant.revision - 1,
            expected_generation=candidate.generation,
            before_publish=publish_active,
            persisted=True,
            missing_inputs=missing,
        )
        if not result.is_success:
            return result
    else:
        lifecycle.commit_detached_content(ref, candidate.generation, publish)
        for variant in candidate.variants:
            baselines.remove(ref, variant.ref)
    return OperationResult.completed(SourceUpdateResult(candidate.path, backup[0]), run_id=context.run_id)


def _missing_after_publication(project, variant, preparer):
    hydrations, diagnostics = [], {}
    load_saved = getattr(preparer, "load_saved_source", None)
    sources = authoritative_baseline_sources(
        tuple(project.envelope.data.get("sources", ())), tuple(project.envelope.data.get("source_relations", ()))
    )
    for source in sources:
        if not source.get("source_artifact_id") or load_saved is None:
            continue
        try:
            prepared = load_saved(source)
            if prepared.hydration is not None:
                hydrations.append(prepared.hydration)
        except (OSError, DomainError) as exc:
            diagnostics[source["source_id"]] = OperationResult.from_exception(exc).diagnostics[0]
    return missing_inputs_for(project, variant, tuple(hydrations), diagnostics)
