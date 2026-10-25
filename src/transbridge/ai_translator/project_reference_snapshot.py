"""Credential-free serialization of a task's published terminology binding."""

from __future__ import annotations

from dataclasses import asdict

from transbridge.application.terminology.models import TermDecision, TermScope
from transbridge.application.translation.terminology_run_snapshot import (
    FrozenEffectiveTerminologyPort,
    FrozenTerminologyRunSnapshot,
    TerminologyRunSnapshotRef,
)

from .legacy_term_policy import (
    ConfirmedTerminologyEchoLink,
    FrozenTerminologyEchoLinks,
    ProjectTerminologyEchoFilter,
)
from .project_terminology_adapter import EnabledEffectiveTerminologyGate, ProjectTerminologyAdapter
from .project_terminology_runtime import ProjectTerminologyBinding


def capture_project_reference(binding):
    frozen = getattr(binding, "run_snapshot", None)
    if frozen is None:
        return {"snapshot": None}
    frozen.verify()
    policy = getattr(binding, "legacy_term_filter", None)
    links = getattr(policy, "_echo_links", None)
    return {
        "snapshot": {"ref": frozen.ref.to_dict(), "decisions": [asdict(item) for item in frozen.decisions]},
        "echo_links": None if links is None else asdict(links),
    }


def restore_project_reference(value):
    snapshot = value["snapshot"]
    if snapshot is None:
        return ProjectTerminologyBinding()
    ref = TerminologyRunSnapshotRef.from_dict(snapshot["ref"])
    decisions = tuple(
        TermDecision(**(dict(row) | {"scope": TermScope(**row["scope"])})) for row in snapshot["decisions"]
    )
    frozen = FrozenTerminologyRunSnapshot(ref, decisions)
    adapter = ProjectTerminologyAdapter(FrozenEffectiveTerminologyPort(frozen), EnabledEffectiveTerminologyGate())
    from transbridge.application.terminology.effective import TerminologyLookupContext

    context = TerminologyLookupContext(ref.local_project_id, ref.local_variant_id, version_id=ref.version_id)
    policy = None
    if (links := value.get("echo_links")) is not None:
        links = dict(links) | {"links": tuple(ConfirmedTerminologyEchoLink(**row) for row in links["links"])}
        policy = ProjectTerminologyEchoFilter(frozen, FrozenTerminologyEchoLinks(**links))
    return ProjectTerminologyBinding(adapter, context, frozen, policy)
