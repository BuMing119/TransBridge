from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import time

import pytest

from transbridge.application.tasks import TaskCancelled
from transbridge.application.terminology.errors import RevisionConflictError
from transbridge.application.terminology_sync.execution_models import TerminologySyncItemStatus
from transbridge.application.terminology_sync.executor import ExecuteTerminologyBackupRequest, TerminologyBackupExecutor
from transbridge.application.terminology_sync.models import (
    TerminologySyncBaseline,
    TerminologySyncCommit,
    TerminologySyncRunOutcome,
    TerminologySyncRunRecord,
)
from transbridge.application.terminology_sync.plan_models import TerminologySyncAction
from transbridge.application.terminology_sync.planner import TerminologySyncPlanner
from transbridge.application.terminology_sync.use_case import (
    AuthorizedTerminologySyncPlan,
    TerminologySyncPlanStaleError,
)
from transbridge.persistence.terminology import SqliteTerminologyRepository

from .controlled_server import ControlledFault, ControlledFaultMode, ControlledParaTranzTermsServer, NoNetworkSpy
from .scenario_builder import FIXED_TIME, TerminologySyncScenarioBuilder
from .test_acceptance_backup import LiveScenarioInputs, controlled_service


class _Cancellation:
    def __init__(self) -> None:
        self.is_cancelled = False
        self.requested_at: float | None = None

    def request(self) -> None:
        self.is_cancelled = True
        self.requested_at = time.perf_counter()

    def wait(self, timeout: float | None = None) -> bool:
        del timeout
        return self.is_cancelled

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled:
            raise TaskCancelled("controlled terminology sync cancellation")


class _CancelAfterFirstWrite:
    def __init__(self, service: object, cancellation: _Cancellation) -> None:
        self._service = service
        self._cancellation = cancellation

    def create_term(self, project_id: int, write: object, *, cancellation: object = None) -> object:
        result = self._service.create_term(project_id, write, cancellation=cancellation)
        self._cancellation.request()
        return result

    def update_term(self, project_id: int, write: object, *, cancellation: object = None) -> object:
        return self._service.update_term(project_id, write, cancellation=cancellation)

    def delete_term(self, project_id: int, remote_id: int, *, cancellation: object = None) -> object:
        return self._service.delete_term(project_id, remote_id, cancellation=cancellation)


def test_timeout_after_commit_is_unknown_then_reconciled_without_a_second_write(tmp_path: Path) -> None:
    with ControlledParaTranzTermsServer() as server:
        scenario = TerminologySyncScenarioBuilder(seed=5170803, endpoint=server.api_url).backup(project_terms=1)
        service = controlled_service(server)
        repository = SqliteTerminologyRepository.open(str(tmp_path), scenario.line.project_id)
        try:
            repository.sync_state.activate_line(scenario.line, scenario.profile)
            inputs = LiveScenarioInputs(scenario, service, repository)
            plan = TerminologySyncPlanner().plan(inputs.load())
            assert [item.action for item in plan.items] == [TerminologySyncAction.CREATE_REMOTE]
            authorized = AuthorizedTerminologySyncPlan(plan, "owner-1", "NOT_REQUIRED")
            executor = TerminologyBackupExecutor(service, repository.sync_state, inputs, clock=lambda: FIXED_TIME)
            server.queue_fault(ControlledFault("POST", ControlledFaultMode.DISCONNECT_AFTER_COMMIT))

            unknown = executor.execute(ExecuteTerminologyBackupRequest(authorized, "run-unknown"))

            assert unknown.reconcile_required and unknown.retry_token is not None
            assert len(server.write_requests) == 1
            assert server.write_requests[0].committed and server.write_requests[0].response_status is None
            unknown_link = repository.sync_state.list_item_links(scenario.line.line_id).items[0]
            assert unknown_link.common_content_digest is None
            reconciled = executor.reconcile(
                ExecuteTerminologyBackupRequest(
                    authorized,
                    "run-reconcile",
                    retry_token=unknown.retry_token,
                )
            )

            assert not reconciled.reconcile_required
            assert reconciled.outcomes[0].status.value == "reconciled"
            assert len(server.write_requests) == 1
            link = repository.sync_state.list_item_links(scenario.line.line_id).items[0]
            assert link.common_content_digest is not None
            assert link.last_outcome is not None and link.last_outcome.value == "confirmed"
        finally:
            repository.close()
            service.close()


