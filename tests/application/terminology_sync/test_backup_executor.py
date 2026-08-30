from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from transbridge.ai_translator.term_formats import TermEntry
from transbridge.application.ports.paratranz import ExternalServiceCategory, ExternalServiceError
from transbridge.application.ports.paratranz_terms import (
    ParaTranzTerm,
    ParaTranzTermSnapshot,
    ParaTranzTermWriteResult,
    TermWriteOperation,
    TermWriteStatus,
)
from transbridge.application.tasks import TaskCancelled
from transbridge.application.terminology.effective import EffectiveSnapshotStatus, EffectiveTerminologySnapshot
from transbridge.application.terminology.models import DecisionStatus, TermDecision
from transbridge.application.terminology_sync.execution_models import TerminologySyncItemStatus
from transbridge.application.terminology_sync.executor import (
    ExecuteTerminologyBackupRequest,
    TerminologyBackupExecutor,
)
from transbridge.application.terminology_sync.identity import sync_line_id
from transbridge.application.terminology_sync.models import (
    TerminologySyncCommit,
    TerminologySyncLine,
    TerminologySyncMode,
    TerminologySyncOutcome,
    TerminologySyncProfile,
    TerminologySyncTarget,
    TerminologySyncTargetBinding,
)
from transbridge.application.terminology_sync.planner import TerminologySyncPlanner, TerminologySyncPlannerInput
from transbridge.application.terminology_sync.use_case import (
    AuthorizedTerminologySyncPlan,
    TerminologySyncPlanStaleError,
)


class _FreshInputs:
    def __init__(self, value: TerminologySyncPlannerInput) -> None:
        self.value = value

    def load_for_plan(self, plan_hash: str) -> TerminologySyncPlannerInput:
        return self.value


class _State:
    def __init__(self) -> None:
        self.commits: list[TerminologySyncCommit] = []

    def commit_run(self, commit: TerminologySyncCommit, *, expected_baseline_revision: int | None) -> object:
        self.commits.append(commit)
        return commit.baseline


class _InboundStore:
    def __init__(self) -> None:
        self.change_sets = []

    def save_change_set(self, change_set):
        self.change_sets.append(change_set)
        return change_set


class _Remote:
    def __init__(self, error: ExternalServiceError | None = None, *, cancel_after_write: bool = False) -> None:
        self.error = error
        self.cancel_after_write = cancel_after_write
        self.create_calls = 0

    def create_term(self, project_id: int, write: object, *, cancellation: object = None) -> ParaTranzTermWriteResult:
        self.create_calls += 1
        if self.error is not None:
            raise self.error
        if self.cancel_after_write:
            raise TaskCancelled("cancelled after controlled remote commit")
        return ParaTranzTermWriteResult(
            TermWriteOperation.CREATE,
            101,
            "revision-1",
            "b" * 64,
            "request-1",
            TermWriteStatus.CONFIRMED,
        )

    def update_term(self, project_id: int, write: object, *, cancellation: object = None) -> object:
        raise AssertionError("unexpected update")

    def delete_term(self, project_id: int, remote_id: int, **kwargs: object) -> object:
        raise AssertionError("unexpected delete")


def _executor(
    remote: _Remote,
    *,
    bindings: object | None = None,
) -> tuple[TerminologyBackupExecutor, _State, object, _FreshInputs]:
    inputs = _inputs()
    plan = TerminologySyncPlanner().plan(inputs)
    state = _State()
    fresh = _FreshInputs(inputs)
    return TerminologyBackupExecutor(remote, state, fresh, bindings=bindings), state, plan, fresh


class _Bindings:
    def __init__(self, value: TerminologySyncTargetBinding | None) -> None:
        self.value = value

    def resolve_target_binding(self, project_id: str) -> TerminologySyncTargetBinding | None:
        assert project_id == "project-1"
        return self.value


