from __future__ import annotations

from datetime import UTC, datetime

from transbridge.ai_translator.term_formats import TermEntry
from transbridge.application.ports.paratranz_terms import ParaTranzTerm, ParaTranzTermSnapshot
from transbridge.application.terminology.effective import (
    EffectiveSnapshotStatus,
    EffectiveTerminologySnapshot,
)
from transbridge.application.terminology.models import DecisionStatus, TermDecision, TermScope
from transbridge.application.terminology_sync.identity import sync_item_id, sync_line_id
from transbridge.application.terminology_sync.mapping import local_content
from transbridge.application.terminology_sync.models import (
    TerminologySyncBaseline,
    TerminologySyncItemLink,
    TerminologySyncLine,
    TerminologySyncMode,
    TerminologySyncOutcome,
    TerminologySyncOwnership,
    TerminologySyncProfile,
    TerminologySyncTarget,
    TerminologySyncTombstone,
)
from transbridge.application.terminology_sync.plan_models import (
    TerminologySyncAction,
    TerminologySyncReason,
)
from transbridge.application.terminology_sync.planner import (
    TerminologySyncPlanner,
    TerminologySyncPlannerInput,
)
from transbridge.application.terminology_sync.use_case import (
    AuthorizeTerminologySyncPlanRequest,
    CreateTerminologySyncPlanRequest,
    TerminologySyncPlanAuthorizationError,
    TerminologySyncPlanningUseCase,
    TerminologySyncPlanStaleError,
)


class _InputsPort:
    def __init__(self, value: TerminologySyncPlannerInput) -> None:
        self.value = value

    def load(self, request: CreateTerminologySyncPlanRequest) -> TerminologySyncPlannerInput:
        return self.value


def _decision(term_id: str, original: str, translation: str, *, plugin: str | None = None) -> TermDecision:
    return TermDecision(
        term_id=term_id,
        project_id="project-1",
        variant_id="variant-1",
        original=original,
        normalized_original=original.casefold(),
        translation=translation,
        scope=TermScope.plugin(plugin) if plugin else TermScope.project(),
        status=DecisionStatus.ADOPTED,
    )


def _remote(remote_id: int, original: str, translation: str) -> ParaTranzTerm:
    return ParaTranzTerm(
        remote_id=remote_id,
        entry=TermEntry(original, translation, "paratranz"),
        server_revision=None,
        observed_digest=f"{remote_id:064x}",
    )


def _inputs(
    *,
    decisions: tuple[TermDecision, ...],
    remote: tuple[ParaTranzTerm, ...],
    mode: TerminologySyncMode = TerminologySyncMode.BACKUP,
    stable: bool = True,
    baseline: TerminologySyncBaseline | None = None,
    links: tuple[TerminologySyncItemLink, ...] = (),
) -> TerminologySyncPlannerInput:
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
    return TerminologySyncPlannerInput(
        line=line,
        profile=TerminologySyncProfile(line_id, 2, mode=mode),
        local_snapshot=EffectiveTerminologySnapshot(
            "project-1",
            "variant-1",
            EffectiveSnapshotStatus.READY,
            version_id="version-1",
            content_digest="local-version-digest",
            decisions=decisions,
        ),
        remote_snapshot=ParaTranzTermSnapshot(
            123,
            remote,
            "a" * 64,
            datetime(2026, 8, 30, tzinfo=UTC),
            stable,
        ),
        baseline=baseline,
        item_links=links,
        binding_revision=4,
    )


def test_backup_is_deterministic_and_preserves_independent_remote_terms() -> None:
    local = _decision("local-1", "Sword", "剑")
    independent = _remote(20, "Shield", "盾")
    planner = TerminologySyncPlanner()

    first = planner.plan(_inputs(decisions=(local,), remote=(independent,)))
    second = planner.plan(_inputs(decisions=(local,), remote=(independent,)))

    assert first.plan_hash == second.plan_hash
    assert [(item.action, item.reason) for item in first.items] == [
        (TerminologySyncAction.SKIP, TerminologySyncReason.INDEPENDENT_REMOTE),
        (TerminologySyncAction.CREATE_REMOTE, TerminologySyncReason.LOCAL_ONLY),
    ]


