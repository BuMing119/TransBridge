from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from tests.persistence.terminology.test_effective_snapshot import _publish
from transbridge.ai_translator.project_terminology_runtime import freeze_project_terminology
from transbridge.ai_translator.term_formats import TermEntry
from transbridge.application.terminology.effective import EffectiveSnapshotStatus, EffectiveTerminologySnapshot
from transbridge.application.terminology.identity import normalize_original, term_id
from transbridge.application.terminology.models import DecisionStatus, TermDecision, TermScope
from transbridge.application.terminology_profiles import (
    ProfileTermMapping,
    TerminologyProfileContent,
    is_profiled_version_id,
)
from transbridge.application.terminology_sync.identity import sync_item_id, sync_line_id
from transbridge.application.terminology_sync.mapping import local_content
from transbridge.application.terminology_sync.models import (
    TerminologySyncBaseline,
    TerminologySyncCommit,
    TerminologySyncItemLink,
    TerminologySyncItemLinkUpdate,
    TerminologySyncLine,
    TerminologySyncOutcome,
    TerminologySyncOwnership,
    TerminologySyncProfile,
    TerminologySyncRunOutcome,
    TerminologySyncRunRecord,
    TerminologySyncTarget,
)
from transbridge.bootstrap.terminology import ProductionTerminologyComposition
from transbridge.persistence.terminology import SqliteEffectiveTerminologySnapshotPort, SqliteTerminologyRepository
from transbridge.ui.tools.ai_translator.run_controller import RunController


def _decision(translation: str) -> TermDecision:
    scope = TermScope.project()
    return TermDecision(
        term_id(project_id="project-1", variant_id="variant-1", scope=scope, original="Sword"),
        "project-1",
        "variant-1",
        "Sword",
        normalize_original("Sword"),
        translation,
        scope=scope,
        status=DecisionStatus.ADOPTED,
    )


def _snapshot(version: str, digest: str, translation: str) -> EffectiveTerminologySnapshot:
    return EffectiveTerminologySnapshot(
        "project-1",
        "variant-1",
        EffectiveSnapshotStatus.READY,
        version,
        digest,
        (_decision(translation),),
    )


@dataclass
class _MutableSnapshotSource:
    current: EffectiveTerminologySnapshot
    reads: int = 0

    def snapshot(self, _project_id: str, _variant_id: str, _version_id: str | None = None):
        self.reads += 1
        return self.current


class _Factory:
    def __init__(self, source: _MutableSnapshotSource) -> None:
        self.source = source

    def effective_adapter(self, _project_id: str, _variant_id: str):
        from transbridge.ai_translator.project_terminology_adapter import (
            EnabledEffectiveTerminologyGate,
            ProjectTerminologyAdapter,
        )

        return ProjectTerminologyAdapter(self.source, EnabledEffectiveTerminologyGate())


@pytest.mark.parametrize(
    ("entrypoint", "run_mode"),
    [
        ("translate", "translate"),
        ("polish", "polish"),
        ("mixed", "mixed"),
        # Custom profiles deliberately reuse their selected base-mode runner.
        ("custom-profile", "translate"),
    ],
)
@pytest.mark.parametrize("transition", ["publish", "restore", "sync", "variant-switch"])
def test_all_ai_entrypoints_keep_one_frozen_snapshot_across_mid_run_transition(
    entrypoint: str,
    run_mode: str,
    transition: str,
) -> None:
    initial = _snapshot("version-1", "digest-1", "旧剑")
    changed = _snapshot(f"version-{transition}", f"digest-{transition}", "新剑")
    source = _MutableSnapshotSource(initial)
    owner = SimpleNamespace(
        active_version_identity=("project-1", "variant-1"),
        effective_terminology_factory=_Factory(source),
    )
    controller = RunController(owner_id=f"{entrypoint}-{transition}")
    request = controller.begin(
        run_mode,
        SimpleNamespace(max_concurrent=1),
        [SimpleNamespace(id="entry-1")],
        esp_path="plugin.esp",
        terminology_owner=owner,
    )
    barrier = threading.Barrier(2)
    observed: list[tuple[str, str, str]] = []

    def run_batches() -> None:
        binding = request.terminology_binding
        first = binding.adapter.load(binding.context, ())
        barrier.wait(timeout=2)
        barrier.wait(timeout=2)
        second = binding.adapter.load(binding.context, ())
        observed.append((first.snapshot_identity, second.snapshot_identity, second.entries[0].translation))

    worker = threading.Thread(target=run_batches)
    worker.start()
    barrier.wait(timeout=2)
    source.current = changed
    if transition == "variant-switch":
        owner.active_version_identity = ("project-1", "variant-2")
    barrier.wait(timeout=2)
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert request.spec.terminology_snapshot is request.terminology_binding.snapshot_ref
    assert request.spec.terminology_snapshot.version_id == "version-1"
    assert request.spec.terminology_snapshot.content_digest == "digest-1"
    expected_scope_identity = f"{request.spec.terminology_snapshot.snapshot_identity}:project"
    assert observed == [(expected_scope_identity, expected_scope_identity, "旧剑")]
    assert source.reads == 1

    controller.finish(request.run_id)
    owner.active_version_identity = ("project-1", "variant-1")
    next_request = controller.begin(
        run_mode,
        SimpleNamespace(max_concurrent=1),
        [SimpleNamespace(id="entry-2")],
        esp_path="plugin.esp",
        terminology_owner=owner,
    )
    assert next_request.spec.terminology_snapshot.version_id == f"version-{transition}"
    assert next_request.spec.terminology_snapshot.content_digest == f"digest-{transition}"
    assert source.reads == 2