def _inputs() -> TerminologySyncPlannerInput:
    target = TerminologySyncTarget("https://paratranz.cn", 7, 123)
    line_id = sync_line_id(
        project_id="project-1",
        variant_id="variant-1",
        target_identity=target.target_id,
        profile_revision=1,
    )
    line = TerminologySyncLine(
        line_id,
        "project-1",
        "variant-1",
        target,
        1,
        "2026-08-30T00:00:00+00:00",
    )
    decision = TermDecision(
        "local-1",
        "project-1",
        "variant-1",
        "Sword",
        "sword",
        "剑",
        status=DecisionStatus.ADOPTED,
    )
    return TerminologySyncPlannerInput(
        line,
        TerminologySyncProfile(line_id, 2),
        EffectiveTerminologySnapshot(
            "project-1",
            "variant-1",
            EffectiveSnapshotStatus.READY,
            "version-1",
            "local-version-digest",
            (decision,),
        ),
        ParaTranzTermSnapshot(123, (), "a" * 64, datetime(2026, 8, 30, tzinfo=UTC), True),
        binding_revision=4,
    )


def test_backup_executor_commits_confirmed_remote_create_and_link() -> None:
    remote = _Remote()
    executor, state, plan, _ = _executor(remote)
    authorized = AuthorizedTerminologySyncPlan(plan, "owner-1", "NOT_REQUIRED")

    result = executor.execute(ExecuteTerminologyBackupRequest(authorized, "run-1"))

    assert remote.create_calls == 1
    assert [item.status for item in result.outcomes] == [TerminologySyncItemStatus.SUCCEEDED]
    assert result.retry_token is None
    assert len(state.commits) == 1
    assert state.commits[0].item_links[0].link.remote_id == 101
    assert state.commits[0].baseline.completed_run_id == "run-1"


def test_bidirectional_executor_runs_remote_half_through_the_same_fresh_plan() -> None:
    remote = _Remote()
    inputs = _inputs()
    inputs = replace(inputs, profile=replace(inputs.profile, mode=TerminologySyncMode.BIDIRECTIONAL))
    plan = TerminologySyncPlanner().plan(inputs)
    state = _State()
    executor = TerminologyBackupExecutor(remote, state, _FreshInputs(inputs), inbound_store=_InboundStore())

    result = executor.execute(
        ExecuteTerminologyBackupRequest(AuthorizedTerminologySyncPlan(plan, "owner-1", "NOT_REQUIRED"), "run-bi")
    )

    assert remote.create_calls == 1
    assert result.outcomes[0].status is TerminologySyncItemStatus.SUCCEEDED
    assert state.commits[0].baseline.completed_run_id == "run-bi"


def test_executor_rejects_project_binding_revision_drift_before_remote_write() -> None:
    remote = _Remote()
    inputs = _inputs()
    plan = TerminologySyncPlanner().plan(inputs)

    class _Bindings:
        @staticmethod
        def resolve_target_binding(project_id: str):
            return TerminologySyncTargetBinding(project_id, inputs.line.target, plan.binding_revision + 1)

    executor = TerminologyBackupExecutor(remote, _State(), _FreshInputs(inputs), bindings=_Bindings())

    try:
        executor.execute(
            ExecuteTerminologyBackupRequest(AuthorizedTerminologySyncPlan(plan, "owner-1", "NOT_REQUIRED"), "run-drift")
        )
    except Exception as exc:  # noqa: BLE001 - assert stable application error below
        assert getattr(exc, "code", None) == "STALE_TERMINOLOGY_SYNC_PLAN"
    else:
        raise AssertionError("binding drift must reject terminology sync execution")
    assert remote.create_calls == 0


