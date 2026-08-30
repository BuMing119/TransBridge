"""Seeded builders for FR5.17 controlled integration scenarios."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
import hashlib
import random

from transbridge.ai_translator.term_formats import TermEntry
from transbridge.application.ports.paratranz_terms import ParaTranzTerm, ParaTranzTermSnapshot
from transbridge.application.terminology.effective import EffectiveSnapshotStatus, EffectiveTerminologySnapshot
from transbridge.application.terminology.identity import normalize_original, term_id
from transbridge.application.terminology.models import DecisionStatus, TermDecision, TermScope
from transbridge.application.terminology_sync.identity import sync_line_id
from transbridge.application.terminology_sync.models import (
    TerminologySyncBaseline,
    TerminologySyncItemLink,
    TerminologySyncLine,
    TerminologySyncMode,
    TerminologySyncProfile,
    TerminologySyncTarget,
)
from transbridge.application.terminology_sync.planner import TerminologySyncPlannerInput

FIXED_TIME = datetime(2026, 8, 30, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class TerminologySyncScenario:
    scenario_id: str
    seed: int
    line: TerminologySyncLine
    profile: TerminologySyncProfile
    local_snapshot: EffectiveTerminologySnapshot
    remote_snapshot: ParaTranzTermSnapshot
    baseline: TerminologySyncBaseline | None = None
    item_links: tuple[TerminologySyncItemLink, ...] = ()
    binding_revision: int = 1

    def planner_input(self) -> TerminologySyncPlannerInput:
        return TerminologySyncPlannerInput(
            self.line,
            self.profile,
            self.local_snapshot,
            self.remote_snapshot,
            self.baseline,
            self.item_links,
            self.binding_revision,
        )

    def with_remote(
        self,
        snapshot: ParaTranzTermSnapshot,
        *,
        baseline: TerminologySyncBaseline | None = None,
        item_links: tuple[TerminologySyncItemLink, ...] | None = None,
    ) -> TerminologySyncScenario:
        return replace(
            self,
            remote_snapshot=snapshot,
            baseline=self.baseline if baseline is None else baseline,
            item_links=self.item_links if item_links is None else item_links,
        )


class TerminologySyncScenarioBuilder:
    def __init__(
        self,
        seed: int = 51708,
        *,
        project_id: str = "project-1",
        variant_id: str = "variant-1",
        remote_project_id: int = 123,
        endpoint: str = "https://controlled.invalid/api",
    ) -> None:
        self.seed = seed
        self.project_id = project_id
        self.variant_id = variant_id
        self.remote_project_id = remote_project_id
        self.endpoint = endpoint
        self._random = random.Random(seed)

    def backup(
        self,
        *,
        project_terms: int = 1,
        plugin_terms: int = 0,
        remote_independent: int = 0,
        mode: TerminologySyncMode = TerminologySyncMode.BACKUP,
        profile_revision: int = 1,
    ) -> TerminologySyncScenario:
        target = TerminologySyncTarget(self.endpoint, 7, self.remote_project_id)
        line_id = sync_line_id(
            project_id=self.project_id,
            variant_id=self.variant_id,
            target_identity=target.target_id,
            profile_revision=profile_revision,
        )
        line = TerminologySyncLine(
            line_id,
            self.project_id,
            self.variant_id,
            target,
            profile_revision,
            FIXED_TIME.isoformat(),
        )
        profile = TerminologySyncProfile(line_id, profile_revision, mode=mode)
        decisions = tuple(
            [self.decision(index) for index in range(project_terms)]
            + [self.decision(project_terms + index, plugin_id=f"Plugin-{index}.esp") for index in range(plugin_terms)]
        )
        remote = tuple(self.remote_term(10_000 + index, independent=True) for index in range(remote_independent))
        local_digest = _digest("local", self.seed, *(decision.term_id for decision in decisions))
        remote_digest = _digest("remote", self.seed, *(item.observed_digest for item in remote))
        return TerminologySyncScenario(
            f"backup-{self.seed}",
            self.seed,
            line,
            profile,
            EffectiveTerminologySnapshot(
                self.project_id,
                self.variant_id,
                EffectiveSnapshotStatus.READY,
                f"version-{self.seed}",
                local_digest,
                decisions,
            ),
            ParaTranzTermSnapshot(
                self.remote_project_id,
                remote,
                remote_digest,
                FIXED_TIME,
                True,
            ),
        )

    def decision(self, index: int, *, plugin_id: str | None = None, suppressed: bool = False) -> TermDecision:
        original = f"Term-{self.seed}-{index:04d}"
        translation = f"译文-{self._random.randrange(1_000_000):06d}"
        scope = TermScope.project() if plugin_id is None else TermScope.plugin(plugin_id)
        return TermDecision(
            term_id(
                project_id=self.project_id,
                variant_id=self.variant_id,
                scope=scope,
                original=original,
            ),
            self.project_id,
            self.variant_id,
            original,
            normalize_original(original),
            translation,
            scope=scope,
            status=DecisionStatus.ADOPTED,
            suppressed=suppressed,
        )

    def remote_term(self, remote_id: int, *, independent: bool = False) -> ParaTranzTerm:
        suffix = f"independent-{remote_id}" if independent else str(remote_id)
        return ParaTranzTerm(
            remote_id,
            TermEntry(f"Remote-{suffix}", f"远端-{suffix}", "paratranz"),
            f"term-{remote_id}",
            _digest("remote-term", remote_id, suffix),
        )

    @staticmethod
    def server_records(snapshot: ParaTranzTermSnapshot) -> tuple[dict[str, object], ...]:
        return tuple(
            {
                "id": item.remote_id,
                "term": item.entry.term,
                "translation": item.entry.translation,
                "variants": list(item.entry.variants),
                "caseSensitive": item.entry.case_sensitive,
                "pos": item.entry.pos,
                "note": item.entry.note,
                "revision": item.server_revision,
            }
            for item in snapshot.items
        )


def _digest(*parts: object) -> str:
    value = "\0".join(str(part) for part in parts)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = ["FIXED_TIME", "TerminologySyncScenario", "TerminologySyncScenarioBuilder"]
