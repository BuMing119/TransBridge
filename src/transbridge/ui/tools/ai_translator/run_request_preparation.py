"""Prepare expensive run identity from detached inputs before GUI admission."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from transbridge.ai_translator.project_terminology_runtime import ProjectTerminologyBinding, freeze_project_terminology
from transbridge.application.tasks import OwnerRef
from transbridge.application.translation.ai_request_budget import AiRequestBudget

from .run_spec import AiRunSpec, FrozenExecutionConfig, build_run_spec


@dataclass(frozen=True, slots=True)
class TerminologyPreparationOwner:
    """Factories are injected services; this object never retains the live UI context."""

    active_version_identity: tuple[str, str] | None
    effective_terminology_factory: object | None
    terminology_echo_links_factory: object | None


def capture_terminology_owner(owner) -> TerminologyPreparationOwner | None:
    """Call on the GUI thread; capture dependencies without invoking storage."""
    if owner is None:
        return None
    identity = getattr(owner, "active_version_identity", None)
    return TerminologyPreparationOwner(
        tuple(identity) if isinstance(identity, tuple) else None,
        getattr(owner, "effective_terminology_factory", None),
        getattr(owner, "terminology_echo_links_factory", None),
    )


@dataclass(frozen=True, slots=True)
class PreparedRun:
    config_snapshot: FrozenExecutionConfig
    terminology_binding: ProjectTerminologyBinding
    spec: AiRunSpec
    entries: tuple
    esp_path: str | None
    project_revision: int | None
    max_concurrent: int

    def validate_admission(
        self, *, owner, mode, entries, overwrite, esp_path, project_revision, run_id, terminology_snapshot_ref
    ):
        if (
            self.spec.owner != owner
            or self.spec.mode != mode
            or self.spec.overwrite != overwrite
            or self.project_revision != project_revision
            or (run_id is not None and self.spec.run_id != run_id)
            or (terminology_snapshot_ref is not None and self.spec.terminology_snapshot != terminology_snapshot_ref)
        ):
            raise ValueError("准备好的 AI 请求与当前运行身份或选项不匹配")
        if entries is not self.entries:
            raise ValueError("AI 请求必须使用准备阶段的同一份独立条目快照")


def prepare_run_request(
    owner_id,
    mode,
    config,
    entries,
    *,
    overwrite=False,
    esp_path=None,
    project_id=None,
    variant_id=None,
    session_id=None,
    project_revision=None,
    run_id=None,
    terminology_owner: TerminologyPreparationOwner | None = None,
    terminology_snapshot_ref=None,
    restored_terminology_binding=None,
) -> PreparedRun:
    """Run off-thread with private config and entries from a detached TaskSession.

    The caller owns their immutability until admission. Window edits are guarded
    separately by the startup coordinator, before any runtime task is created.
    """
    if terminology_owner is not None and not isinstance(terminology_owner, TerminologyPreparationOwner):
        raise TypeError("run preparation requires a captured terminology owner, not a live UI context")
    config_snapshot = FrozenExecutionConfig(config)
    execution_config = config_snapshot.copy()
    max_concurrent = getattr(execution_config, "max_concurrent", None)
    try:
        AiRequestBudget(max_concurrent)
    except ValueError as exc:
        raise ValueError("config.max_concurrent must be a positive integer") from exc
    if (
        terminology_owner is not None
        and terminology_owner.active_version_identity is not None
        and project_id is not None
        and variant_id is not None
        and terminology_owner.active_version_identity != (project_id, variant_id)
    ):
        raise ValueError("准备阶段的术语身份不属于本次 Project/Variant")
    binding = restored_terminology_binding
    if binding is None:
        binding = (
            ProjectTerminologyBinding()
            if terminology_owner is None
            else freeze_project_terminology(terminology_owner, terminology_snapshot_ref)
        )
    elif binding.context is not None and (
        binding.context.local_project_id,
        binding.context.local_variant_id,
    ) != (project_id, variant_id):
        raise ValueError("恢复的任务参考不属于本次 Project/Variant")
    values = tuple(entries)
    owner = OwnerRef(
        owner_id=owner_id,
        entrypoint="ui.ai-translator",
        project_id=project_id,
        variant_id=variant_id,
        session_id=session_id,
    )
    spec = build_run_spec(
        run_id=run_id or f"ai-{uuid4().hex}",
        generation=1,
        mode=mode,
        owner=owner,
        config=execution_config,
        entries=values,
        esp_path=esp_path,
        overwrite=overwrite,
        project_revision=project_revision,
        terminology_snapshot=binding.snapshot_ref,
    )
    return PreparedRun(config_snapshot, binding, spec, values, esp_path, project_revision, max_concurrent)
