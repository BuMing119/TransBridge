"""List authoritative source identities and counts without opening source files."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from transbridge.application.io.identity import SourceNamespace

from .source_content import authoritative_baseline_sources, source_content_identity
from .source_registry import SourceRegistration, SourceRegistrySnapshot, migrate_legacy_source_registry


@dataclass(frozen=True, slots=True)
class FileSourceCatalogItem:
    source_id: str
    label: str
    count: int
    namespace: SourceNamespace | None


def registered_file_sources(project):
    """Keep folded translation imports out of the primary source directory."""
    try:
        registry = SourceRegistrySnapshot.from_project_data(project.envelope.data)
    except (KeyError, TypeError, ValueError):
        registry = migrate_legacy_source_registry(project.envelope.identity, project.envelope.data.get("sources", ()))
    raw = authoritative_baseline_sources(
        tuple(source.to_dict() for source in registry.sources),
        tuple(relation.to_dict() for relation in registry.relations),
    )
    return tuple((source, value) for value in raw if (source := SourceRegistration.from_dict(value)).enabled)


def build_file_sources_catalog(project, variant):
    counts = Counter(entry.entry_key.namespace for entry in variant.entries if not entry.tombstone)
    by_digest = defaultdict(list)
    for fingerprint in variant.source_fingerprints:
        by_digest[fingerprint.sha256].append(fingerprint.namespace)
    result = []
    for source, raw in registered_file_sources(project):
        namespace = source_content_identity(raw)
        if namespace is not None:
            namespace = SourceNamespace(namespace)
        else:
            matches = by_digest.get(source.fingerprint, ())
            namespace = matches[0] if len(matches) == 1 else None
        result.append(
            FileSourceCatalogItem(
                source.source_id,
                source.display_name or Path(source.location).name,
                counts.get(namespace, 0),
                namespace,
            )
        )
    return tuple(result)
