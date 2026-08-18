"""GUI command facade over the authoritative Project lifecycle service."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

from transbridge.application.contracts import OperationOutcome, OperationResult, RequestContext
from transbridge.application.io.stage_policy import Stage
from transbridge.persistence.v2.baselines import LegacyIdentityRegistry
from transbridge.persistence.v2.ids import ProjectRef, VariantRef
from transbridge.persistence.v2.variant import VariantChangeSet

from .lifecycle import ProjectLifecycleService
from .models import DirtyDecision, TransitionTarget


class GuiProjectCommandFacade:
    def __init__(
        self,
        lifecycle: ProjectLifecycleService,
        legacy_identities: LegacyIdentityRegistry,
        projection_rebuild: Callable[[], None] | None = None,
    ) -> None:
        self._lifecycle = lifecycle
        self._legacy_identities = legacy_identities
        self._projection_rebuild = projection_rebuild

    def switch_v2(
        self,
        project_ref: ProjectRef,
        variant_ref: VariantRef | None,
        context: RequestContext,
        *,
        dirty_decision: DirtyDecision | None = None,
    ) -> OperationResult[dict | None]:
        prepared = self._lifecycle.prepare_transition(
            TransitionTarget(project_ref, variant_ref),
            context,
            dirty_decision=dirty_decision,
        )
        if prepared.outcome is not OperationOutcome.COMPLETED or prepared.value is None:
            return prepared
        return self._lifecycle.commit_transition(prepared.value["token"], context)

    def switch_legacy(
        self,
        legacy_project_key: str,
        legacy_variant_name: str,
        context: RequestContext,
        *,
        dirty_decision: DirtyDecision | None = None,
    ) -> OperationResult[dict | None]:
        try:
            project_ref, variant_ref = self._legacy_identities.resolve(
                legacy_project_key,
                legacy_variant_name,
            )
        except Exception as exc:  # DomainError is preserved by OperationResult
            return OperationResult.from_exception(exc, run_id=context.run_id)
        return self.switch_v2(
            project_ref,
            variant_ref,
            context,
            dirty_decision=dirty_decision,
        )

    def save(self, context: RequestContext) -> OperationResult[dict | None]:
        result = self._lifecycle.save_active(context)
        if result.is_success and self._projection_rebuild is not None:
            self._projection_rebuild()
        return result

    def update_entry(
        self,
        local_key: str,
        context: RequestContext,
        *,
        translation: str | None = None,
        stage: int | None = None,
    ) -> OperationResult[dict[str, Any]]:
        def update(entries):
            found = False
            projected = []
            for entry in entries:
                if entry.entry_key.local_key != local_key:
                    projected.append(entry)
                    continue
                found = True
                projected.append(
                    replace(
                        entry,
                        translation=entry.translation if translation is None else translation,
                        stage=entry.stage if stage is None else Stage(stage),
                    )
                )
            if not found:
                raise ValueError("EntryKey is not present in the active Variant")
            return tuple(projected)

        return self._commit_variant(context, update_entries=update)

    def replace_labels(
        self,
        entry_labels: Mapping[str, set[str]],
        label_library: Mapping[str, Mapping[str, Any]],
        context: RequestContext,
    ) -> OperationResult[dict[str, Any]]:
        def update(entries):
            return tuple(
                replace(entry, labels=tuple(sorted(entry_labels.get(entry.entry_key.local_key, ()))))
                for entry in entries
            )

        return self._commit_variant(
            context,
            update_entries=update,
            label_library=tuple((str(key), dict(value)) for key, value in label_library.items()),
        )

    def _commit_variant(
        self,
        context: RequestContext,
        *,
        update_entries,
        label_library=None,
    ) -> OperationResult[dict[str, Any]]:
        active = self._lifecycle.active
        if active is None or active.variant is None or active.formal_variant_ref is None:
            from transbridge.application.contracts import DomainError, ErrorCategory

            return OperationResult.failed(
                DomainError(
                    ErrorCategory.PREREQUISITE,
                    "ACTIVE_VARIANT_REQUIRED",
                    "A V2 Variant must be active before applying a GUI command.",
                ),
                run_id=context.run_id,
            )
        snapshot = active.variant.snapshot()
        try:
            revision = active.variant.commit(
                VariantChangeSet(
                    snapshot.ref,
                    snapshot.revision,
                    snapshot.source_fingerprints,
                    update_entries(snapshot.entries),
                    snapshot.label_library if label_library is None else label_library,
                    context.run_id or "",
                ),
                context,
            )
            if self._projection_rebuild is not None:
                self._projection_rebuild()
            return OperationResult.completed(
                {"variant_id": snapshot.ref.identity.value, "revision": revision},
                run_id=context.run_id,
            )
        except Exception as exc:  # noqa: BLE001
            return OperationResult.from_exception(exc, run_id=context.run_id)


__all__ = ["GuiProjectCommandFacade"]
