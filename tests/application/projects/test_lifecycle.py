from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io.identity import SourceNamespace
from transbridge.application.projects import (
    ActiveProject,
    DirtyDecision,
    LegacyProjectLifecycleAdapter,
    LifecycleLease,
    ProjectLifecycleService,
    TransitionTarget,
)
from transbridge.application.projects.models import (
    LifecycleActivation,
    LifecycleSave,
    LifecycleSnapshot,
)
from transbridge.persistence.project_lifecycle_uow import RepositoryLifecycleUnitOfWorkFactory
from transbridge.persistence.v2 import (
    ProjectDto,
    ProjectId,
    ProjectRef,
    SchemaEnvelope,
    SourceFingerprint,
    VariantAggregate,
    VariantChangeSet,
    VariantId,
    VariantRef,
    VariantSnapshot,
)


def _refs(project: str, variant: str) -> tuple[ProjectRef, VariantRef]:
    project_ref = ProjectRef(ProjectId(project))
    return project_ref, VariantRef(VariantId(variant), project_ref.identity)


def _project(project_ref: ProjectRef, variants: tuple[VariantRef, ...], *, revision: int = 0) -> ProjectDto:
    return ProjectDto(
        SchemaEnvelope(
            2,
            project_ref.kind,
            project_ref.identity.value,
            revision,
            {
                "name": project_ref.identity.value,
                "sources": [],
                "variant_ids": [item.identity.value for item in variants],
                "active_variant_id": None,
            },
        )
    )


def _active(
    project_ref: ProjectRef,
    variant_ref: VariantRef | None,
    *,
    revision: int = 0,
    persisted_revision: int | None = None,
    source_ref: str | None = None,
    leases: tuple[LifecycleLease, ...] = (),
) -> ActiveProject:
    variants = () if variant_ref is None else (variant_ref,)
    variant = None
    if variant_ref is not None:
        variant = VariantAggregate(VariantSnapshot(variant_ref, (), (), revision))
    return ActiveProject(
        project=_project(project_ref, variants),
        variant=variant,
        formal_variant_ref=variant_ref,
        persisted_project_revision=0,
        persisted_variant_revision=(
            (revision if persisted_revision is None else persisted_revision) if variant_ref is not None else None
        ),
        source_ref=source_ref,
        leases=leases,
    )


def _context(owner: str = "owner", run_id: str = "run") -> RequestContext:
    return RequestContext(owner_id=owner, run_id=run_id)


class _Loader:
    def __init__(self, candidates: dict[str, ActiveProject]) -> None:
        self.candidates = candidates
        self.calls: list[TransitionTarget] = []
        self.error: Exception | None = None

    def prepare_candidate(self, target: TransitionTarget, context: RequestContext) -> ActiveProject:
        self.calls.append(target)
        if self.error is not None:
            raise self.error
        assert target.project_ref is not None
        return self.candidates[target.project_ref.identity.value]


class _Leases:
    def __init__(self) -> None:
        self.acquired: list[tuple[TransitionTarget, str]] = []
        self.released: list[tuple[str, ...]] = []
        self.error: Exception | None = None

    def acquire(self, target: TransitionTarget, context: RequestContext) -> tuple[LifecycleLease, ...]:
        self.acquired.append((target, context.owner_id))
        if self.error is not None:
            raise self.error
        return (LifecycleLease(f"lease-{len(self.acquired)}", context.owner_id),)

    def release(self, leases: tuple[LifecycleLease, ...]) -> None:
        self.released.append(tuple(item.lease_id for item in leases))


