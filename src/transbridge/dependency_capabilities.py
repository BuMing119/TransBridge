"""Release dependency capability probes.

Imports are intentionally delayed until the feature is used.  A missing
optional distribution therefore becomes an explicit capability state instead
of making ``import transbridge`` fail.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.util import find_spec

from transbridge.application.capabilities import CapabilityId, CapabilityReport, CapabilityState


@dataclass(frozen=True, slots=True)
class DependencyCapability:
    feature: str
    distribution: str
    import_name: str
    declared: bool = True
    locked: bool = True
    bundled: bool = True
    optional_at_runtime: bool = True


DEPENDENCY_BASELINE: tuple[DependencyCapability, ...] = (
    DependencyCapability("hybrid-term-retrieval", "rank-bm25", "rank_bm25"),
    DependencyCapability("vector-term-retrieval", "faiss-cpu", "faiss"),
    DependencyCapability("local-embedding", "sentence-transformers", "sentence_transformers"),
    DependencyCapability("7z-archive", "py7zr", "py7zr"),
    DependencyCapability("rar-archive", "rarfile", "rarfile"),
)


def probe_dependency(capability: DependencyCapability) -> CapabilityReport:
    metadata = (
        ("bundled", capability.bundled),
        ("declared", capability.declared),
        ("distribution", capability.distribution),
        ("import_name", capability.import_name),
        ("locked", capability.locked),
        ("optional_at_runtime", capability.optional_at_runtime),
    )
    try:
        available = find_spec(capability.import_name) is not None
    except (ImportError, ModuleNotFoundError, ValueError) as exc:
        return CapabilityReport(
            CapabilityId(capability.feature),
            CapabilityState.UNAVAILABLE,
            reasons=(str(exc),),
            missing_prerequisites=(capability.distribution,),
            metadata=metadata,
        )
    if available:
        return CapabilityReport(CapabilityId(capability.feature), CapabilityState.AVAILABLE, metadata=metadata)
    return CapabilityReport(
        CapabilityId(capability.feature),
        CapabilityState.UNAVAILABLE,
        reasons=(f"optional dependency {capability.distribution!r} is not installed",),
        missing_prerequisites=(capability.distribution,),
        metadata=metadata,
    )


def probe_dependency_baseline() -> tuple[CapabilityReport, ...]:
    return tuple(probe_dependency(capability) for capability in DEPENDENCY_BASELINE)
