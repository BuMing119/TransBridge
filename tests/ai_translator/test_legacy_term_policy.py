from __future__ import annotations

from transbridge.ai_translator.legacy_term_policy import (
    ConfirmedTerminologyEchoLink,
    FrozenTerminologyEchoLinks,
    ProjectTerminologyEchoFilter,
)
from transbridge.ai_translator.term_formats import TermEntry
from transbridge.application.terminology.effective import EffectiveSnapshotStatus, EffectiveTerminologySnapshot
from transbridge.application.terminology.identity import normalize_original, term_id
from transbridge.application.terminology.models import DecisionStatus, TermDecision, TermScope
from transbridge.application.translation.terminology_run_snapshot import TerminologyRunSnapshotFactory


def _frozen():
    scope = TermScope.project()
    decision = TermDecision(
        term_id(project_id="project-1", variant_id="variant-1", scope=scope, original="Sword"),
        "project-1",
        "variant-1",
        "Sword",
        normalize_original("Sword"),
        "剑",
        scope=scope,
        status=DecisionStatus.ADOPTED,
    )
    snapshot = EffectiveTerminologySnapshot(
        "project-1",
        "variant-1",
        EffectiveSnapshotStatus.READY,
        version_id="version-1",
        content_digest="digest-1",
        decisions=(decision,),
    )
    source = type("Source", (), {"snapshot": lambda _self, *_args: snapshot})()
    return TerminologyRunSnapshotFactory(source).freeze("project-1", "variant-1"), decision


def _links(decision: TermDecision, **updates):
    values = {
        "local_project_id": "project-1",
        "local_variant_id": "variant-1",
        "remote_target_id": "remote-project-7",
        "remote_term_id": "remote-10",
        "local_term_id": decision.term_id,
        "local_version_id": "version-1",
        "local_content_digest": "digest-1",
        "original": "Sword",
        "translation": "剑",
    }
    values.update(updates)
    return FrozenTerminologyEchoLinks(
        "project-1",
        "variant-1",
        "remote-project-7",
        "baseline-3",
        (ConfirmedTerminologyEchoLink(**values),),
    )


def test_filter_removes_only_exact_confirmed_paratranz_echo() -> None:
    frozen, decision = _frozen()
    policy = ProjectTerminologyEchoFilter(frozen, _links(decision))
    echo = TermEntry("Sword", "剑", "paratranz", external_id="remote-10")
    independent = TermEntry("Shield", "盾", "paratranz", external_id="remote-11")

    assert policy.filter_entries("paratranz", (echo, independent)) == (independent,)
    assert policy.filter_entries("json", (echo,)) == (echo,)


def test_unknown_or_version_mismatched_link_does_not_remove_remote_fallback() -> None:
    frozen, decision = _frozen()
    entry = TermEntry("Sword", "剑", "paratranz", external_id="remote-10")
    unknown = _links(decision, outcome="unknown")
    stale = _links(decision, local_content_digest="old-digest")

    assert ProjectTerminologyEchoFilter(frozen, unknown).filter_entries("paratranz", (entry,)) == (entry,)
    assert ProjectTerminologyEchoFilter(frozen, stale).filter_entries("paratranz", (entry,)) == (entry,)


def test_unavailable_baseline_fails_closed_for_remote_terms_only() -> None:
    frozen, _decision = _frozen()
    links = FrozenTerminologyEchoLinks(
        "project-1",
        "variant-1",
        "remote-project-7",
        "baseline-unavailable",
        available=False,
        diagnostic="baseline snapshot unavailable",
    )
    policy = ProjectTerminologyEchoFilter(frozen, links)
    entry = TermEntry("Remote only", "远端", "paratranz", external_id="remote-99")

    assert policy.filter_entries("paratranz", (entry,)) == ()
    assert policy.filter_entries("csv", (entry,)) == (entry,)
    assert policy.diagnostic == "baseline snapshot unavailable"