class _TransactionStore:
    def __init__(self) -> None:
        self.staged: dict[str, tuple[str, object]] = {}
        self.calls: list[tuple[str, str]] = []
        self.active_pointer: tuple[str, str | None] | None = None
        self.saved_revisions: list[int] = []
        self.snapshots: list[LifecycleSnapshot] = []
        self.fail_stage: str | None = None
        self.fail_begin = False
        self.fail_commit = False
        self.fail_rollback = False
        self.before_commit: Callable[[], None] | None = None

    def begin(self, transaction_id: str) -> None:
        self.calls.append(("begin", transaction_id))
        if self.fail_begin:
            raise OSError("injected begin failure")

    def stage_save(self, transaction_id: str, save: LifecycleSave) -> None:
        self._stage(transaction_id, "save", save)

    def stage_activate(self, transaction_id: str, activation: LifecycleActivation) -> None:
        self._stage(transaction_id, "activate", activation)

    def stage_snapshot(self, transaction_id: str, snapshot: LifecycleSnapshot) -> None:
        self._stage(transaction_id, "snapshot", snapshot)

    def commit(self, transaction_id: str) -> None:
        self.calls.append(("commit", transaction_id))
        if self.before_commit is not None:
            self.before_commit()
        if self.fail_commit:
            raise OSError("injected commit failure")
        kind, value = self.staged.pop(transaction_id)
        if kind == "save":
            save = value
            assert isinstance(save, LifecycleSave)
            self.saved_revisions.append(-1 if save.variant is None else save.variant.revision)
        elif kind == "activate":
            activation = value
            assert isinstance(activation, LifecycleActivation)
            if activation.candidate_project is None:
                self.active_pointer = None
            else:
                project_id = activation.candidate_project.envelope.identity
                self.active_pointer = (
                    project_id,
                    None
                    if activation.candidate_variant_ref is None
                    else activation.candidate_variant_ref.identity.value,
                )
        else:
            assert isinstance(value, LifecycleSnapshot)
            self.snapshots.append(value)

    def rollback(self, transaction_id: str) -> None:
        self.calls.append(("rollback", transaction_id))
        if self.fail_rollback:
            raise OSError("injected rollback failure")
        self.staged.pop(transaction_id, None)

    def _stage(self, transaction_id: str, kind: str, value: object) -> None:
        self.calls.append((f"stage-{kind}", transaction_id))
        if self.fail_stage == kind:
            raise OSError(f"injected {kind} staging failure")
        self.staged[transaction_id] = (kind, value)


@dataclass
class _Harness:
    service: ProjectLifecycleService
    loader: _Loader
    leases: _Leases
    store: _TransactionStore


def _harness(
    active: ActiveProject | None,
    candidates: dict[str, ActiveProject],
    *,
    event: Callable | None = None,
) -> _Harness:
    loader = _Loader(candidates)
    leases = _Leases()
    store = _TransactionStore()
    sequence = iter(f"token-{index}" for index in range(100))
    factory = RepositoryLifecycleUnitOfWorkFactory(store, lambda: next(sequence))
    service = ProjectLifecycleService(
        loader,
        factory,
        active=active,
        leases=leases,
        token_factory=lambda: next(sequence),
        event_publisher=event,
    )
    return _Harness(service, loader, leases, store)


def _prepare_token(result) -> str:
    assert result.outcome is OperationOutcome.COMPLETED
    assert result.value is not None
    return result.value["token"]


def test_cancel_dirty_transition_has_zero_save_load_or_pointer_side_effects() -> None:
    old_project, old_variant = _refs("old-project", "old-variant")
    new_project, new_variant = _refs("new-project", "new-variant")
    old = _active(old_project, old_variant, revision=2, persisted_revision=1)
    harness = _harness(old, {new_project.identity.value: _active(new_project, new_variant)})

    result = harness.service.prepare_transition(
        TransitionTarget(new_project, new_variant),
        _context(),
        dirty_decision=DirtyDecision.CANCEL,
    )

    assert result.outcome is OperationOutcome.CANCELLED
    assert harness.service.active is old
    assert harness.loader.calls == []
    assert harness.store.calls == []
    assert harness.leases.acquired == []


def test_save_or_target_load_failure_keeps_old_dirty_context_usable() -> None:
    old_project, old_variant = _refs("old-project", "old-variant")
    new_project, new_variant = _refs("new-project", "new-variant")
    old = _active(old_project, old_variant, revision=2, persisted_revision=1)
    harness = _harness(old, {new_project.identity.value: _active(new_project, new_variant)})
    harness.store.fail_commit = True

    save_failed = harness.service.prepare_transition(
        TransitionTarget(new_project, new_variant),
        _context(),
        dirty_decision=DirtyDecision.SAVE,
    )
    assert save_failed.diagnostics[0].code == "ACTIVE_SAVE_FAILED"
    assert harness.service.active is old
    assert harness.service.active.dirty
    assert harness.loader.calls == []

    harness.store.fail_commit = False
    harness.loader.error = ValueError("invalid target schema")
    load_failed = harness.service.prepare_transition(
        TransitionTarget(new_project, new_variant),
        _context(),
        dirty_decision=DirtyDecision.DISCARD,
    )
    assert load_failed.diagnostics[0].code == "LIFECYCLE_PREPARE_FAILED"
    assert harness.service.active is old
    assert harness.leases.released == [("lease-1",)]


