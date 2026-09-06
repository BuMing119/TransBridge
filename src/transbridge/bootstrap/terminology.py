"""Production composition for the project terminology workbench."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING

from transbridge.application.contracts import RequestContext
from transbridge.application.io import default_format_catalog
from transbridge.application.projects import ProjectLifecycleService
from transbridge.application.terminology.effective import SnapshotEffectiveTerminologyPort
from transbridge.application.terminology.input_capture import BuildInputCaptureService
from transbridge.application.terminology.ports import PageRequest
from transbridge.application.terminology.runtime import TerminologyTaskEntrypoint, TerminologyWorkloadRegistry
from transbridge.application.terminology.workloads import TerminologyWorkloadType
from transbridge.application.terminology_profiles import (
    ProfiledEffectiveTerminologySnapshotPort,
    TerminologyProfileService,
    is_profiled_version_id,
)
from transbridge.application.terminology_sync.draft_import import InboundDraftImportService
from transbridge.application.terminology_sync.draft_import_models import DraftImportChoice, DraftImportSelection
from transbridge.application.terminology_sync.executor import TerminologyBackupExecutor, TerminologySyncFreshInputPort
from transbridge.application.terminology_sync.identity import sync_line_id
from transbridge.application.terminology_sync.inbound_service import DurableTerminologyInboundService
from transbridge.application.terminology_sync.mapping import local_content
from transbridge.application.terminology_sync.models import (
    TerminologySyncLine,
    TerminologySyncMode,
    TerminologySyncOutcome,
    TerminologySyncProfile,
    TerminologySyncTarget,
    TerminologySyncTargetBinding,
    TerminologySyncTombstone,
)
from transbridge.application.terminology_sync.planner import TerminologySyncPlanner, TerminologySyncPlannerInput
from transbridge.application.terminology_sync.service import (
    TerminologySyncApplicationService,
    TerminologySyncContextPort,
    TerminologySyncPreflight,
)
from transbridge.application.terminology_sync.task_adapter import TerminologySyncTaskEntrypoint
from transbridge.application.terminology_sync.use_case import (
    CreateTerminologySyncPlanRequest,
    TerminologySyncPlanningInputPort,
    TerminologySyncPlanningUseCase,
    TerminologySyncPlanStaleError,
)
from transbridge.config import ParatranzConfig
from transbridge.persistence.terminology import SqliteEffectiveTerminologySnapshotPort

if TYPE_CHECKING:
    from transbridge.application.translation.terminology_run_snapshot import TerminologyRunSnapshotRef
    from transbridge.paratranz.terms_service import ParaTranzTermsService

from .terminology_storage import (
    FilesystemSourceLeases,
    FormatCapabilities,
    LifecycleCapture,
    ProductionState,
    ProductionTerminologyCommitPort,
    ProjectTerminologyRepositories,
    RepositoryBaselines,
)
from .terminology_workloads import (
    BuildRunner,
    ChangelogRunner,
    HistoryCompareRunner,
    ProductionTerminologyCommands,
    PublishRunner,
    ReportRunner,
)


@dataclass(frozen=True, slots=True)
class ProductionTerminologyComposition:
    repositories: ProjectTerminologyRepositories
    build_inputs: BuildInputCaptureService
    workloads: TerminologyWorkloadRegistry
    tasks: TerminologyTaskEntrypoint
    commands: ProductionTerminologyCommands
    commit_port: ProductionTerminologyCommitPort
    lifecycle: ProjectLifecycleService | None = None
    ids: object | None = None
    clock: object | None = None
    _sync_services: dict[tuple[str, str, str], TerminologySyncApplicationService] = field(
        default_factory=dict, compare=False, repr=False
    )
    _profile_services: dict[str, TerminologyProfileService] = field(default_factory=dict, compare=False, repr=False)
    _sync_resources: list[ParaTranzTermsService] = field(default_factory=list, compare=False, repr=False)
    _sync_lock: RLock = field(default_factory=RLock, compare=False, repr=False)

    def services_for(self, context: RequestContext):
        from transbridge.ui.tools.terminology.presenter import TerminologyUiServices

        queries = None if context.project_id is None else self.repositories.for_project(context.project_id)
        sync = None
        if context.project_id is not None and context.variant_id is not None:
            sync = self.sync_service_for(context)
        return TerminologyUiServices(self.build_inputs, queries, self.commands, self.tasks.runtime, sync)

    def sync_service_for(self, context: RequestContext) -> TerminologySyncApplicationService:
        if context.project_id is None or context.variant_id is None:
            raise ValueError("terminology sync requires Project and Variant context")
        key = (context.owner_id, context.project_id, context.variant_id)
        with self._sync_lock:
            existing = self._sync_services.get(key)
            if existing is not None:
                return existing
            # Keep generic runtime composition headless. The ParaTranz adapter
            # reaches the HTTP/infra package and is only needed after a user
            # explicitly opens a scoped synchronization workflow.
            from transbridge.paratranz.terms_service import ParaTranzTermsService

            config = ParatranzConfig.create_or_load()
            remote = ParaTranzTermsService.from_config(config)
            contexts = _ProductionSyncContext(self, config)
            inputs = _ProductionSyncInputs(self, remote)
            planning = TerminologySyncPlanningUseCase(inputs)
            repository = self.repositories.for_project(context.project_id)
            inbound_store = repository.inbound_reviews
            inbound = _production_inbound_service(repository, self.lifecycle, self.clock, self.ids)
            executor = TerminologyBackupExecutor(
                remote,
                repository.sync_state,
                inputs,
                bindings=_ProductionSyncBindingResolver(self.lifecycle),
                inbound_store=inbound_store,
            )
            task_entrypoint = TerminologySyncTaskEntrypoint(self.tasks.runtime, executor)
            service = TerminologySyncApplicationService(
                contexts=contexts,
                planning=planning,
                tasks=task_entrypoint,
                runtime=self.tasks.runtime,
                inbound=inbound,
                bidirectional_tasks=task_entrypoint,
            )
            self._sync_resources.append(remote)
            self._sync_services[key] = service
            return service

    def profile_service_for(self, project_id: str) -> TerminologyProfileService:
        """Return the one profile application service bound to a Project repository."""

        if not project_id.strip():
            raise ValueError("terminology profile service requires a Project identity")
        with self._sync_lock:
            existing = self._profile_services.get(project_id)
            if existing is not None:
                return existing
            repository = self.repositories.for_project(project_id)
            now = getattr(getattr(self, "clock", None), "now", None)
            new_id = getattr(getattr(self, "ids", None), "new_id", None)
            service = TerminologyProfileService(
                repository.localization_profiles,
                now=now if callable(now) else None,
                new_id=new_id if callable(new_id) else None,
            )
            self._profile_services[project_id] = service
            return service

    def base_terminology_snapshot(self, project_id: str, variant_id: str):
        """Read the unprofiled snapshot used to prove export completeness."""

        repository = self.repositories.for_project(project_id)
        return SqliteEffectiveTerminologySnapshotPort(repository).snapshot(project_id, variant_id)

    def close(self) -> None:
        with self._sync_lock:
            resources = tuple(self._sync_resources)
            self._sync_resources.clear()
            self._sync_services.clear()
            self._profile_services.clear()
        for resource in reversed(resources):
            resource.close()
        self.repositories.close()

    def effective_adapter(self, project_id: str, variant_id: str):
        """Create an adapter enabled by the existence of a published version."""

        from transbridge.ai_translator.project_terminology_adapter import (
            ProjectTerminologyAdapter,
            PublishedEffectiveTerminologyGate,
        )

        repository = self.repositories.for_project(project_id)
        snapshots = ProfiledEffectiveTerminologySnapshotPort(
            SqliteEffectiveTerminologySnapshotPort(repository),
            self.profile_service_for(project_id),
        )
        return ProjectTerminologyAdapter(
            SnapshotEffectiveTerminologyPort(snapshots),
            PublishedEffectiveTerminologyGate(
                lambda candidate_project, candidate_variant: (
                    repository.effective_version(candidate_project, candidate_variant) is not None
                )
            ),
        )

    def freeze_echo_links(self, ref: TerminologyRunSnapshotRef):
        """Freeze only baseline-proven ParaTranz echoes for an exact AI version."""

        from transbridge.ai_translator.legacy_term_policy import (
            ConfirmedTerminologyEchoLink,
            FrozenTerminologyEchoLinks,
        )
        from transbridge.application.projects import project_paratranz_binding

        if is_profiled_version_id(ref.version_id):
            return FrozenTerminologyEchoLinks(
                ref.local_project_id,
                ref.local_variant_id,
                "profiled-effective:no-remote-target",
                "unavailable",
                available=False,
                diagnostic=(
                    "ParaTranz legacy terminology is disabled for a profiled AI run until an explicit "
                    "profile-to-remote mapping is available"
                ),
            )

        lifecycle = self.lifecycle
        active = None if lifecycle is None else lifecycle.active
        if active is None or active.project_ref.identity.value != ref.local_project_id:
            return None
        binding = project_paratranz_binding(active.project)
        if binding is None:
            return None
        target = TerminologySyncTarget(binding.endpoint, binding.account_user_id, binding.project_id)
        repository = self.repositories.for_project(ref.local_project_id)
        state = repository.sync_state
        line_state = state.resolve_line(ref.local_project_id, ref.local_variant_id, target)
        line = line_state.line
        if line is None:
            return None
        baseline = line_state.baseline
        revision = "unavailable" if baseline is None else str(baseline.revision)
        if (
            baseline is None
            or ref.version_id is None
            or ref.content_digest is None
            or baseline.local_version_id != ref.version_id
            or baseline.local_content_digest != ref.content_digest
        ):
            return FrozenTerminologyEchoLinks(
                ref.local_project_id,
                ref.local_variant_id,
                target.target_id,
                revision,
                available=False,
                diagnostic="the exact terminology sync baseline is unavailable for this AI run",
            )
        snapshot = SqliteEffectiveTerminologySnapshotPort(repository).snapshot(
            ref.local_project_id,
            ref.local_variant_id,
            ref.version_id,
        )
        decisions = {decision.term_id: decision for decision in snapshot.decisions}
        links = []
        request = PageRequest(limit=1000)
        while True:
            page = state.list_item_links(line.line_id, request)
            for link in page.items:
                decision = decisions.get(link.local_term_id or "")
                if (
                    decision is None
                    or link.remote_id is None
                    or link.local_version_id != ref.version_id
                    or link.tombstone is not TerminologySyncTombstone.LIVE
                    or link.last_outcome not in {TerminologySyncOutcome.CONFIRMED, TerminologySyncOutcome.RECONCILED}
                    or link.common_content_digest != local_content(decision).digest
                ):
                    continue
                links.append(
                    ConfirmedTerminologyEchoLink(
                        local_project_id=ref.local_project_id,
                        local_variant_id=ref.local_variant_id,
                        remote_target_id=target.target_id,
                        remote_term_id=str(link.remote_id),
                        local_term_id=decision.term_id,
                        local_version_id=ref.version_id,
                        # This is deliberately the whole-version digest. Item
                        # equality is proven separately by common_content_digest.
                        local_content_digest=baseline.local_content_digest,
                        original=decision.original,
                        translation=decision.translation,
                    )
                )
            if page.next_cursor is None:
                break
            request = PageRequest(limit=1000, cursor=page.next_cursor)
        return FrozenTerminologyEchoLinks(
            ref.local_project_id,
            ref.local_variant_id,
            target.target_id,
            str(baseline.revision),
            tuple(links),
        )


class _ProductionSyncContext(TerminologySyncContextPort):
    def __init__(self, composition: ProductionTerminologyComposition, config: ParatranzConfig) -> None:
        self._composition = composition
        self._config = config

    def preflight(self, context: RequestContext, mode) -> TerminologySyncPreflight:
        mode = TerminologySyncMode(mode)
        diagnostics: list[str] = []
        if context.project_id is None:
            diagnostics.append("未打开 Project")
        if context.variant_id is None:
            diagnostics.append("未选择 active Variant")
        active = None if self._composition.lifecycle is None else self._composition.lifecycle.active
        binding = None
        if active is not None and context.project_id == active.project_ref.identity.value:
            from transbridge.application.projects import project_paratranz_binding

            binding = project_paratranz_binding(active.project)
        if binding is None:
            diagnostics.append("当前 Project 未绑定 ParaTranz 目标")
        if self._config.token is None:
            diagnostics.append("ParaTranz 凭据未配置")
        target = None
        line_state = None
        local = None
        if context.project_id is not None and context.variant_id is not None:
            repository = self._composition.repositories.for_project(context.project_id)
            local = SqliteEffectiveTerminologySnapshotPort(repository).snapshot(context.project_id, context.variant_id)
            if local.version_id is None:
                diagnostics.append("当前 Variant 没有已发布术语版本")
            if binding is not None:
                target = TerminologySyncTarget(binding.endpoint, binding.account_user_id, binding.project_id)
                configured_target = TerminologySyncTarget(
                    self._config.base_url,
                    self._config.user_id,
                    binding.project_id,
                )
                if configured_target.target_id != target.target_id:
                    diagnostics.append("当前 ParaTranz endpoint/account 与已验证绑定不一致")
                line_state = repository.sync_state.resolve_line(context.project_id, context.variant_id, target)
                if line_state.line is None:
                    diagnostics.append("尚未建立 Project/Variant 与目标的同步映射")
                elif not line_state.writable:
                    diagnostics.append(
                        "目标已映射到另一 Variant，需要确认替换同步映射"
                        if line_state.diagnostic == "variant_mapping_conflict"
                        else line_state.diagnostic or "同步存储不可写"
                    )
                elif line_state.profile is None or line_state.profile.mode is not mode:
                    diagnostics.append(f"当前同步映射未配置为 {mode.value} 模式")
        return TerminologySyncPreflight(
            mode=mode,
            available=not diagnostics,
            project_id=context.project_id,
            variant_id=context.variant_id,
            local_version_id=None if local is None else local.version_id,
            local_content_digest=None if local is None else local.content_digest,
            target=target,
            profile_id=None if line_state is None or line_state.line is None else line_state.line.line_id,
            mapping_status="ready"
            if line_state is not None and line_state.writable and line_state.line is not None
            else "unmapped",
            diagnostics=tuple(diagnostics),
        )

    def planning_request(self, context: RequestContext, mode) -> CreateTerminologySyncPlanRequest:
        preflight = self.preflight(context, mode)
        if (
            not preflight.available
            or preflight.project_id is None
            or preflight.variant_id is None
            or preflight.target is None
        ):
            raise RuntimeError("; ".join(preflight.diagnostics))
        active = self._composition.lifecycle.active if self._composition.lifecycle is not None else None
        return CreateTerminologySyncPlanRequest(
            preflight.project_id,
            preflight.variant_id,
            preflight.target,
            preflight.mode,
            None if active is None else active.persisted_project_revision,
        )

    def activate_mapping(
        self,
        context: RequestContext,
        mode,
        *,
        replace_existing: bool = False,
    ) -> TerminologySyncPreflight:
        mode = TerminologySyncMode(mode)
        if context.project_id is None or context.variant_id is None:
            raise ValueError("terminology sync mapping requires Project and Variant context")
        active = None if self._composition.lifecycle is None else self._composition.lifecycle.active
        if active is None or active.project_ref.identity.value != context.project_id:
            raise RuntimeError("the active Project changed before terminology sync mapping activation")
        from transbridge.application.projects import project_paratranz_binding

        binding = project_paratranz_binding(active.project)
        if binding is None:
            raise RuntimeError("bind the current Project to a verified ParaTranz target first")
        target = TerminologySyncTarget(binding.endpoint, binding.account_user_id, binding.project_id)
        repository = self._composition.repositories.for_project(context.project_id)
        state = repository.sync_state.resolve_line(context.project_id, context.variant_id, target)
        if state.line is not None and state.line.variant_id != context.variant_id and not replace_existing:
            raise RuntimeError("the ParaTranz target is mapped to another Variant; use explicit replacement review")
        if state.line is None or state.line.variant_id != context.variant_id:
            now = self._composition.clock.now() if self._composition.clock is not None else None
            if now is None:
                from datetime import UTC, datetime

                now = datetime.now(UTC)
            mapping_revision = 0 if state.profile is None else state.profile.mapping_revision + 1
            profile_revision = 0 if state.line is None else state.line.profile_revision + 1
            line_id = sync_line_id(
                project_id=context.project_id,
                variant_id=context.variant_id,
                target_identity=target.target_id,
                profile_revision=profile_revision,
            )
            line = TerminologySyncLine(
                line_id,
                context.project_id,
                context.variant_id,
                target,
                profile_revision,
                now.isoformat(),
            )
            profile = TerminologySyncProfile(line_id, 0, mode=mode, mapping_revision=mapping_revision)
            if state.line is None:
                repository.sync_state.activate_line(line, profile)
            else:
                repository.sync_state.replace_active_variant_mapping(
                    line,
                    profile,
                    expected_mapping_revision=state.profile.mapping_revision,
                    retired_at=now.isoformat(),
                )
        elif state.profile is not None and state.profile.mode is not mode:
            from dataclasses import replace

            repository.sync_state.update_profile(
                replace(state.profile, revision=state.profile.revision + 1, mode=mode),
                expected_revision=state.profile.revision,
            )
        return self.preflight(context, mode)


class _ProductionSyncInputs(TerminologySyncPlanningInputPort, TerminologySyncFreshInputPort):
    def __init__(self, composition: ProductionTerminologyComposition, remote: ParaTranzTermsService) -> None:
        self._composition = composition
        self._remote = remote
        self._requests: dict[str, CreateTerminologySyncPlanRequest] = {}

    def load(self, request: CreateTerminologySyncPlanRequest) -> TerminologySyncPlannerInput:
        inputs = self._load(request)
        plan_hash = TerminologySyncPlanner().plan(inputs).plan_hash
        self._requests[plan_hash] = request
        return inputs

    def load_for_plan(self, plan_hash: str) -> TerminologySyncPlannerInput:
        try:
            request = self._requests[plan_hash]
        except KeyError as exc:
            raise KeyError("unknown terminology sync plan inputs") from exc
        return self._load(request)

    def _load(self, request: CreateTerminologySyncPlanRequest) -> TerminologySyncPlannerInput:
        lifecycle = self._composition.lifecycle
        active = None if lifecycle is None else lifecycle.active
        if (
            active is None
            or active.project_ref.identity.value != request.local_project_id
            or active.persisted_project_revision != request.binding_revision
        ):
            raise TerminologySyncPlanStaleError("Project binding revision changed after terminology sync planning")
        from transbridge.application.projects import project_paratranz_binding

        binding = project_paratranz_binding(active.project)
        if binding is None:
            raise TerminologySyncPlanStaleError("ParaTranz binding was removed after terminology sync planning")
        config = ParatranzConfig.create_or_load()
        current_target = TerminologySyncTarget(config.base_url, config.user_id, binding.project_id)
        bound_target = TerminologySyncTarget(binding.endpoint, binding.account_user_id, binding.project_id)
        if current_target.target_id != request.target.target_id or bound_target.target_id != request.target.target_id:
            raise TerminologySyncPlanStaleError(
                "ParaTranz target/account/endpoint changed after terminology sync planning"
            )
        repository = self._composition.repositories.for_project(request.local_project_id)
        state = repository.sync_state
        line_state = state.resolve_line(request.local_project_id, request.local_variant_id, request.target)
        if line_state.line is None or line_state.profile is None:
            raise RuntimeError("terminology sync mapping is unavailable")
        local = SqliteEffectiveTerminologySnapshotPort(repository).snapshot(
            request.local_project_id,
            request.local_variant_id,
        )
        remote = self._remote.snapshot_terms(request.target.remote_project_id)
        links = []
        page_request = PageRequest(limit=1000)
        while True:
            page = state.list_item_links(line_state.line.line_id, page_request)
            links.extend(page.items)
            if page.next_cursor is None:
                break
            page_request = PageRequest(limit=1000, cursor=page.next_cursor)
        return TerminologySyncPlannerInput(
            line=line_state.line,
            profile=line_state.profile,
            local_snapshot=local,
            remote_snapshot=remote,
            baseline=line_state.baseline,
            item_links=tuple(links),
            binding_revision=request.binding_revision,
            variant_mapping_conflict=line_state.diagnostic == "variant_mapping_conflict",
        )


class _ProductionDraftState:
    def __init__(self, line_reader, repository) -> None:
        self._line_reader = line_reader
        self._repository = repository

    def current_line(self, project_id: str, variant_id: str):
        return self._line_reader.read_line(None, project_id, variant_id)

    def effective_decisions(self, line):
        version = self._repository.effective_version(line.project_id, line.variant_id)
        return () if version is None else version.decisions


def _production_inbound_service(repository, lifecycle, clock, ids):
    from .terminology_workloads import _LifecycleDraftLineReader, _RequestManualActor

    line_reader = _LifecycleDraftLineReader(lifecycle, repository)
    transactions = repository.draft_transactions(line_reader)
    importer = InboundDraftImportService(
        repository.inbound_reviews,
        transactions,
        _ProductionDraftState(line_reader, repository),
        _RequestManualActor(),
        clock,
        ids,
    )
    durable = DurableTerminologyInboundService(repository.inbound_reviews, importer)
    return _ProductionInboundApplication(repository.inbound_reviews, transactions, line_reader, durable)


class _ProductionInboundApplication:
    def __init__(self, store, transactions, line_reader, durable) -> None:
        self._store = store
        self._transactions = transactions
        self._line_reader = line_reader
        self._durable = durable

    def list_inbound(self, context: RequestContext):
        return self._durable.list_inbound(context)

    def get_inbound(self, context: RequestContext, change_set_id: str):
        return self._durable.get_inbound(context, change_set_id)

    def prepare_selection(
        self,
        context: RequestContext,
        change_set_id: str,
        choices: tuple[DraftImportChoice, ...],
    ) -> DraftImportSelection:
        change_set = self._durable.get_inbound(context, change_set_id)
        review = self._store.get_review_state(change_set_id)
        line = self._line_reader.read_line(None, change_set.project_id, change_set.variant_id)
        draft = self._transactions.active_draft(change_set.project_id, change_set.variant_id)
        from transbridge.application.terminology.drafts import DraftWriteExpectation

        expectation = None if draft is None else DraftWriteExpectation.from_draft(draft, line)
        return DraftImportSelection(
            change_set_id,
            change_set.content_digest,
            review.revision,
            line,
            choices,
            expectation,
        )

    def preview_import(self, selection):
        return self._durable.preview_import(selection)

    def commit_import(self, proposal, context):
        return self._durable.commit_import(proposal, context)


class _ProductionSyncBindingResolver:
    def __init__(self, lifecycle) -> None:
        self._lifecycle = lifecycle

    def resolve_target_binding(self, project_id: str) -> TerminologySyncTargetBinding | None:
        from transbridge.application.projects import project_paratranz_binding

        active = None if self._lifecycle is None else self._lifecycle.active
        if active is None or active.project_ref.identity.value != project_id:
            return None
        binding = project_paratranz_binding(active.project)
        if binding is None:
            return None
        return TerminologySyncTargetBinding(
            project_id,
            TerminologySyncTarget(binding.endpoint, binding.account_user_id, binding.project_id),
            active.persisted_project_revision,
        )


def build_production_terminology(
    *,
    root: str | Path,
    lifecycle: ProjectLifecycleService,
    task_runtime,
    ids,
    clock,
    max_unstreamed_source_count: int = 50,
    max_unstreamed_source_bytes: int = 64 * 1024 * 1024,
    max_unstreamed_total_bytes: int = 256 * 1024 * 1024,
) -> ProductionTerminologyComposition:
    repositories = ProjectTerminologyRepositories(root)
    leases = FilesystemSourceLeases(max_unstreamed_source_bytes=max_unstreamed_source_bytes)
    capabilities = FormatCapabilities(default_format_catalog())
    state = ProductionState()
    build_inputs = BuildInputCaptureService(
        LifecycleCapture(lifecycle),
        leases,
        capabilities,
        RepositoryBaselines(repositories),
        max_unstreamed_source_count=max_unstreamed_source_count,
        max_unstreamed_source_bytes=max_unstreamed_source_bytes,
        max_unstreamed_total_bytes=max_unstreamed_total_bytes,
    )
    commit_port = ProductionTerminologyCommitPort(lifecycle, repositories, state, leases)
    registry = TerminologyWorkloadRegistry()
    registry.bind(TerminologyWorkloadType.BUILD, BuildRunner(repositories, state, capabilities.catalog, ids))
    registry.bind(TerminologyWorkloadType.HISTORY_COMPARE, HistoryCompareRunner(repositories, state))
    registry.bind(TerminologyWorkloadType.PUBLISH, PublishRunner(repositories, state))
    registry.bind(TerminologyWorkloadType.REPORT_RENDER, ReportRunner(repositories, state))
    registry.bind(TerminologyWorkloadType.CHANGELOG_RENDER, ChangelogRunner(repositories, state))
    tasks = TerminologyTaskEntrypoint(task_runtime, registry, commit_port)
    commands = ProductionTerminologyCommands(
        tasks,
        repositories,
        repositories.paths,
        state,
        ids,
        clock,
        build_inputs,
        lifecycle,
    )
    return ProductionTerminologyComposition(
        repositories,
        build_inputs,
        registry,
        tasks,
        commands,
        commit_port,
        lifecycle,
        ids=ids,
        clock=clock,
    )


__all__ = [
    "ProductionTerminologyComposition",
    "ProductionTerminologyCommands",
    "ProjectTerminologyRepositories",
    "build_production_terminology",
]
