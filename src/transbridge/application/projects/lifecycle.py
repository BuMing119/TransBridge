"""Two-phase Project/Variant lifecycle service.

No projection changes until ``commit_transition`` succeeds.  Prepared tokens
and export revision leases are one-shot and owner-bound.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
import logging
import secrets
from threading import RLock
from typing import Any, cast

from transbridge.application.contracts import (
    Diagnostic,
    DiagnosticSeverity,
    DomainError,
    ErrorCategory,
    OperationOutcome,
    OperationResult,
    RequestContext,
)
from transbridge.persistence.v2.variant import VariantChangeSet

from .models import (
    ActiveProject,
    DirtyDecision,
    ExportRevisionLease,
    LifecycleActivation,
    LifecycleEvent,
    LifecycleLease,
    LifecycleProjectUpdate,
    LifecycleSave,
    LifecycleSnapshot,
    PreparedTransition,
    TransitionTarget,
    project_with_active_variant,
)
from .ports import (
    CandidateLoaderPort,
    LifecycleLeasePort,
    LifecycleUnitOfWorkFactoryPort,
    NullLifecycleLeasePort,
)
from .provisioning import ProjectProvisioningCommit


@dataclass(frozen=True, slots=True)
class _PreparedState:
    public: PreparedTransition
    candidate: ActiveProject | None
    leases: tuple[LifecycleLease, ...]
    old_signature: tuple[str, int, str | None, int | None] | None


class ProjectLifecycleService:
    def __init__(
        self,
        loader: CandidateLoaderPort,
        unit_of_work: LifecycleUnitOfWorkFactoryPort,
        *,
        active: ActiveProject | None = None,
        leases: LifecycleLeasePort | None = None,
        token_factory: Callable[[], str] | None = None,
        event_publisher: Callable[[LifecycleEvent], None] | None = None,
    ) -> None:
        self._loader = loader
        self._unit_of_work = unit_of_work
        self._active = active
        self._leases = leases or NullLifecycleLeasePort()
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(24))
        self._event_publisher = event_publisher
        self._generation = 0
        self._prepared: dict[str, _PreparedState] = {}
        self._exports: dict[str, ExportRevisionLease] = {}
        self._issued_tokens: set[str] = set()
        self._lock = RLock()

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    @property
    def active(self) -> ActiveProject | None:
        with self._lock:
            return self._active

    def prepare_transition(
        self,
        target: TransitionTarget,
        context: RequestContext,
        *,
        dirty_decision: DirtyDecision | None = None,
    ) -> OperationResult[dict[str, Any]]:
        with self._lock:
            if dirty_decision is not None and not isinstance(dirty_decision, DirtyDecision):
                try:
                    dirty_decision = DirtyDecision(dirty_decision)
                except ValueError:
                    return _failed(
                        "DIRTY_DECISION_INVALID",
                        "Dirty decision must be save, discard, or cancel.",
                        ErrorCategory.INPUT,
                        context,
                    )
            if (
                context.project_id is not None
                and target.project_ref is not None
                and context.project_id != target.project_ref.identity.value
            ):
                return _failed(
                    "PROJECT_CONTEXT_MISMATCH",
                    "The request context targets a different Project.",
                    ErrorCategory.PERMISSION,
                    context,
                )
            if (
                context.variant_id is not None
                and target.variant_ref is not None
                and context.variant_id != target.variant_ref.identity.value
            ):
                return _failed(
                    "VARIANT_CONTEXT_MISMATCH",
                    "The request context targets a different Variant.",
                    ErrorCategory.PERMISSION,
                    context,
                )
            if self._active is not None and self._active.dirty:
                if dirty_decision is None:
                    return _failed(
                        "DIRTY_DECISION_REQUIRED",
                        "The active Project or Variant has unpersisted changes and requires an explicit decision.",
                        ErrorCategory.PREREQUISITE,
                        context,
                    )
                if dirty_decision is DirtyDecision.CANCEL:
                    return OperationResult.cancelled(
                        Diagnostic(
                            "LIFECYCLE_TRANSITION_CANCELLED",
                            "The lifecycle transition was cancelled before target loading.",
                            DiagnosticSeverity.INFO,
                            ErrorCategory.CANCELLED,
                        ),
                        run_id=context.run_id,
                    )
                if dirty_decision is DirtyDecision.SAVE:
                    saved = self.save_active(context)
                    if saved.outcome is not OperationOutcome.COMPLETED:
                        return cast(OperationResult[dict[str, Any]], saved)

            acquired: tuple[LifecycleLease, ...] = ()
            try:
                acquired = self._leases.acquire(target, context)
                if any(lease.owner_id != context.owner_id for lease in acquired):
                    raise DomainError(
                        ErrorCategory.PERMISSION,
                        "LIFECYCLE_LEASE_OWNER_MISMATCH",
                        "A lifecycle lease was issued to a different owner.",
                    )
                candidate = None if target.project_ref is None else self._loader.prepare_candidate(target, context)
                candidate = self._validate_candidate(target, candidate, acquired)
                token = self._new_token(self._prepared)
                public: PreparedTransition = {
                    "token": token,
                    "owner_id": context.owner_id,
                    "expected_generation": self._generation,
                    "old": None if self._active is None else self._active.summary(),
                    "candidate": None if candidate is None else candidate.summary(),
                    "target": target.to_dict(),
                    "leases": [lease.lease_id for lease in acquired],
                }
                self._prepared[token] = _PreparedState(
                    public,
                    candidate,
                    acquired,
                    _active_signature(self._active),
                )
                return OperationResult.completed(public, run_id=context.run_id)
            except Exception as exc:  # noqa: BLE001 - map adapter failures without leaking details
                self._safe_release(acquired)
                return _from_exception(exc, "LIFECYCLE_PREPARE_FAILED", context)

    def commit_transition(self, token: str, context: RequestContext) -> OperationResult[dict[str, Any]]:
        with self._lock:
            prepared = self._prepared.get(token)
            if prepared is None:
                return _failed(
                    "PREPARED_TRANSITION_INVALID",
                    "The prepared transition is unknown, expired, or already consumed.",
                    ErrorCategory.CONFLICT,
                    context,
                )
            if prepared.public["owner_id"] != context.owner_id:
                return _failed(
                    "PREPARED_TRANSITION_OWNER_MISMATCH",
                    "The prepared transition belongs to another owner.",
                    ErrorCategory.PERMISSION,
                    context,
                )
            self._prepared.pop(token)
            if prepared.public[
                "expected_generation"
            ] != self._generation or prepared.old_signature != _active_signature(self._active):
                self._safe_release(prepared.leases)
                return _failed(
                    "PREPARED_TRANSITION_STALE",
                    "The active lifecycle changed after this transition was prepared.",
                    ErrorCategory.CONFLICT,
                    context,
                )

            old = self._active
            activation = LifecycleActivation.capture(old, prepared.candidate)
            uow = None
            try:
                uow = self._unit_of_work.begin()
                uow.stage_activate(activation)
                uow.commit()
            except Exception as exc:  # noqa: BLE001 - rollback is part of the application contract
                _rollback(uow)
                self._safe_release(prepared.leases)
                return _from_exception(exc, "LIFECYCLE_COMMIT_FAILED", context)

            activated = prepared.candidate
            if activated is not None and activated.variant is not None:
                assert activation.candidate_variant is not None
                activated = replace(
                    activated,
                    persisted_project_revision=activation.candidate_project.envelope.revision,
                    persisted_variant_revision=activation.candidate_variant.revision,
                )
            elif activated is not None:
                assert activation.candidate_project is not None
                activated = replace(
                    activated,
                    persisted_project_revision=activation.candidate_project.envelope.revision,
                )
            self._active = activated
            self._generation += 1
            diagnostics: list[Diagnostic] = []
            release_error = self._safe_release(() if old is None else old.leases)
            if release_error:
                diagnostics.append(
                    Diagnostic(
                        "OLD_LIFECYCLE_LEASE_RELEASE_FAILED",
                        "The new lifecycle is active, but an old lease could not be released.",
                        DiagnosticSeverity.WARNING,
                    )
                )
            if self._active is None and self._safe_release(prepared.leases):
                diagnostics.append(
                    Diagnostic(
                        "CLOSE_LIFECYCLE_LEASE_RELEASE_FAILED",
                        "The lifecycle closed, but its preparation lease could not be released.",
                        DiagnosticSeverity.WARNING,
                    )
                )
            event = LifecycleEvent(
                "active-project-changed",
                self._generation,
                None if old is None else old.summary(),
                None if self._active is None else self._active.summary(),
            )
            if self._event_publisher is not None:
                try:
                    self._event_publisher(event)
                except Exception:  # noqa: BLE001 - projection callbacks cannot roll back domain state
                    diagnostics.append(
                        Diagnostic(
                            "PROJECTION_EVENT_FAILED",
                            "The lifecycle committed, but a projection callback failed.",
                            DiagnosticSeverity.WARNING,
                        )
                    )
            return OperationResult.completed(
                None if self._active is None else self._active.summary(),
                diagnostics=tuple(diagnostics),
                run_id=context.run_id,
            )

    def commit_provisioning(
        self,
        provisioning: ProjectProvisioningCommit,
        candidate: ActiveProject,
        expected_generation: int,
        expected_active_signature: tuple[str, int, str | None, int | None] | None,
        context: RequestContext,
    ) -> OperationResult[dict[str, Any]]:
        """Publish a new Project while holding the lifecycle generation lock."""

        with self._lock:
            if expected_generation != self._generation or expected_active_signature != _active_signature(self._active):
                return _failed(
                    "PROJECT_PROVISIONING_STALE",
                    "The active lifecycle changed after the Project preview was prepared.",
                    ErrorCategory.CONFLICT,
                    context,
                )
            if self._active is not None and self._active.dirty:
                return _failed(
                    "ACTIVE_SAVE_REQUIRED",
                    "Save or discard the active Project changes before creating another Project.",
                    ErrorCategory.PREREQUISITE,
                    context,
                )
            if candidate.project_ref != provisioning.project_ref:
                return _failed(
                    "PROJECT_PROVISIONING_CANDIDATE_MISMATCH",
                    "The prepared Project candidate does not match its persistence mutation.",
                    ErrorCategory.CONFLICT,
                    context,
                )
            if candidate.formal_variant_ref != provisioning.variant_ref or candidate.variant is None:
                return _failed(
                    "PROJECT_PROVISIONING_VARIANT_MISMATCH",
                    "The prepared default Variant does not match its persistence mutation.",
                    ErrorCategory.CONFLICT,
                    context,
                )
            if candidate.variant.snapshot() != provisioning.variant:
                return _failed(
                    "PROJECT_PROVISIONING_SNAPSHOT_MISMATCH",
                    "The prepared Variant changed before Project publication.",
                    ErrorCategory.CONFLICT,
                    context,
                )

            old = self._active
            uow = None
            try:
                uow = self._unit_of_work.begin()
                uow.stage_provisioning(provisioning)
                uow.commit()
            except Exception as exc:  # noqa: BLE001 - rollback is part of the application contract
                _rollback(uow)
                return _from_exception(exc, "PROJECT_PROVISIONING_COMMIT_FAILED", context)

            self._active = replace(
                candidate,
                persisted_project_revision=provisioning.project.envelope.revision,
                persisted_variant_revision=provisioning.variant.revision,
            )
            self._generation += 1
            diagnostics: list[Diagnostic] = []
            if old is not None and self._safe_release(old.leases):
                diagnostics.append(
                    Diagnostic(
                        "OLD_LIFECYCLE_LEASE_RELEASE_FAILED",
                        "The new lifecycle is active, but an old lease could not be released.",
                        DiagnosticSeverity.WARNING,
                    )
                )
            event = LifecycleEvent(
                "active-project-changed",
                self._generation,
                None if old is None else old.summary(),
                self._active.summary(),
            )
            if self._event_publisher is not None:
                try:
                    self._event_publisher(event)
                except Exception:  # noqa: BLE001 - committed data remains authoritative
                    diagnostics.append(
                        Diagnostic(
                            "PROJECTION_EVENT_FAILED",
                            "The lifecycle committed, but a projection callback failed.",
                            DiagnosticSeverity.WARNING,
                        )
                    )
            return OperationResult.completed(
                self._active.summary(),
                diagnostics=tuple(diagnostics),
                run_id=context.run_id,
            )

    def discard_prepared(self, token: str, context: RequestContext) -> OperationResult[None]:
        with self._lock:
            prepared = self._prepared.get(token)
            if prepared is None:
                return _failed(
                    "PREPARED_TRANSITION_INVALID",
                    "The prepared transition is unknown, expired, or already consumed.",
                    ErrorCategory.CONFLICT,
                    context,
                )
            if prepared.public["owner_id"] != context.owner_id:
                return _failed(
                    "PREPARED_TRANSITION_OWNER_MISMATCH",
                    "The prepared transition belongs to another owner.",
                    ErrorCategory.PERMISSION,
                    context,
                )
            self._prepared.pop(token)
            self._safe_release(prepared.leases)
            return OperationResult.completed(run_id=context.run_id)

    def save_active(self, context: RequestContext) -> OperationResult[dict[str, Any] | None]:
        with self._lock:
            active = self._active
            if active is None or not active.dirty:
                return OperationResult.completed(
                    None if active is None else active.summary(),
                    run_id=context.run_id,
                )
            uow = None
            save = LifecycleSave.capture(active)
            try:
                uow = self._unit_of_work.begin()
                uow.stage_save(save)
                uow.commit()
            except Exception as exc:  # noqa: BLE001
                _rollback(uow)
                return _from_exception(exc, "ACTIVE_SAVE_FAILED", context)
            saved_variant_revision = None if save.variant is None else save.variant.revision
            self._active = replace(
                active,
                persisted_project_revision=save.project.envelope.revision,
                persisted_variant_revision=saved_variant_revision,
            )
            revision_changed = active.project.envelope.revision != save.project.envelope.revision
            if active.variant is not None and save.variant is not None:
                revision_changed = revision_changed or active.variant.revision != save.variant.revision
            if revision_changed:
                return _failed(
                    "ACTIVE_SAVE_REVISION_CHANGED",
                    "The active Project or Variant changed while its snapshot was being saved; retry is required.",
                    ErrorCategory.CONFLICT,
                    context,
                )
            return OperationResult.completed(self._active.summary(), run_id=context.run_id)

    def commit_active_variant(
        self,
        change_set: VariantChangeSet,
        context: RequestContext,
        *,
        expected_project_revision: int,
    ) -> OperationResult[dict[str, Any]]:
        """Commit one complete Variant change while holding the lifecycle lock."""

        with self._lock:
            active = self._active
            if active is None or active.variant is None or active.formal_variant_ref is None:
                return _failed(
                    "ACTIVE_VARIANT_REQUIRED",
                    "A V2 Variant must be active before applying a project command.",
                    ErrorCategory.PREREQUISITE,
                    context,
                )
            if context.project_id is not None and context.project_id != active.project_ref.identity.value:
                return _failed(
                    "PROJECT_CONTEXT_MISMATCH",
                    "The request context targets a different Project.",
                    ErrorCategory.PERMISSION,
                    context,
                )
            if context.variant_id is not None and context.variant_id != active.formal_variant_ref.identity.value:
                return _failed(
                    "VARIANT_CONTEXT_MISMATCH",
                    "The request context targets a different Variant.",
                    ErrorCategory.PERMISSION,
                    context,
                )
            if change_set.ref != active.formal_variant_ref:
                return _failed(
                    "ACTIVE_VARIANT_IDENTITY_CHANGED",
                    "The active Variant changed before the command could commit.",
                    ErrorCategory.CONFLICT,
                    context,
                )
            if expected_project_revision != active.project.envelope.revision:
                return _failed(
                    "ACTIVE_PROJECT_REVISION_CHANGED",
                    "The active Project changed before the Variant command could commit.",
                    ErrorCategory.CONFLICT,
                    context,
                )
            if change_set.expected_revision != active.variant.revision:
                return _failed(
                    "ACTIVE_VARIANT_REVISION_CHANGED",
                    "The active Variant changed before the command could commit.",
                    ErrorCategory.CONFLICT,
                    context,
                )

            old_summary = active.summary()
            try:
                revision = active.variant.commit(change_set, context)
            except Exception as exc:  # noqa: BLE001 - preserve the domain cause in OperationResult
                return _from_exception(exc, "ACTIVE_VARIANT_CHANGE_FAILED", context)

            self._generation += 1
            diagnostics: list[Diagnostic] = []
            event = LifecycleEvent(
                "active-variant-updated",
                self._generation,
                old_summary,
                active.summary(),
            )
            if self._event_publisher is not None:
                try:
                    self._event_publisher(event)
                except Exception:  # noqa: BLE001 - committed aggregate remains authoritative
                    diagnostics.append(
                        Diagnostic(
                            "PROJECTION_EVENT_FAILED",
                            "The Variant command committed, but a projection callback failed.",
                            DiagnosticSeverity.WARNING,
                        )
                    )
            return OperationResult.completed(
                {
                    "project_id": active.project_ref.identity.value,
                    "project_revision": active.project.envelope.revision,
                    "variant_id": active.formal_variant_ref.identity.value,
                    "revision": revision,
                },
                diagnostics=tuple(diagnostics),
                run_id=context.run_id,
            )

    def commit_active_content(
        self,
        project,
        variant,
        context: RequestContext,
        *,
        expected_project_revision: int,
        expected_variant_revision: int,
        before_publish: Callable[[], None] | None = None,
    ) -> OperationResult[dict[str, Any]]:
        """Atomically replace active Project and Variant working-copy snapshots."""

        from transbridge.persistence.v2.variant import VariantAggregate

        with self._lock:
            active = self._active
            if active is None or active.variant is None or active.formal_variant_ref is None:
                return _failed(
                    "ACTIVE_VARIANT_REQUIRED",
                    "A V2 Variant must be active before changing Project content.",
                    ErrorCategory.PREREQUISITE,
                    context,
                )
            if context.project_id is not None and context.project_id != active.project_ref.identity.value:
                return _failed(
                    "PROJECT_CONTEXT_MISMATCH",
                    "The request context targets a different Project.",
                    ErrorCategory.PERMISSION,
                    context,
                )
            if context.variant_id is not None and context.variant_id != active.formal_variant_ref.identity.value:
                return _failed(
                    "VARIANT_CONTEXT_MISMATCH",
                    "The request context targets a different Variant.",
                    ErrorCategory.PERMISSION,
                    context,
                )
            if active.project.envelope.revision != expected_project_revision:
                return _failed(
                    "ACTIVE_PROJECT_REVISION_CHANGED",
                    "The active Project changed before the content command could commit.",
                    ErrorCategory.CONFLICT,
                    context,
                )
            if active.variant.revision != expected_variant_revision:
                return _failed(
                    "ACTIVE_VARIANT_REVISION_CHANGED",
                    "The active Variant changed before the content command could commit.",
                    ErrorCategory.CONFLICT,
                    context,
                )
            if project.envelope.identity != active.project.envelope.identity:
                return _failed(
                    "ACTIVE_PROJECT_IDENTITY_CHANGED",
                    "The candidate Project does not match the active Project.",
                    ErrorCategory.CONFLICT,
                    context,
                )
            if project.envelope.revision not in {expected_project_revision, expected_project_revision + 1}:
                return _failed(
                    "ACTIVE_PROJECT_REVISION_INVALID",
                    "The candidate Project revision must stay unchanged or advance once.",
                    ErrorCategory.CONFLICT,
                    context,
                )
            if variant.ref != active.formal_variant_ref:
                return _failed(
                    "ACTIVE_VARIANT_IDENTITY_CHANGED",
                    "The candidate Variant does not match the active Variant.",
                    ErrorCategory.CONFLICT,
                    context,
                )
            if variant.revision not in {expected_variant_revision, expected_variant_revision + 1}:
                return _failed(
                    "ACTIVE_VARIANT_REVISION_INVALID",
                    "The candidate Variant revision must stay unchanged or advance once.",
                    ErrorCategory.CONFLICT,
                    context,
                )
            if project.envelope.revision == expected_project_revision and variant.revision == expected_variant_revision:
                return OperationResult.completed(active.summary(), run_id=context.run_id)

            try:
                candidate_variant = VariantAggregate(variant)
                if before_publish is not None:
                    before_publish()
            except Exception as exc:  # noqa: BLE001
                return _from_exception(exc, "ACTIVE_CONTENT_CHANGE_FAILED", context)

            old = active
            self._active = replace(active, project=project, variant=candidate_variant)
            self._generation += 1
            diagnostics: list[Diagnostic] = []
            event = LifecycleEvent(
                "active-project-content-updated",
                self._generation,
                old.summary(),
                self._active.summary(),
            )
            if self._event_publisher is not None:
                try:
                    self._event_publisher(event)
                except Exception:  # noqa: BLE001 - committed working copy remains authoritative
                    diagnostics.append(
                        Diagnostic(
                            "PROJECTION_EVENT_FAILED",
                            "The Project content command committed, but a projection callback failed.",
                            DiagnosticSeverity.WARNING,
                        )
                    )
            return OperationResult.completed(
                self._active.summary(),
                diagnostics=tuple(diagnostics),
                run_id=context.run_id,
            )

    def commit_project_update(
        self,
        project,
        expected_project_revision: int,
        context: RequestContext,
    ) -> OperationResult[dict[str, Any]]:
        """Atomically replace only the active Project document.

        Variant dirty state is intentionally preserved; changing remote metadata
        must not implicitly save or discard translation edits.
        """

        with self._lock:
            active = self._active
            if active is None:
                return _failed(
                    "ACTIVE_PROJECT_REQUIRED",
                    "An active Project is required.",
                    ErrorCategory.PREREQUISITE,
                    context,
                )
            if context.project_id is not None and context.project_id != active.project_ref.identity.value:
                return _failed(
                    "PROJECT_CONTEXT_MISMATCH",
                    "The request context targets a different Project.",
                    ErrorCategory.PERMISSION,
                    context,
                )
            if expected_project_revision != active.project.envelope.revision:
                return _failed(
                    "PROJECT_UPDATE_STALE",
                    "The active Project changed before the update was committed.",
                    ErrorCategory.CONFLICT,
                    context,
                )
            if project.envelope.identity != active.project.envelope.identity:
                return _failed(
                    "PROJECT_UPDATE_IDENTITY_MISMATCH",
                    "The updated Project does not match the active Project.",
                    ErrorCategory.CONFLICT,
                    context,
                )
            if project.envelope.revision != expected_project_revision + 1:
                return _failed(
                    "PROJECT_UPDATE_REVISION_INVALID",
                    "The updated Project must advance its revision exactly once.",
                    ErrorCategory.CONFLICT,
                    context,
                )

            update = LifecycleProjectUpdate(project, active.persisted_project_revision)
            uow = None
            try:
                uow = self._unit_of_work.begin()
                uow.stage_project_update(update)
                uow.commit()
            except Exception as exc:  # noqa: BLE001 - rollback is part of the application contract
                _rollback(uow)
                return _from_exception(exc, "PROJECT_UPDATE_COMMIT_FAILED", context)

            old = active
            self._active = replace(
                active,
                project=project,
                persisted_project_revision=project.envelope.revision,
            )
            self._generation += 1
            diagnostics: list[Diagnostic] = []
            event = LifecycleEvent(
                "active-project-updated",
                self._generation,
                old.summary(),
                self._active.summary(),
            )
            if self._event_publisher is not None:
                try:
                    self._event_publisher(event)
                except Exception:  # noqa: BLE001 - committed data remains authoritative
                    diagnostics.append(
                        Diagnostic(
                            "PROJECTION_EVENT_FAILED",
                            "The Project update committed, but a projection callback failed.",
                            DiagnosticSeverity.WARNING,
                        )
                    )
            from .remote_binding import project_paratranz_binding

            binding = project_paratranz_binding(project)
            return OperationResult.completed(
                {
                    "project_id": self._active.project_ref.identity.value,
                    "project_revision": project.envelope.revision,
                    "paratranz_binding": None if binding is None else binding.to_dict(),
                },
                diagnostics=tuple(diagnostics),
                run_id=context.run_id,
            )

    def save_snapshot(self, name: str, context: RequestContext) -> OperationResult[dict[str, Any]]:
        with self._lock:
            if not name or not name.strip():
                return _failed(
                    "SNAPSHOT_NAME_REQUIRED",
                    "Snapshot display name must not be empty.",
                    ErrorCategory.INPUT,
                    context,
                )
            active = self._active
            if active is None or active.variant is None or active.formal_variant_ref is None:
                return _failed(
                    "ACTIVE_VARIANT_REQUIRED",
                    "A snapshot requires an active formal Variant.",
                    ErrorCategory.PREREQUISITE,
                    context,
                )
            capture = LifecycleSnapshot(
                active.project_ref,
                active.formal_variant_ref,
                active.variant.snapshot(),
                name.strip(),
            )
            generation = self._generation
            uow = None
            try:
                uow = self._unit_of_work.begin()
                uow.stage_snapshot(capture)
                uow.commit()
            except Exception as exc:  # noqa: BLE001
                _rollback(uow)
                return _from_exception(exc, "SNAPSHOT_SAVE_FAILED", context)
            if self._generation != generation or self._active is not active:
                return _failed(
                    "SNAPSHOT_LIFECYCLE_CONFLICT",
                    "The active lifecycle changed while the snapshot was being saved.",
                    ErrorCategory.CONFLICT,
                    context,
                )
            return OperationResult.completed(
                {
                    "name": capture.name,
                    "project_id": capture.project_ref.identity.value,
                    "variant_id": capture.formal_variant_ref.identity.value,
                    "variant_revision": capture.variant.revision,
                    "current_pointer_changed": False,
                },
                run_id=context.run_id,
            )

    def acquire_export_lease(self, context: RequestContext) -> OperationResult[dict[str, Any]]:
        with self._lock:
            active = self._active
            if active is None or active.variant is None or active.formal_variant_ref is None:
                return _failed(
                    "ACTIVE_VARIANT_REQUIRED",
                    "Export requires an active Variant.",
                    ErrorCategory.PREREQUISITE,
                    context,
                )
            token = self._new_token(self._exports)
            lease: ExportRevisionLease = {
                "token": token,
                "owner_id": context.owner_id,
                "generation": self._generation,
                "project_id": active.project_ref.identity.value,
                "variant_id": active.formal_variant_ref.identity.value,
                "variant_revision": active.variant.revision,
            }
            self._exports[token] = lease
            return OperationResult.completed(lease, run_id=context.run_id)

    def validate_export_lease(self, token: str, context: RequestContext) -> OperationResult[dict[str, Any]]:
        with self._lock:
            lease = self._exports.get(token)
            if lease is None:
                return _failed(
                    "EXPORT_REVISION_LEASE_INVALID",
                    "The export revision lease is unknown or already consumed.",
                    ErrorCategory.CONFLICT,
                    context,
                )
            if lease["owner_id"] != context.owner_id:
                return _failed(
                    "EXPORT_REVISION_LEASE_OWNER_MISMATCH",
                    "The export revision lease belongs to another owner.",
                    ErrorCategory.PERMISSION,
                    context,
                )
            self._exports.pop(token)
            active = self._active
            current = None
            if active is not None and active.variant is not None and active.formal_variant_ref is not None:
                current = (
                    self._generation,
                    active.project_ref.identity.value,
                    active.formal_variant_ref.identity.value,
                    active.variant.revision,
                )
            expected = (
                lease["generation"],
                lease["project_id"],
                lease["variant_id"],
                lease["variant_revision"],
            )
            if current != expected:
                return _failed(
                    "EXPORT_VARIANT_REVISION_CHANGED",
                    "The active Variant changed during export; publication must fail or retry.",
                    ErrorCategory.CONFLICT,
                    context,
                )
            return OperationResult.completed(lease, run_id=context.run_id)

    def _validate_candidate(
        self,
        target: TransitionTarget,
        candidate: ActiveProject | None,
        leases: tuple[LifecycleLease, ...],
    ) -> ActiveProject | None:
        if target.project_ref is None:
            if candidate is not None:
                raise ValueError("close transition must not materialize a candidate")
            return None
        if candidate is None or candidate.project_ref != target.project_ref:
            raise ValueError("candidate Project does not match the transition target")
        if candidate.formal_variant_ref != target.variant_ref:
            raise ValueError("candidate Variant does not match the transition target")
        if target.snapshot_ref is not None and candidate.source_ref != target.snapshot_ref:
            raise ValueError("snapshot candidate does not retain its read-only source reference")
        if candidate.leases:
            raise ValueError("candidate loader must not retain hidden lifecycle leases")
        project = project_with_active_variant(candidate.project, target.variant_ref)
        return replace(candidate, project=project, source_ref=target.snapshot_ref, leases=leases)

    def _new_token(self, existing: dict[str, Any]) -> str:
        token = self._token_factory()
        if not token or token in existing or token in self._issued_tokens:
            raise RuntimeError("token factory returned an empty or duplicate token")
        self._issued_tokens.add(token)
        return token

    def _safe_release(self, leases: tuple[LifecycleLease, ...]) -> bool:
        if not leases:
            return False
        try:
            self._leases.release(leases)
        except Exception:  # noqa: BLE001 - release failure is surfaced as a warning where possible
            return True
        return False


def _failed[T](
    code: str,
    message: str,
    category: ErrorCategory,
    context: RequestContext,
) -> OperationResult[T]:
    return OperationResult.failed(DomainError(category, code, message), run_id=context.run_id)


def _from_exception[T](exc: Exception, fallback_code: str, context: RequestContext) -> OperationResult[T]:
    if isinstance(exc, DomainError):
        error = exc
    else:
        logging.getLogger(__name__).error("Lifecycle operation failed (%s)", fallback_code, exc_info=exc)
        error = DomainError(
            ErrorCategory.INTERNAL,
            fallback_code,
            "The lifecycle operation failed before its state could commit.",
            cause=exc,
        )
    return OperationResult.failed(error, run_id=context.run_id)


def _rollback(unit_of_work: Any | None) -> None:
    if unit_of_work is None:
        return
    try:
        unit_of_work.rollback()
    except Exception:
        # The active state has not been exchanged; adapter cleanup failure must
        # not replace the original lifecycle diagnostic.
        return


def _active_signature(active: ActiveProject | None) -> tuple[str, int, str | None, int | None] | None:
    if active is None:
        return None
    return (
        active.project_ref.identity.value,
        active.project.envelope.revision,
        None if active.formal_variant_ref is None else active.formal_variant_ref.identity.value,
        None if active.variant is None else active.variant.revision,
    )


__all__ = ["ProjectLifecycleService"]