def test_commit_is_atomic_for_active_state_and_projection_publishes_after_swap() -> None:
    old_project, old_variant = _refs("old-project", "old-variant")
    new_project, new_variant = _refs("new-project", "new-variant")
    old_lease = LifecycleLease("old-lease", "owner")
    old = _active(old_project, old_variant, leases=(old_lease,))
    candidate = _active(new_project, new_variant, revision=5, persisted_revision=5)
    observed: list[str] = []
    service_box: list[ProjectLifecycleService] = []

    def publish(_event) -> None:
        active = service_box[0].active
        assert active is not None
        observed.append(active.project_ref.identity.value)

    harness = _harness(old, {new_project.identity.value: candidate}, event=publish)
    service_box.append(harness.service)
    token = _prepare_token(harness.service.prepare_transition(TransitionTarget(new_project, new_variant), _context()))

    assert harness.service.active is old
    assert harness.store.active_pointer is None
    committed = harness.service.commit_transition(token, _context())

    assert committed.outcome is OperationOutcome.COMPLETED
    assert harness.service.active is not old
    assert harness.service.active.project_ref == new_project
    assert harness.store.active_pointer == ("new-project", "new-variant")
    assert observed == ["new-project"]
    assert harness.leases.released == [("old-lease",)]


def test_reference_swap_failure_rolls_back_and_releases_candidate_even_if_cleanup_fails() -> None:
    old_project, old_variant = _refs("old-project", "old-variant")
    new_project, new_variant = _refs("new-project", "new-variant")
    old = _active(old_project, old_variant)
    harness = _harness(old, {new_project.identity.value: _active(new_project, new_variant)})
    token = _prepare_token(harness.service.prepare_transition(TransitionTarget(new_project, new_variant), _context()))
    harness.store.fail_commit = True
    harness.store.fail_rollback = True

    result = harness.service.commit_transition(token, _context())

    assert result.diagnostics[0].code == "LIFECYCLE_COMMIT_FAILED"
    assert harness.service.active is old
    assert harness.store.active_pointer is None
    assert harness.leases.released == [("lease-1",)]


def test_uow_begin_failure_is_structured_and_keeps_old_active_state() -> None:
    old_project, old_variant = _refs("old-project", "old-variant")
    new_project, new_variant = _refs("new-project", "new-variant")
    old = _active(old_project, old_variant)
    harness = _harness(old, {new_project.identity.value: _active(new_project, new_variant)})
    token = _prepare_token(harness.service.prepare_transition(TransitionTarget(new_project, new_variant), _context()))
    harness.store.fail_begin = True

    result = harness.service.commit_transition(token, _context())

    assert result.diagnostics[0].code == "LIFECYCLE_COMMIT_FAILED"
    assert harness.service.active is old
    assert harness.leases.released == [("lease-1",)]


def test_prepared_token_is_owner_bound_one_shot_and_stale_double_click_is_rejected() -> None:
    project_a, variant_a = _refs("project-a", "variant-a")
    project_b, variant_b = _refs("project-b", "variant-b")
    candidates = {
        project_a.identity.value: _active(project_a, variant_a),
        project_b.identity.value: _active(project_b, variant_b),
    }
    harness = _harness(None, candidates)
    token_a = _prepare_token(harness.service.prepare_transition(TransitionTarget(project_a, variant_a), _context()))
    token_b = _prepare_token(harness.service.prepare_transition(TransitionTarget(project_b, variant_b), _context()))

    wrong_owner = harness.service.commit_transition(token_a, _context("other"))
    assert wrong_owner.diagnostics[0].code == "PREPARED_TRANSITION_OWNER_MISMATCH"
    assert harness.service.commit_transition(token_a, _context()).outcome is OperationOutcome.COMPLETED
    replay = harness.service.commit_transition(token_a, _context())
    stale = harness.service.commit_transition(token_b, _context())

    assert replay.diagnostics[0].code == "PREPARED_TRANSITION_INVALID"
    assert stale.diagnostics[0].code == "PREPARED_TRANSITION_STALE"
    assert harness.leases.released == [("lease-2",)]