class _Repositories:
    def __init__(self, repository: SqliteTerminologyRepository) -> None:
        self.repository = repository

    def for_project(self, project_id: str) -> SqliteTerminologyRepository:
        assert project_id == "project-1"
        return self.repository


class _ProductionEffectiveSeam:
    effective_adapter = ProductionTerminologyComposition.effective_adapter
    freeze_echo_links = ProductionTerminologyComposition.freeze_echo_links
    profile_service_for = ProductionTerminologyComposition.profile_service_for

    def __init__(self, repository: SqliteTerminologyRepository) -> None:
        self.repositories = _Repositories(repository)
        self._profile_services = {}
        self._sync_lock = threading.RLock()
        identity = SimpleNamespace(value="project-1")
        self.lifecycle = SimpleNamespace(
            active=SimpleNamespace(project_ref=SimpleNamespace(identity=identity), project=object())
        )


def test_production_sqlite_composition_filters_exact_echo_and_preserves_independent_remote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    try:
        _publish(repository)
        snapshot = SqliteEffectiveTerminologySnapshotPort(repository).snapshot("project-1", "variant-1")
        decision = snapshot.decisions[0]
        target = TerminologySyncTarget("https://paratranz.cn", 7, 41)
        line_id = sync_line_id(
            project_id="project-1",
            variant_id="variant-1",
            target_identity=target.target_id,
            profile_revision=1,
        )
        line = TerminologySyncLine(line_id, "project-1", "variant-1", target, 1, "2026-08-30T00:00:00+00:00")
        repository.sync_state.activate_line(line, TerminologySyncProfile(line_id, 1))
        run = TerminologySyncRunRecord(
            "run-echo",
            line_id,
            "plan-echo",
            "owner-1",
            target.target_id,
            None,
            TerminologySyncRunOutcome.SUCCEEDED,
            "2026-08-30T00:00:00+00:00",
            "2026-08-30T00:00:01+00:00",
        )
        item_digest = local_content(decision).digest
        link = TerminologySyncItemLink(
            line_id,
            sync_item_id(line_id=line_id, local_term_id=decision.term_id),
            0,
            decision.term_id,
            snapshot.version_id,
            item_digest,
            10,
            "remote-revision-1",
            "a" * 64,
            item_digest,
            decision.scope.canonical_key,
            TerminologySyncOwnership.MANAGED,
            last_outcome=TerminologySyncOutcome.CONFIRMED,
        )
        baseline = TerminologySyncBaseline(
            line_id,
            0,
            snapshot.version_id,
            snapshot.content_digest,
            "b" * 64,
            "c" * 64,
            run.run_id,
        )
        repository.sync_state.commit_run(
            TerminologySyncCommit(run, (), baseline, (TerminologySyncItemLinkUpdate(link, None),)),
            expected_baseline_revision=None,
        )
        remote_binding = SimpleNamespace(endpoint=target.endpoint, account_user_id=7, project_id=41)
        monkeypatch.setattr(
            "transbridge.application.projects.project_paratranz_binding",
            lambda _project: remote_binding,
        )
        composition = _ProductionEffectiveSeam(repository)
        frozen = freeze_project_terminology(
            SimpleNamespace(
                active_version_identity=("project-1", "variant-1"),
                effective_terminology_factory=composition,
            )
        )
        echo = TermEntry(decision.original, decision.translation, "paratranz", external_id=10)
        independent = TermEntry("Shield", "盾", "paratranz", external_id=11)

        assert frozen.legacy_term_filter is not None
        assert frozen.legacy_term_filter.filter_entries("paratranz", (echo, independent)) == (independent,)
        assert frozen.legacy_term_filter.filter_entries("json", (echo, independent)) == (echo, independent)
        assert frozen.snapshot_ref.version_id == "version-1"
        assert frozen.snapshot_ref.content_digest == snapshot.content_digest
    finally:
        repository.close()


def test_production_profile_selection_overlays_ai_terms_and_disables_paratranz_legacy_source(tmp_path: Path) -> None:
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    try:
        _publish(repository)
        composition = _ProductionEffectiveSeam(repository)
        profiles = composition.profile_service_for("project-1")
        profile = profiles.create("project-1", "本体汉化 A")
        draft = profiles.save_draft(
            profile.profile_id,
            TerminologyProfileContent(mappings=(ProfileTermMapping("Dragon", "巨龙", "龙"),)),
            expected_revision=profile.draft_revision,
        )
        profiles.publish(profile.profile_id, expected_draft_revision=draft.draft_revision)
        profiles.select("project-1", "variant-1", profile.profile_id)

        frozen = freeze_project_terminology(
            SimpleNamespace(
                active_version_identity=("project-1", "variant-1"),
                effective_terminology_factory=composition,
            )
        )
        loaded = frozen.adapter.load(frozen.context, ())

        assert composition.profile_service_for("project-1") is profiles
        assert is_profiled_version_id(frozen.snapshot_ref.version_id)
        assert [(entry.term, entry.translation) for entry in loaded.entries] == [("Dragon", "巨龙")]
        assert frozen.legacy_term_filter is not None
        assert "explicit profile-to-remote mapping" in frozen.legacy_term_filter.diagnostic
        assert (
            frozen.legacy_term_filter.filter_entries(
                "paratranz",
                (TermEntry("Independent", "独立远端术语", "paratranz", external_id=99),),
            )
            == ()
        )
    finally:
        repository.close()
