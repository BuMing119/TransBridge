# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Prepare a complete source set before publishing a new Project."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, replace
import logging
from pathlib import Path

from transbridge.application.contracts import DomainError, ErrorCategory, RequestContext
from transbridge.application.io import FormatId, Stage
from transbridge.application.io.identity import SourceNamespace
from transbridge.config.text_encoding import TextEncodings
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.persistence.v2.variant import SourceBaseline
from transbridge.translation_memory.library import DictionaryLibrary

from .dictionary_apply import prepare_library_application
from .prepared_source_results import with_initial_entry_states
from .provisioning_models import (
    PreparedProjectSource,
    PreparedSourceHydration,
    ProjectProvisioningRequest,
    ProjectSourcePreparationPort,
)
from .source_encoding import with_source_read_options
from .source_registry import (
    SourceRegistrySnapshot,
    SourceRelation,
    SourceRelationKind,
    migrate_legacy_source_registry,
    normalize_source_location,
    stable_relation_id,
)


@dataclass(frozen=True, slots=True)
class PreparedProjectSources:
    registrations: tuple[PreparedProjectSource, ...]
    baselines: tuple[SourceBaseline, ...]
    hydrations: tuple[PreparedSourceHydration, ...]
    translation_pairs: tuple[tuple[int, int], ...] = ()

    def registry(self, project_id: str) -> SourceRegistrySnapshot:
        registry = migrate_legacy_source_registry(project_id, (item.to_dict() for item in self.registrations))
        if not self.translation_pairs:
            return registry
        relations = {item.from_source_id: item for item in registry.relations}
        source_ids = {normalize_source_location(item.location).casefold(): item.source_id for item in registry.sources}
        resolved_ids = set()
        for primary_index, migration_index in self.translation_pairs:
            primary_location = str(self.registrations[primary_index].to_dict()["location"])
            migration_location = str(self.registrations[migration_index].to_dict()["location"])
            primary_id = source_ids[normalize_source_location(primary_location).casefold()]
            migration_id = source_ids[normalize_source_location(migration_location).casefold()]
            kind = SourceRelationKind.TRANSLATION_FOR
            relations[migration_id] = SourceRelation(
                stable_relation_id(kind, migration_id, primary_id),
                kind,
                migration_id,
                primary_id,
                folded_into_baseline=True,
            )
            resolved_ids.add(migration_id)
        return replace(
            registry,
            relations=tuple(relations.values()),
            diagnostics=tuple(item for item in registry.diagnostics if item[1] not in resolved_ids),
        )