def test_edit_after_prepare_invalidates_transition_before_pointer_commit() -> None:
    old_project, old_variant = _refs("old-project", "old-variant")
    new_project, new_variant = _refs("new-project", "new-variant")
    old = _active(old_project, old_variant, revision=2, persisted_revision=2)
    harness = _harness(old, {new_project.identity.value: _active(new_project, new_variant)})
    token = _prepare_token(harness.service.prepare_transition(TransitionTarget(new_project, new_variant), _context()))
    assert old.variant is not None
    old.variant.commit(
        VariantChangeSet(old_variant, 2, (), (), (), "edit-run"),
        RequestContext(
            owner_id="owner",
            run_id="edit-run",
            project_id=old_project.identity.value,
            variant_id=old_variant.identity.value,
        ),
    )

    result = harness.service.commit_transition(token, _context())

    assert result.diagnostics[0].code == "PREPARED_TRANSITION_STALE"
    assert harness.service.active is old
    assert harness.store.active_pointer is None
    assert harness.leases.released == [("lease-1",)]


def test_projection_event_failure_does_not_revert_committed_domain_state() -> None:
    project, variant = _refs("project", "variant")

    def fail_event(_event) -> None:
        raise RuntimeError("projection failed")

    harness = _harness(None, {project.identity.value: _active(project, variant)}, event=fail_event)
    token = _prepare_token(harness.service.prepare_transition(TransitionTarget(project, variant), _context()))

    result = harness.service.commit_transition(token, _context())

    assert result.outcome is OperationOutcome.COMPLETED
    assert [item.code for item in result.diagnostics] == ["PROJECTION_EVENT_FAILED"]
    assert harness.service.active is not None
    assert harness.service.active.project_ref == project


def test_snapshot_load_keeps_formal_current_pointer_and_snapshot_save_does_not_change_it() -> None:
    project, variant = _refs("project", "formal-variant")
    source_ref = "快照/very-long-" + "路径" * 100
    candidate = _active(project, variant, revision=7, persisted_revision=7, source_ref=source_ref)
    harness = _harness(None, {project.identity.value: candidate})
    target = TransitionTarget(project, variant, snapshot_ref=source_ref)
    token = _prepare_token(harness.service.prepare_transition(target, _context()))
    assert harness.service.commit_transition(token, _context()).outcome is OperationOutcome.COMPLETED
    pointer = harness.store.active_pointer

    saved = harness.service.save_snapshot("审阅 快照", _context())

    assert saved.outcome is OperationOutcome.COMPLETED
    assert saved.value is not None and saved.value["current_pointer_changed"] is False
    assert harness.store.active_pointer == pointer == ("project", "formal-variant")
    assert harness.store.snapshots[0].formal_variant_ref == variant
    assert harness.service.active is not None
    assert harness.service.active.source_ref == source_ref


def test_export_revision_lease_fails_closed_after_variant_revision_changes() -> None:
    project, variant = _refs("project", "variant")
    active = _active(project, variant, revision=2, persisted_revision=2)
    harness = _harness(active, {})
    acquired = harness.service.acquire_export_lease(_context())
    assert acquired.value is not None
    token = acquired.value["token"]
    assert active.variant is not None
    active.variant.commit(
        VariantChangeSet(variant, 2, (), (), (), "mutation-run"),
        RequestContext(
            owner_id="owner",
            run_id="mutation-run",
            project_id=project.identity.value,
            variant_id=variant.identity.value,
        ),
    )

    changed = harness.service.validate_export_lease(token, _context())
    replay = harness.service.validate_export_lease(token, _context())

    assert changed.diagnostics[0].code == "EXPORT_VARIANT_REVISION_CHANGED"
    assert replay.diagnostics[0].code == "EXPORT_REVISION_LEASE_INVALID"