def test_unknown_create_requires_reconcile_and_is_not_retried_blindly() -> None:
    remote = _Remote(ExternalServiceError(ExternalServiceCategory.TIMEOUT, "timed out", request_id="request-1"))
    executor, state, plan, _ = _executor(remote)
    authorized = AuthorizedTerminologySyncPlan(plan, "owner-1", "NOT_REQUIRED")

    first = executor.execute(ExecuteTerminologyBackupRequest(authorized, "run-1"))
    second = executor.execute(ExecuteTerminologyBackupRequest(authorized, "run-2", retry_token=first.retry_token))

    assert remote.create_calls == 1
    assert first.reconcile_required
    assert first.retry_token is not None
    assert second.reconcile_required
    assert second.outcomes[0].code == "RECONCILE_REQUIRED"
    assert len(state.commits) == 1
    unknown_link = state.commits[0].item_links[0].link
    assert unknown_link.last_outcome is TerminologySyncOutcome.UNKNOWN
    assert unknown_link.common_content_digest is None


def test_reconcile_confirms_timeout_after_create_without_repeating_write() -> None:
    remote = _Remote(ExternalServiceError(ExternalServiceCategory.TIMEOUT, "timed out"))
    executor, state, plan, fresh = _executor(remote)
    authorized = AuthorizedTerminologySyncPlan(plan, "owner-1", "NOT_REQUIRED")
    first = executor.execute(ExecuteTerminologyBackupRequest(authorized, "run-1"))
    first_commit = state.commits[0]
    created = ParaTranzTerm(
        101,
        TermEntry("Sword", "剑", "paratranz"),
        "revision-1",
        "c" * 64,
    )
    fresh.value = replace(
        fresh.value,
        remote_snapshot=ParaTranzTermSnapshot(
            123,
            (created,),
            "d" * 64,
            datetime(2026, 8, 30, 0, 1, tzinfo=UTC),
            True,
        ),
        baseline=first_commit.baseline,
        item_links=(first_commit.item_links[0].link,),
    )

    reconciled = executor.reconcile(ExecuteTerminologyBackupRequest(authorized, "run-2", retry_token=first.retry_token))

    assert remote.create_calls == 1
    assert reconciled.outcomes[0].status is TerminologySyncItemStatus.RECONCILED
    assert not reconciled.reconcile_required
    assert reconciled.retry_token is None
    assert len(state.commits) == 2
    assert state.commits[1].item_links[0].link.remote_id == 101


def test_cancellation_crossing_a_remote_write_boundary_is_durable_unknown() -> None:
    remote = _Remote(cancel_after_write=True)
    executor, state, plan, _ = _executor(remote)

    result = executor.execute(
        ExecuteTerminologyBackupRequest(
            AuthorizedTerminologySyncPlan(plan, "owner-1", "NOT_REQUIRED"),
            "run-cancel-after-commit",
        )
    )

    assert remote.create_calls == 1
    assert result.reconcile_required and result.retry_token is not None
    assert result.outcomes[0].status is TerminologySyncItemStatus.UNKNOWN
    assert result.outcomes[0].code == "REMOTE_CANCELLED"
    assert len(state.commits) == 1
    assert state.commits[0].item_links[0].link.last_outcome is TerminologySyncOutcome.UNKNOWN


def test_fresh_binding_contract_rejects_revision_or_target_drift_before_remote_write() -> None:
    original = _inputs().line.target
    stale_bindings = (
        TerminologySyncTargetBinding("project-1", original, 5),
        TerminologySyncTargetBinding("project-1", TerminologySyncTarget("https://other.invalid", 7, 123), 4),
        TerminologySyncTargetBinding("project-1", TerminologySyncTarget(original.endpoint, 8, 123), 4),
        TerminologySyncTargetBinding("project-1", TerminologySyncTarget(original.endpoint, 7, 456), 4),
    )

    for binding in stale_bindings:
        remote = _Remote()
        executor, state, plan, _ = _executor(remote, bindings=_Bindings(binding))
        with pytest.raises(TerminologySyncPlanStaleError, match="binding changed"):
            executor.execute(
                ExecuteTerminologyBackupRequest(
                    AuthorizedTerminologySyncPlan(plan, "owner-1", "NOT_REQUIRED"),
                    "run-stale-binding",
                )
            )
        assert remote.create_calls == 0
        assert state.commits == []