def test_bidirectional_remote_change_becomes_inbound_proposal() -> None:
    local = _decision("local-1", "Sword", "剑")
    base_digest = local_content(local).digest
    changed_remote = _remote(20, "Sword", "长剑")
    inputs = _inputs(
        decisions=(local,),
        remote=(changed_remote,),
        mode=TerminologySyncMode.BIDIRECTIONAL,
    )
    baseline = TerminologySyncBaseline(
        inputs.line.line_id,
        3,
        "version-0",
        "local-0",
        "remote-0",
        "common-0",
        "run-0",
    )
    link = TerminologySyncItemLink(
        line_id=inputs.line.line_id,
        item_id=sync_item_id(line_id=inputs.line.line_id, local_term_id=local.term_id),
        revision=1,
        local_term_id=local.term_id,
        local_version_id="version-0",
        local_content_digest="local-0",
        remote_id=20,
        remote_revision=None,
        remote_observed_digest=changed_remote.observed_digest,
        common_content_digest=base_digest,
        scope="project",
        ownership=TerminologySyncOwnership.MANAGED,
        last_outcome=TerminologySyncOutcome.CONFIRMED,
    )
    plan = TerminologySyncPlanner().plan(
        _inputs(
            decisions=(local,),
            remote=(changed_remote,),
            mode=TerminologySyncMode.BIDIRECTIONAL,
            baseline=baseline,
            links=(link,),
        )
    )

    assert len(plan.items) == 1
    assert plan.items[0].action is TerminologySyncAction.PROPOSE_LOCAL_UPDATE
    assert plan.items[0].reason is TerminologySyncReason.REMOTE_CHANGED
    assert plan.requires_confirmation


def test_bidirectional_remote_delete_conflicts_with_concurrent_local_change() -> None:
    base = _decision("local-1", "Sword", "剑")
    current = _decision("local-1", "Sword", "长剑")
    inputs = _inputs(decisions=(current,), remote=(), mode=TerminologySyncMode.BIDIRECTIONAL)
    baseline = TerminologySyncBaseline(
        inputs.line.line_id,
        3,
        "version-0",
        "local-0",
        "remote-0",
        "common-0",
        "run-0",
    )
    link = TerminologySyncItemLink(
        inputs.line.line_id,
        sync_item_id(line_id=inputs.line.line_id, local_term_id=current.term_id),
        1,
        current.term_id,
        "version-0",
        "local-0",
        20,
        None,
        "2" * 64,
        local_content(base).digest,
        "project",
        TerminologySyncOwnership.MANAGED,
        last_outcome=TerminologySyncOutcome.CONFIRMED,
    )

    plan = TerminologySyncPlanner().plan(
        _inputs(
            decisions=(current,),
            remote=(),
            mode=TerminologySyncMode.BIDIRECTIONAL,
            baseline=baseline,
            links=(link,),
        )
    )

    assert plan.items[0].action is TerminologySyncAction.CONFLICT
    assert plan.items[0].reason is TerminologySyncReason.BOTH_CHANGED


def test_confirmed_link_without_remote_id_is_blocked_as_missing_identity() -> None:
    local = _decision("local-1", "Sword", "剑")
    inputs = _inputs(decisions=(local,), remote=())
    link = TerminologySyncItemLink(
        inputs.line.line_id,
        sync_item_id(line_id=inputs.line.line_id, local_term_id=local.term_id),
        1,
        local.term_id,
        "version-0",
        "local-0",
        None,
        None,
        None,
        local_content(local).digest,
        "project",
        TerminologySyncOwnership.MANAGED,
        last_outcome=TerminologySyncOutcome.CONFIRMED,
    )

    plan = TerminologySyncPlanner().plan(_inputs(decisions=(local,), remote=(), links=(link,)))

    assert plan.blocked
    assert plan.items[0].action is TerminologySyncAction.BLOCKED
    assert plan.items[0].reason is TerminologySyncReason.REMOTE_ID_MISSING


def test_linked_remote_id_with_another_term_identity_is_blocked_as_reused() -> None:
    local = _decision("local-1", "Sword", "剑")
    reused = _remote(20, "Shield", "盾")
    inputs = _inputs(decisions=(local,), remote=(reused,))
    link = TerminologySyncItemLink(
        inputs.line.line_id,
        sync_item_id(line_id=inputs.line.line_id, local_term_id=local.term_id),
        1,
        local.term_id,
        "version-0",
        "local-0",
        reused.remote_id,
        None,
        reused.observed_digest,
        local_content(local).digest,
        "project",
        TerminologySyncOwnership.MANAGED,
        last_outcome=TerminologySyncOutcome.CONFIRMED,
    )

    plan = TerminologySyncPlanner().plan(_inputs(decisions=(local,), remote=(reused,), links=(link,)))

    assert plan.blocked
    assert plan.items[0].action is TerminologySyncAction.BLOCKED
    assert plan.items[0].reason is TerminologySyncReason.REMOTE_ID_REUSED


