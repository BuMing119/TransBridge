"""Transport-neutral translation I/O contracts and format catalog.

Concrete adapters stay lazy so importing a leaf identity contract from the
legacy converter model cannot pull that model back in through this package.
"""

from typing import TYPE_CHECKING, Any

from .catalog import FormatCatalog, default_format_catalog
from .contracts import (
    CancellationToken,
    CapabilityLevel,
    FormatCapability,
    FormatCapabilitySnapshot,
    FormatId,
    FormatProbe,
    ParseRequest,
    ParseResult,
    ParseStats,
    ProbeConfidence,
    ProbeEvidence,
    ProbeEvidenceKind,
    ProbeRequest,
    ProbeStatus,
    SourceDescriptor,
    SourceSnapshot,
    WriteRequest,
)
from .identity import EntryKey, EntryRevision, ExternalEntryRef, Provenance, SourceNamespace
from .mutation import (
    ChangeSet,
    CollectionMutationPort,
    EntryPatch,
    EntrySnapshot,
    LegacyEntryMapping,
    LegacyMappingReport,
    MutationResult,
    MutationStatus,
)
from .paratranz import ParatranzJsonAdapter
from .paratranz_mapping import (
    PARATRANZ_CORE_FIELDS,
    PARATRANZ_SYSTEM,
    ParatranzEntry,
    ParatranzMappingBatch,
    map_paratranz_record,
    map_paratranz_records,
    paratranz_record_from_entry,
)
from .ports import FormatAdapter
from .stage_policy import (
    DEFAULT_STAGE_POLICY,
    Stage,
    StageDecision,
    StageOperation,
    StagePolicy,
    StagePolicyPort,
)
from .use_case import TranslationIoUseCase

if TYPE_CHECKING:
    from .legacy_adapters import EetXmlAdapter, SsePluginAdapter, XtXmlAdapter
    from .strings_adapter import LocalizedStringRecord, LocalizedStringsAdapter

_LAZY_ADAPTER_EXPORTS = frozenset({
    "EetXmlAdapter",
    "LocalizedStringRecord",
    "LocalizedStringsAdapter",
    "SsePluginAdapter",
    "XtXmlAdapter",
})


def __getattr__(name: str) -> Any:
    if name in _LAZY_ADAPTER_EXPORTS:
        if name.startswith("Localized"):
            from . import strings_adapter as adapter_module
        else:
            from . import legacy_adapters as adapter_module

        value = getattr(adapter_module, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "CancellationToken",
    "ChangeSet",
    "CapabilityLevel",
    "DEFAULT_STAGE_POLICY",
    "FormatAdapter",
    "FormatCapability",
    "FormatCapabilitySnapshot",
    "FormatCatalog",
    "FormatId",
    "FormatProbe",
    "CollectionMutationPort",
    "EntryKey",
    "EntryPatch",
    "EntryRevision",
    "EntrySnapshot",
    "EetXmlAdapter",
    "ExternalEntryRef",
    "LegacyEntryMapping",
    "LegacyMappingReport",
    "LocalizedStringRecord",
    "LocalizedStringsAdapter",
    "MutationResult",
    "MutationStatus",
    "PARATRANZ_CORE_FIELDS",
    "PARATRANZ_SYSTEM",
    "ParatranzEntry",
    "ParatranzJsonAdapter",
    "ParatranzMappingBatch",
    "ParseRequest",
    "ParseResult",
    "ParseStats",
    "ProbeConfidence",
    "ProbeEvidence",
    "ProbeEvidenceKind",
    "ProbeRequest",
    "ProbeStatus",
    "Provenance",
    "SourceDescriptor",
    "SourceNamespace",
    "SourceSnapshot",
    "Stage",
    "StageDecision",
    "StageOperation",
    "StagePolicy",
    "StagePolicyPort",
    "SsePluginAdapter",
    "TranslationIoUseCase",
    "WriteRequest",
    "XtXmlAdapter",
    "default_format_catalog",
    "map_paratranz_record",
    "map_paratranz_records",
    "paratranz_record_from_entry",
]
