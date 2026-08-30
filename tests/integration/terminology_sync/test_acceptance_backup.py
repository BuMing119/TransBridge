from __future__ import annotations

from collections import Counter
from dataclasses import replace
from pathlib import Path

from transbridge.application.terminology.effective import EffectiveSnapshotStatus, EffectiveTerminologySnapshot
from transbridge.application.terminology_sync.executor import ExecuteTerminologyBackupRequest, TerminologyBackupExecutor
from transbridge.application.terminology_sync.plan_models import TerminologySyncAction, TerminologySyncReason
from transbridge.application.terminology_sync.planner import TerminologySyncPlanner, TerminologySyncPlannerInput
from transbridge.application.terminology_sync.use_case import AuthorizedTerminologySyncPlan
from transbridge.config.paratranz import ParatranzConfig
from transbridge.config.paratranz_credentials import UnavailableCredentialStore
from transbridge.paratranz.api.paratranz_terms_api import ParatranzTermsAPI
from transbridge.paratranz.paratranz_client import RetryPolicy
from transbridge.paratranz.terms_service import ParaTranzTermsService
from transbridge.persistence.terminology import SqliteTerminologyRepository

from .controlled_server import ControlledParaTranzTermsServer
from .evidence import RELEASE_GATE_BLOCKED, TerminologySyncEvidenceManifest
from .scenario_builder import FIXED_TIME, TerminologySyncScenario, TerminologySyncScenarioBuilder


class LiveScenarioInputs:
    def __init__(
        self,
        scenario: TerminologySyncScenario,
        service: ParaTranzTermsService,
        repository: SqliteTerminologyRepository,
    ) -> None:
        self.scenario = scenario
        self.service = service
        self.repository = repository

    def load_for_plan(self, plan_hash: str) -> TerminologySyncPlannerInput:
        del plan_hash
        return self.load()

    def load(self) -> TerminologySyncPlannerInput:
        line = self.scenario.line
        return TerminologySyncPlannerInput(
            line,
            self.scenario.profile,
            self.scenario.local_snapshot,
            self.service.snapshot_terms(line.target.remote_project_id, page_size=2),
            self.repository.sync_state.get_baseline(line.line_id),
            self.repository.sync_state.list_item_links(line.line_id).items,
            self.scenario.binding_revision,
        )


def controlled_service(server: ControlledParaTranzTermsServer) -> ParaTranzTermsService:
    config = ParatranzConfig(
        token="controlled-test-canary",
        base_url=server.api_url,
        timeout=2,
        credential_store=UnavailableCredentialStore(),
    )
    api = ParatranzTermsAPI(config, retry_policy=RetryPolicy(max_attempts=1, initial_backoff=0, maximum_backoff=0))
    return ParaTranzTermsService(api, clock=lambda: FIXED_TIME)