def test_tombstoned_remote_id_reappearing_is_blocked_even_with_matching_content() -> None:
    local = _decision("local-1", "Sword", "剑")
    revived = _remote(20, "Sword", "剑")
    inputs = _inputs(decisions=(local,), remote=(revived,))
    link = TerminologySyncItemLink(
        inputs.line.line_id,
        sync_item_id(line_id=inputs.line.line_id, local_term_id=local.term_id),
        2,
        local.term_id,
        "version-0",
        "local-0",
        revived.remote_id,
        None,
        revived.observed_digest,
        local_content(local).digest,
        "project",
        TerminologySyncOwnership.MANAGED,
        tombstone=TerminologySyncTombstone.BOTH_DELETED,
        last_outcome=TerminologySyncOutcome.CONFIRMED,
    )

    plan = TerminologySyncPlanner().plan(_inputs(decisions=(local,), remote=(revived,), links=(link,)))

    assert plan.blocked
    assert plan.items[0].reason is TerminologySyncReason.REMOTE_ID_REUSED


def test_plugin_scope_is_visible_but_never_executable() -> None:
    plan = TerminologySyncPlanner().plan(
        _inputs(decisions=(_decision("plugin-1", "Sword", "剑", plugin="Skyrim.esm"),), remote=())
    )

    assert plan.items[0].action is TerminologySyncAction.LOSSY_MAPPING
    assert plan.items[0].reason is TerminologySyncReason.PLUGIN_SCOPE
    assert not plan.items[0].action.executable_remote


def test_unstable_remote_snapshot_blocks_planning() -> None:
    plan = TerminologySyncPlanner().plan(
        _inputs(decisions=(_decision("local-1", "Sword", "剑"),), remote=(), stable=False)
    )

    assert plan.blocked
    assert plan.items[0].reason is TerminologySyncReason.REMOTE_SNAPSHOT_UNSTABLE


def test_absent_baseline_and_revision_zero_have_distinct_plan_hashes() -> None:
    local = _decision("local-1", "Sword", "剑")
    first_inputs = _inputs(decisions=(local,), remote=())
    first = TerminologySyncPlanner().plan(first_inputs)
    committed = TerminologySyncBaseline(
        first_inputs.line.line_id,
        0,
        "version-0",
        "local-0",
        "remote-0",
        "common-0",
        "run-0",
    )
    second = TerminologySyncPlanner().plan(_inputs(decisions=(local,), remote=(), baseline=committed))

    assert first.baseline_revision is None
    assert second.baseline_revision == 0
    assert first.plan_hash != second.plan_hash


def test_authorization_replans_and_rejects_stale_remote_snapshot() -> None:
    initial = _inputs(decisions=(_decision("local-1", "Sword", "剑"),), remote=())
    port = _InputsPort(initial)
    use_case = TerminologySyncPlanningUseCase(port)
    context = CreateTerminologySyncPlanRequest(
        "project-1",
        "variant-1",
        initial.line.target,
        TerminologySyncMode.BACKUP,
        binding_revision=4,
    )
    plan = use_case.create_plan(context)
    port.value = _inputs(
        decisions=(_decision("local-1", "Sword", "剑"),),
        remote=(_remote(20, "Shield", "盾"),),
    )

    try:
        use_case.authorize(AuthorizeTerminologySyncPlanRequest(plan, "owner-1", context))
    except TerminologySyncPlanStaleError:
        pass
    else:
        raise AssertionError("changed remote snapshot must invalidate the plan")


def test_confirmation_is_owner_bound_and_one_use() -> None:
    initial = _inputs(
        decisions=(_decision("plugin-1", "Sword", "剑", plugin="Skyrim.esm"),),
        remote=(),
    )
    port = _InputsPort(initial)
    use_case = TerminologySyncPlanningUseCase(port)
    context = CreateTerminologySyncPlanRequest(
        "project-1",
        "variant-1",
        initial.line.target,
        TerminologySyncMode.BACKUP,
        binding_revision=4,
    )
    plan = use_case.create_plan(context)
    token = use_case.issue_confirmation(plan, owner_id="owner-1")

    authorized = use_case.authorize(AuthorizeTerminologySyncPlanRequest(plan, "owner-1", context, token))

    assert authorized.owner_id == "owner-1"
    try:
        use_case.authorize(AuthorizeTerminologySyncPlanRequest(plan, "owner-1", context, token))
    except TerminologySyncPlanAuthorizationError as exc:
        assert exc.code == "CONFIRMATION_REPLAYED"
    else:
        raise AssertionError("confirmation tokens must be one-use")