def prepare_project_sources(
    sources: ProjectSourcePreparationPort,
    request: ProjectProvisioningRequest,
    context: RequestContext,
    encodings: TextEncodings,
    *,
    initial_dictionary: Callable[[TextEncodings], DictionaryLibrary | None] | None = None,
) -> PreparedProjectSources:
    candidates = (
        *((source, "primary") for source in request.sources),
        *((source, "migration") for source in request.migration_sources),
    )
    registrations = []
    for source, role in candidates:
        try:
            registrations.append(
                sources.prepare_source(
                    with_source_read_options(source, encodings),
                    context,
                    role=role,
                    common_options=request.parse_options,
                )
            )
        except DomainError as exc:
            raise DomainError(
                exc.category,
                exc.code,
                f"{exc.message}\n来源：{source.location}",
                retryable=exc.retryable,
                details={**exc.details, "source_location": source.location},
                cause=exc,
            ) from exc
    locations = [normalize_source_location(str(item.to_dict()["location"])).casefold() for item in registrations]
    if len(set(locations)) != len(locations):
        raise DomainError(ErrorCategory.CONFLICT, "PROJECT_SOURCE_PATH_DUPLICATE", "工程来源路径重复。")

    groups: dict[SourceNamespace, list[int]] = defaultdict(list)
    for index, registration in enumerate(registrations):
        groups[registration.baseline.fingerprint.namespace].append(index)
    primary_count = len(request.sources)
    translation_pairs = []
    folded_migrations = set()
    changed_primaries = set()
    persist_prepared = getattr(sources, "persist_prepared_source", None)
    for indices in groups.values():
        if len(indices) == 1:
            continue
        primary_indices = [index for index in indices if index < primary_count]
        migration_indices = [index for index in indices if index >= primary_count]
        if (
            len(primary_indices) != 1
            or len(migration_indices) != 1
            or any(registrations[index].to_dict().get("format_id") != FormatId.PLUGIN_SSE.value for index in indices)
            or registrations[primary_indices[0]].hydration is None
        ):
            raise DomainError(ErrorCategory.CONFLICT, "PROJECT_SOURCE_IDENTITY_DUPLICATE", "工程来源身份重复。")
        primary_index, migration_index = primary_indices[0], migration_indices[0]
        registrations[primary_index] = _merge_plugin_translation(
            registrations[primary_index], registrations[migration_index]
        )
        changed_primaries.add(primary_index)
        translation_pairs.append((primary_index, migration_index))
        folded_migrations.add(migration_index)

    if initial_dictionary is not None:
        plugins = [
            index
            for index, source in enumerate(registrations[:primary_count])
            if source.hydration is not None and source.hydration.format_id is FormatId.PLUGIN_SSE
        ]
        library = initial_dictionary(encodings) if plugins else None
        if library is not None:
            for index in plugins:
                prepared = registrations[index]
                filled = _apply_initial_dictionary(prepared, library)
                if filled is not prepared:
                    registrations[index] = filled
                    changed_primaries.add(index)

    if persist_prepared is not None:
        for index in sorted(changed_primaries):
            registrations[index] = persist_prepared(registrations[index])

    baseline_sources = tuple(item for index, item in enumerate(registrations) if index not in folded_migrations)
    hydrations = tuple(item.hydration for item in baseline_sources if item.hydration is not None)
    return PreparedProjectSources(
        tuple(registrations),
        tuple(item.baseline for item in baseline_sources),
        hydrations if len(hydrations) == len(baseline_sources) else (),
        tuple(translation_pairs),
    )


def _apply_initial_dictionary(prepared: PreparedProjectSource, library: DictionaryLibrary) -> PreparedProjectSource:
    """Apply the new Variant's default library to empty rows after explicit import."""
    hydration = prepared.hydration
    assert hydration is not None
    candidates = (
        TranslationEntry(
            row.legacy_id,
            row.entry_key.local_key,
            row.original,
            row.translation,
            row.stage,
            row.context,
            entry_key=row.entry_key,
            metadata=row.metadata,
        )
        for row in hydration.entries
        if not row.translation and Stage.from_value(row.stage) is Stage.UNTRANSLATED
    )
    try:
        plan = prepare_library_application(candidates, library, mod_file_id=Path(hydration.location).stem)
    except Exception:  # noqa: BLE001 - optional automation cannot block project creation
        logging.getLogger(__name__).warning("新建工程时无法套用词典：%s", hydration.location, exc_info=True)
        return prepared
    return with_initial_entry_states(prepared, plan.states)


def _merge_plugin_translation(
    primary: PreparedProjectSource,
    migration: PreparedProjectSource,
) -> PreparedProjectSource:
    """Fold a translated plugin copy into its primary baseline by stable EntryKey."""

    if primary.hydration is None:
        raise ValueError("primary plugin translation migration requires hydration")
    originals = {entry.entry_key: entry.original for entry in primary.hydration.entries}
    candidates = {
        entry.entry_key: entry.translation for entry in migration.baseline.entries if entry.translation.strip()
    }

    def translated_value(entry_key, current: str) -> str | None:
        candidate = candidates.get(entry_key)
        original = originals.get(entry_key)
        if current or candidate is None or original is None or candidate == original:
            return None
        return candidate

    baseline_entries = tuple(
        replace(entry, translation=candidate, stage=Stage.TRANSLATED)
        if (candidate := translated_value(entry.entry_key, entry.translation)) is not None
        else entry
        for entry in primary.baseline.entries
    )
    hydration_entries = tuple(
        replace(entry, translation=candidate, stage=Stage.TRANSLATED.value)
        if (candidate := translated_value(entry.entry_key, entry.translation)) is not None
        else entry
        for entry in primary.hydration.entries
    )
    hydration = replace(primary.hydration, entries=hydration_entries)
    baseline = SourceBaseline(primary.baseline.fingerprint, baseline_entries)
    return replace(primary, baseline=baseline, hydration=hydration)
