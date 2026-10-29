"""Resolve plugin import records that share one materialized content source."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .source_registry import SourceRelationKind, is_plugin_source, legacy_source_role


@dataclass(frozen=True, slots=True)
class FoldedPluginPair:
    primary: dict[str, Any]
    translation: dict[str, Any]


def source_content_identity(source: Mapping[str, Any]) -> str | None:
    """Read the content namespace retained by both legacy and canonical sources.

    A canonical source_id identifies a registration, not its content. File
    digests likewise verify a revision and must not substitute for a namespace.
    """

    legacy = source.get("legacy")
    if isinstance(legacy, Mapping):
        value = legacy.get("namespace") or legacy.get("source_id")
    elif "bilingual_capability" not in source:
        value = source.get("namespace") or source.get("key") or source.get("source_id")
    else:
        value = None
    normalized = "" if value is None else str(value).strip()
    return normalized or None


def folded_plugin_pairs(
    sources: tuple[dict[str, Any], ...],
    relations: tuple[dict[str, Any], ...] = (),
) -> tuple[FoldedPluginPair, ...]:
    """Recognize one confirmed baseline fold, or a legacy shared-namespace pair.

    Provisioning folds one translated ESP into its original ESP, retaining both
    registrations. An explicit relation survives primary source replacement:
    the retained translation keeps the identity of its captured input artifact.
    Legacy records may lack relations; their explicit roles and shared namespace
    are sufficient, but a conflicting recorded relation must never be ignored.
    """

    pairs: list[FoldedPluginPair] = []
    by_id = {source.get("source_id"): source for source in sources}
    for translation in sources:
        if legacy_source_role(translation) != "migration" or not is_plugin_source(translation):
            continue
        links = tuple(item for item in relations if item.get("from_source_id") == translation.get("source_id"))
        if (
            len(links) != 1
            or links[0].get("kind") != SourceRelationKind.TRANSLATION_FOR.value
            or not links[0].get("folded_into_baseline", False)
        ):
            continue
        primary = by_id.get(links[0].get("to_source_id"))
        if primary is None or legacy_source_role(primary) != "primary" or not is_plugin_source(primary):
            continue
        identity = source_content_identity(translation)
        if identity is not None and any(
            source is not primary
            and legacy_source_role(source) == "primary"
            and source_content_identity(source) == identity
            for source in sources
        ):
            # A relation must not redirect an existing namespace pair to a
            # different primary; that is ambiguous rather than a source update.
            continue
        incoming = tuple(
            item
            for item in relations
            if item.get("kind") == SourceRelationKind.TRANSLATION_FOR.value
            and item.get("to_source_id") == primary.get("source_id")
            and (member := by_id.get(item.get("from_source_id"))) is not None
            and legacy_source_role(member) == "migration"
            and is_plugin_source(member)
        )
        if len(incoming) == 1:
            pairs.append(FoldedPluginPair(primary, translation))

    groups: dict[str, list[dict[str, Any]]] = {}
    for source in sources:
        if any(source is pair.primary or source is pair.translation for pair in pairs):
            continue
        identity = source_content_identity(source)
        if identity is not None:
            groups.setdefault(identity, []).append(source)
    for members in groups.values():
        if len(members) != 2 or any(not is_plugin_source(source) for source in members):
            continue
        primaries = [source for source in members if legacy_source_role(source) == "primary"]
        translations = [source for source in members if legacy_source_role(source) == "migration"]
        if len(primaries) != 1 or len(translations) != 1:
            continue
        primary, translation = primaries[0], translations[0]
        links = tuple(item for item in relations if item.get("from_source_id") == translation.get("source_id"))
        if links and (
            len(links) != 1
            or links[0].get("kind") != SourceRelationKind.TRANSLATION_FOR.value
            or links[0].get("to_source_id") != primary.get("source_id")
        ):
            continue
        pairs.append(FoldedPluginPair(primary, translation))
    return tuple(pairs)


def authoritative_baseline_sources(
    sources: tuple[dict[str, Any], ...],
    relations: tuple[dict[str, Any], ...] = (),
) -> tuple[dict[str, Any], ...]:
    pairs = folded_plugin_pairs(sources, relations)
    if not pairs:
        return sources
    return tuple(source for source in sources if all(source is not pair.translation for pair in pairs))


__all__ = ["authoritative_baseline_sources", "folded_plugin_pairs", "source_content_identity"]