def test_baseline_cas_conflict_rolls_back_run_and_preserves_first_commit(tmp_path: Path) -> None:
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    scenario = TerminologySyncScenarioBuilder(seed=5170804).backup(project_terms=1)
    try:
        repository.sync_state.activate_line(scenario.line, scenario.profile)
        first_run = TerminologySyncRunRecord(
            "run-winner",
            scenario.line.line_id,
            "plan-winner",
            "owner-1",
            scenario.line.target.target_id,
            None,
            TerminologySyncRunOutcome.SUCCEEDED,
            FIXED_TIME.isoformat(),
            FIXED_TIME.isoformat(),
        )
        first_baseline = TerminologySyncBaseline(
            scenario.line.line_id,
            0,
            scenario.local_snapshot.version_id or "version",
            scenario.local_snapshot.content_digest or "local",
            scenario.remote_snapshot.observed_digest,
            "common-winner",
            first_run.run_id,
        )
        repository.sync_state.commit_run(
            TerminologySyncCommit(first_run, (), first_baseline),
            expected_baseline_revision=None,
        )
        losing_run = replace(first_run, run_id="run-loser", plan_id="plan-loser")
        losing_baseline = replace(first_baseline, completed_run_id=losing_run.run_id)

        with pytest.raises(RevisionConflictError):
            repository.sync_state.commit_run(
                TerminologySyncCommit(losing_run, (), losing_baseline),
                expected_baseline_revision=None,
            )

        assert repository.sync_state.get_baseline(scenario.line.line_id) == first_baseline
        assert repository.sync_state.list_outcomes("run-loser").items == ()
        assert (
            repository._connection.execute("SELECT 1 FROM terminology_sync_runs WHERE run_id = 'run-loser'").fetchone()
            is None
        )
    finally:
        repository.close()


def test_absent_to_revision_zero_invalidates_plan_before_remote_write(tmp_path: Path) -> None:
    with ControlledParaTranzTermsServer() as server:
        scenario = TerminologySyncScenarioBuilder(seed=5170807, endpoint=server.api_url).backup(project_terms=1)
        service = controlled_service(server)
        repository = SqliteTerminologyRepository.open(str(tmp_path), scenario.line.project_id)
        try:
            repository.sync_state.activate_line(scenario.line, scenario.profile)
            inputs = LiveScenarioInputs(scenario, service, repository)
            stale_plan = TerminologySyncPlanner().plan(inputs.load())
            assert stale_plan.baseline_revision is None
            winner = TerminologySyncRunRecord(
                "run-concurrent",
                scenario.line.line_id,
                "plan-concurrent",
                "owner-2",
                scenario.line.target.target_id,
                None,
                TerminologySyncRunOutcome.SUCCEEDED,
                FIXED_TIME.isoformat(),
                FIXED_TIME.isoformat(),
            )
            repository.sync_state.commit_run(
                TerminologySyncCommit(
                    winner,
                    (),
                    TerminologySyncBaseline(
                        scenario.line.line_id,
                        0,
                        scenario.local_snapshot.version_id or "version",
                        scenario.local_snapshot.content_digest or "local",
                        scenario.remote_snapshot.observed_digest,
                        "concurrent-common",
                        winner.run_id,
                    ),
                ),
                expected_baseline_revision=None,
            )
            executor = TerminologyBackupExecutor(service, repository.sync_state, inputs, clock=lambda: FIXED_TIME)

            with pytest.raises(TerminologySyncPlanStaleError):
                executor.execute(
                    ExecuteTerminologyBackupRequest(
                        AuthorizedTerminologySyncPlan(stale_plan, "owner-1", "NOT_REQUIRED"),
                        "run-stale",
                    )
                )

            assert server.write_requests == ()
            assert (
                repository._connection.execute(
                    "SELECT 1 FROM terminology_sync_runs WHERE run_id = 'run-stale'"
                ).fetchone()
                is None
            )
        finally:
            repository.close()
            service.close()


def test_default_disabled_local_snapshot_and_storage_do_not_touch_network(tmp_path: Path) -> None:
    spy = NoNetworkSpy()
    scenario = TerminologySyncScenarioBuilder(seed=5170805).backup(project_terms=1)
    repository = SqliteTerminologyRepository.open(str(tmp_path), scenario.line.project_id)
    try:
        repository.sync_state.activate_line(scenario.line, scenario.profile)
        local = scenario.local_snapshot
        assert local.status.value == "ready"
        assert repository.sync_state.get_baseline(scenario.line.line_id) is None
        assert spy.calls == []
    finally:
        repository.close()


def test_cancellation_returns_feedback_within_500ms_and_stops_new_remote_writes(tmp_path: Path) -> None:
    with ControlledParaTranzTermsServer() as server:
        scenario = TerminologySyncScenarioBuilder(seed=5170808, endpoint=server.api_url).backup(project_terms=2)
        service = controlled_service(server)
        repository = SqliteTerminologyRepository.open(str(tmp_path), scenario.line.project_id)
        try:
            repository.sync_state.activate_line(scenario.line, scenario.profile)
            inputs = LiveScenarioInputs(scenario, service, repository)
            plan = TerminologySyncPlanner().plan(inputs.load())
            assert sum(item.action.executable_remote for item in plan.items) == 2
            cancellation = _Cancellation()
            executor = TerminologyBackupExecutor(
                _CancelAfterFirstWrite(service, cancellation),
                repository.sync_state,
                inputs,
                clock=lambda: FIXED_TIME,
            )

            result = executor.execute(
                ExecuteTerminologyBackupRequest(
                    AuthorizedTerminologySyncPlan(plan, "owner-1", "NOT_REQUIRED"),
                    "run-cancelled",
                    cancellation=cancellation,
                )
            )

            assert cancellation.requested_at is not None
            assert time.perf_counter() - cancellation.requested_at < 0.5
            assert len(server.write_requests) == 1
            assert [item.status for item in result.outcomes] == [
                TerminologySyncItemStatus.SUCCEEDED,
                TerminologySyncItemStatus.CANCELLED,
            ]
            assert result.run_id == "run-cancelled"
        finally:
            repository.close()
            service.close()