def test_repeated_backup_is_zero_write_and_preserves_independent_and_lossy_terms(
    tmp_path: Path,
    request: object,
) -> None:
    builder = TerminologySyncScenarioBuilder(seed=5170801)
    initial = builder.backup(project_terms=1, plugin_terms=1, remote_independent=1)
    records = builder.server_records(initial.remote_snapshot)
    with ControlledParaTranzTermsServer(terms=records) as server:
        scenario = TerminologySyncScenarioBuilder(seed=5170801, endpoint=server.api_url).backup(
            project_terms=1,
            plugin_terms=1,
            remote_independent=1,
        )
        service = controlled_service(server)
        repository = SqliteTerminologyRepository.open(str(tmp_path), scenario.line.project_id)
        try:
            repository.sync_state.activate_line(scenario.line, scenario.profile)
            inputs = LiveScenarioInputs(scenario, service, repository)
            first_plan = TerminologySyncPlanner().plan(inputs.load())
            classified = {(item.action, item.reason) for item in first_plan.items}
            assert (TerminologySyncAction.CREATE_REMOTE, TerminologySyncReason.LOCAL_ONLY) in classified
            assert (TerminologySyncAction.SKIP, TerminologySyncReason.INDEPENDENT_REMOTE) in classified
            assert (TerminologySyncAction.LOSSY_MAPPING, TerminologySyncReason.PLUGIN_SCOPE) in classified

            executor = TerminologyBackupExecutor(service, repository.sync_state, inputs, clock=lambda: FIXED_TIME)
            first = executor.execute(
                ExecuteTerminologyBackupRequest(
                    AuthorizedTerminologySyncPlan(first_plan, "owner-1", "CONTROLLED_CONFIRMED"),
                    "run-first",
                )
            )
            writes_after_first = len(server.write_requests)
            second_plan = TerminologySyncPlanner().plan(inputs.load())
            assert not any(item.action.executable_remote for item in second_plan.items)
            second = executor.execute(
                ExecuteTerminologyBackupRequest(
                    AuthorizedTerminologySyncPlan(second_plan, "owner-1", "CONTROLLED_CONFIRMED"),
                    "run-second",
                )
            )

            assert writes_after_first == 1
            assert len(server.write_requests) == writes_after_first
            assert any(term["term"].startswith("Remote-independent") for term in server.terms)
            assert not any(str(term["term"]).startswith("Term-") and "Plugin" in str(term) for term in server.terms)
            baseline = repository.sync_state.get_baseline(scenario.line.line_id)
            assert baseline is not None and baseline.completed_run_id == "run-second"
            counts = Counter(item.status.value for item in (*first.outcomes, *second.outcomes))
            manifest = TerminologySyncEvidenceManifest(
                test_node_id=getattr(request, "node").nodeid,
                scenario_id=scenario.scenario_id,
                fixture_seed=scenario.seed,
                local_version_id=scenario.local_snapshot.version_id or "missing",
                local_content_digest=scenario.local_snapshot.content_digest or "missing",
                remote_snapshot_digest=inputs.load().remote_snapshot.observed_digest,
                baseline_revision=baseline.revision,
                plan_hash=second_plan.plan_hash,
                run_id="run-second",
                outcome_counts=tuple(counts.items()),
                request_counts=(("writes", len(server.write_requests)),),
            )
            assert manifest.release_gate == RELEASE_GATE_BLOCKED
            assert "controlled-test-canary" not in manifest.to_json()
        finally:
            repository.close()
            service.close()


def test_managed_delete_is_explicit_and_does_not_delete_independent_remote(tmp_path: Path) -> None:
    builder = TerminologySyncScenarioBuilder(seed=5170802)
    initial = builder.backup(project_terms=1, remote_independent=1)
    with ControlledParaTranzTermsServer(terms=builder.server_records(initial.remote_snapshot)) as server:
        scenario = TerminologySyncScenarioBuilder(seed=5170802, endpoint=server.api_url).backup(
            project_terms=1,
            remote_independent=1,
        )
        service = controlled_service(server)
        repository = SqliteTerminologyRepository.open(str(tmp_path), scenario.line.project_id)
        try:
            repository.sync_state.activate_line(scenario.line, scenario.profile)
            inputs = LiveScenarioInputs(scenario, service, repository)
            executor = TerminologyBackupExecutor(service, repository.sync_state, inputs, clock=lambda: FIXED_TIME)
            first_plan = TerminologySyncPlanner().plan(inputs.load())
            executor.execute(
                ExecuteTerminologyBackupRequest(
                    AuthorizedTerminologySyncPlan(first_plan, "owner-1", "NOT_REQUIRED"),
                    "run-create",
                )
            )
            managed_remote_id = repository.sync_state.list_item_links(scenario.line.line_id).items[0].remote_id
            assert managed_remote_id is not None
            remote_before_delete = {int(term["id"]): term for term in server.terms}
            independent_ids = set(remote_before_delete) - {managed_remote_id}
            inputs.scenario = replace(
                scenario,
                local_snapshot=EffectiveTerminologySnapshot(
                    scenario.line.project_id,
                    scenario.line.variant_id,
                    EffectiveSnapshotStatus.READY,
                    "version-delete",
                    "delete-local-digest",
                    (),
                ),
            )
            delete_plan = TerminologySyncPlanner().plan(inputs.load())

            delete_items = [item for item in delete_plan.items if item.action is TerminologySyncAction.DELETE_REMOTE]
            assert len(delete_items) == 1
            assert delete_items[0].remote_id == managed_remote_id
            assert delete_items[0].reason is TerminologySyncReason.LOCAL_DELETED
            assert delete_plan.requires_confirmation
            executor.execute(
                ExecuteTerminologyBackupRequest(
                    AuthorizedTerminologySyncPlan(delete_plan, "owner-1", "CONTROLLED_CONFIRMED"),
                    "run-delete",
                )
            )

            remaining_ids = {int(term["id"]) for term in server.terms}
            assert managed_remote_id not in remaining_ids
            assert independent_ids <= remaining_ids
            assert [request.method for request in server.write_requests] == ["POST", "DELETE"]
        finally:
            repository.close()
            service.close()