def test_save_uses_immutable_revision_capture_and_keeps_later_mutation_dirty() -> None:
    project, variant = _refs("project", "variant")
    active = _active(project, variant, revision=2, persisted_revision=1)
    harness = _harness(active, {})
    assert active.variant is not None

    def mutate_during_commit() -> None:
        active.variant.commit(
            VariantChangeSet(variant, 2, (), (), (), "later-run"),
            RequestContext(
                owner_id="owner",
                run_id="later-run",
                project_id=project.identity.value,
                variant_id=variant.identity.value,
            ),
        )

    harness.store.before_commit = mutate_during_commit
    result = harness.service.save_active(_context())

    assert result.diagnostics[0].code == "ACTIVE_SAVE_REVISION_CHANGED"
    assert harness.store.saved_revisions == [2]
    assert harness.service.active is not None
    assert harness.service.active.variant is active.variant
    assert harness.service.active.persisted_variant_revision == 2
    assert harness.service.active.dirty


def test_project_only_dirty_state_saves_without_requiring_an_active_variant() -> None:
    project = ProjectRef(ProjectId("project-only"))
    active = ActiveProject(
        project=_project(project, (), revision=2),
        variant=None,
        formal_variant_ref=None,
        persisted_project_revision=1,
        persisted_variant_revision=None,
    )
    harness = _harness(active, {})

    result = harness.service.save_active(_context())

    assert result.outcome is OperationOutcome.COMPLETED
    assert harness.store.saved_revisions == [-1]
    assert harness.service.active is not None
    assert not harness.service.active.dirty


def test_empty_project_and_close_transition_are_supported() -> None:
    project = ProjectRef(ProjectId("empty-project"))
    candidate = _active(project, None)
    harness = _harness(None, {project.identity.value: candidate})
    open_token = _prepare_token(harness.service.prepare_transition(TransitionTarget(project), _context()))
    assert harness.service.commit_transition(open_token, _context()).outcome is OperationOutcome.COMPLETED

    close_token = _prepare_token(harness.service.prepare_transition(TransitionTarget.close(), _context()))
    assert harness.service.commit_transition(close_token, _context()).outcome is OperationOutcome.COMPLETED
    assert harness.service.active is None
    assert harness.store.active_pointer is None


def test_multi_source_candidate_survives_prepare_commit_without_namespace_collapse() -> None:
    project, variant = _refs("multi-project", "multi-variant")
    namespace_a = SourceNamespace.from_fingerprint("esp", "a" * 64)
    namespace_b = SourceNamespace.from_fingerprint("json", "b" * 64)
    fingerprints = (
        SourceFingerprint(namespace_a, "a" * 64),
        SourceFingerprint(namespace_b, "b" * 64),
    )
    aggregate = VariantAggregate(VariantSnapshot(variant, fingerprints, (), revision=4))
    candidate = ActiveProject(
        project=_project(project, (variant,)),
        variant=aggregate,
        formal_variant_ref=variant,
        persisted_project_revision=0,
        persisted_variant_revision=4,
    )
    harness = _harness(None, {project.identity.value: candidate})

    token = _prepare_token(harness.service.prepare_transition(TransitionTarget(project, variant), _context()))
    assert harness.service.commit_transition(token, _context()).outcome is OperationOutcome.COMPLETED

    assert harness.service.active is not None and harness.service.active.variant is aggregate
    assert harness.service.active.variant.snapshot().source_fingerprints == fingerprints


def test_legacy_adapter_refuses_gui_switch_until_s05_injects_authoritative_baseline() -> None:
    project, variant = _refs("project", "variant")
    builder_calls: list[object] = []
    adapter = LegacyProjectLifecycleAdapter(
        lambda _target, baseline, _context: builder_calls.append(baseline) or _active(project, variant)
    )
    service = ProjectLifecycleService(
        adapter,
        RepositoryLifecycleUnitOfWorkFactory(_TransactionStore(), lambda: "uow-token"),
        token_factory=lambda: "prepare-token",
    )

    result = service.prepare_transition(TransitionTarget(project, variant), _context())

    assert not adapter.authoritative
    assert result.diagnostics[0].code == "LEGACY_SOURCE_BASELINE_REQUIRED"
    assert builder_calls == []
