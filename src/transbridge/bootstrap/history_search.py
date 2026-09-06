"""Production readers and composition for the persisted-history search index."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.history_search import (
    HistoryDiagnostic,
    HistoryEntryKind,
    HistorySearchRefreshService,
    HistorySearchService,
    HistorySearchTaskEntrypoint,
    HistorySourceRef,
    HistorySourceType,
    ProviderResult,
    SourceRecord,
)
from transbridge.application.io import EntryKey, ParseRequest, SourceDescriptor, TranslationIoUseCase
from transbridge.application.projects import variant_catalog
from transbridge.application.projects.source_registry import SourceRegistrySnapshot
from transbridge.persistence.history_search import SqliteHistorySearchIndex
from transbridge.persistence.terminology import SqliteTerminologyRepository, TerminologyPaths
from transbridge.persistence.v2.ids import ProjectId, ProjectRef, VariantId, VariantRef
from transbridge.persistence.v2.variant import VariantSnapshot
from transbridge.translation_memory.model import Dictionary


@dataclass(frozen=True, slots=True)
class ProductionHistorySearch:
    index: SqliteHistorySearchIndex
    query: HistorySearchService
    refresh: HistorySearchRefreshService
    tasks: HistorySearchTaskEntrypoint


class ProjectVariantHistoryProvider:
    name = "Project/Variant"

    def __init__(self, catalog, projects, variants, io: TranslationIoUseCase | None = None) -> None:
        self._catalog = catalog
        self._projects = projects
        self._variants = variants
        self._io = io or TranslationIoUseCase()

    def collect(self, cancellation) -> ProviderResult:
        records: list[SourceRecord] = []
        diagnostics: list[HistoryDiagnostic] = []
        catalog = self._catalog.list_projects()
        diagnostics.extend(
            HistoryDiagnostic(item.code, item.message, "Project catalog") for item in catalog.diagnostics
        )
        for catalog_entry in catalog.projects:
            _raise_if_cancelled(cancellation)
            if not catalog_entry.available:
                diagnostics.append(
                    HistoryDiagnostic(
                        "HISTORY_PROJECT_UNAVAILABLE",
                        catalog_entry.reason or "工程记录不可用。",
                        catalog_entry.name,
                    )
                )
                continue
            try:
                project_ref = ProjectRef(ProjectId(catalog_entry.project_id))
                project = self._projects.read_snapshot(project_ref)
                registry = SourceRegistrySnapshot.from_project_data(project.envelope.data)
                originals, source_by_namespace, parse_diagnostics = self._originals(
                    catalog_entry.project_id,
                    registry,
                    cancellation,
                )
                diagnostics.extend(parse_diagnostics)
                locales = _project_locales(project.envelope.data)
                for descriptor in variant_catalog(project):
                    _raise_if_cancelled(cancellation)
                    variant_ref = VariantRef(VariantId(descriptor.variant_id), project_ref.identity)
                    snapshot = VariantSnapshot.from_dto(self._variants.read_snapshot(variant_ref), variant_ref)
                    unmatched = 0
                    for entry in snapshot.entries:
                        translation = entry.translation.strip()
                        original = originals.get(entry.entry_key)
                        if entry.tombstone or not translation:
                            continue
                        if original is None or not original.strip():
                            unmatched += 1
                            continue
                        registration = source_by_namespace.get(entry.entry_key.namespace.value)
                        plugin_id = None if registration is None else registration.plugin_scope
                        source_label = Path(registration.location).name if registration is not None else "未知来源"
                        records.append(
                            SourceRecord(
                                HistoryEntryKind.TRANSLATION,
                                original,
                                entry.translation,
                                HistorySourceRef(
                                    HistorySourceType.PROJECT_VARIANT,
                                    f"{catalog_entry.project_id}:{descriptor.variant_id}:{entry.entry_key.serialize()}",
                                    f"{catalog_entry.name} / {descriptor.name} / {source_label}",
                                    project_id=catalog_entry.project_id,
                                    project_name=catalog_entry.name,
                                    variant_id=descriptor.variant_id,
                                    variant_name=descriptor.name,
                                    plugin_id=plugin_id or source_label,
                                    details=(("entry_key", entry.entry_key.serialize()),),
                                ),
                                source_locale=locales[0],
                                target_locale=locales[1],
                                status=entry.stage.name,
                            )
                        )
                    if unmatched:
                        diagnostics.append(
                            HistoryDiagnostic(
                                "HISTORY_ENTRY_UNMATCHED",
                                f"{unmatched} 条已保存译文无法用完整 EntryKey 恢复原文，已跳过。",
                                f"{catalog_entry.name} / {descriptor.name}",
                            )
                        )
            except Exception as exc:  # noqa: BLE001 - isolate one Project from all remaining sources
                diagnostics.append(
                    HistoryDiagnostic("HISTORY_PROJECT_READ_FAILED", f"工程读取失败：{exc}", catalog_entry.name)
                )
        return ProviderResult(tuple(records), tuple(diagnostics))

    def _originals(self, project_id, registry, cancellation):
        originals = {}
        source_by_namespace = {}
        diagnostics: list[HistoryDiagnostic] = []
        for registration in registry.sources:
            _raise_if_cancelled(cancellation)
            if not registration.enabled:
                continue
            source_name = registration.display_name or Path(registration.location).name
            try:
                path = Path(registration.location)
                result = self._io.parse(
                    ParseRequest(
                        SourceDescriptor(str(path), source_name, path.stat().st_size),
                        RequestContext("history-search", project_id=project_id),
                        format_hint=registration.format_id,
                        options=registration.format_options,
                        cancellation=cancellation,
                    )
                )
                if result.outcome not in {OperationOutcome.COMPLETED, OperationOutcome.PARTIAL}:
                    message = result.diagnostics[0].message if result.diagnostics else "来源无法解析。"
                    raise ValueError(message)
                if registration.fingerprint and result.source_snapshot is not None:
                    if result.source_snapshot.sha256 != registration.fingerprint:
                        raise ValueError("来源内容已变化，与保存时指纹不一致。")
                for parsed in result.entries:
                    key = getattr(parsed, "identity", None) or getattr(parsed, "entry_key", None)
                    if not isinstance(key, EntryKey):
                        continue
                    originals[key] = parsed.original
                    source_by_namespace[key.namespace.value] = registration
            except Exception as exc:  # noqa: BLE001 - isolate one registered source
                diagnostics.append(
                    HistoryDiagnostic("HISTORY_SOURCE_PARSE_FAILED", f"来源无法恢复原文：{exc}", source_name)
                )
        return originals, source_by_namespace, tuple(diagnostics)


class DictionaryHistoryProvider:
    name = ".tbdict"

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def collect(self, cancellation) -> ProviderResult:
        if not self._root.exists():
            return ProviderResult()
        records: list[SourceRecord] = []
        diagnostics: list[HistoryDiagnostic] = []
        for path in sorted(self._root.glob("*.tbdict")):
            _raise_if_cancelled(cancellation)
            try:
                dictionary = Dictionary.from_dict(json.loads(path.read_text(encoding="utf-8")))
                for entry_id, entry in dictionary.entries.items():
                    if not entry.original.strip() or not entry.translation.strip():
                        continue
                    enabled_status = "" if entry.enabled else " / disabled"
                    records.append(
                        SourceRecord(
                            HistoryEntryKind.TRANSLATION,
                            entry.original,
                            entry.translation,
                            HistorySourceRef(
                                HistorySourceType.DICTIONARY,
                                f"{dictionary.dictionary_id}:{entry_id}",
                                f"词典 {dictionary.mod_file_id}",
                                plugin_id=entry.source_mod or dictionary.mod_file_id,
                                dictionary_id=dictionary.dictionary_id,
                                details=(("file", path.name), ("scope", dictionary.scope)),
                            ),
                            source_locale=entry.source_locale,
                            target_locale=entry.target_locale,
                            status=f"{dictionary.scope} / stage {entry.stage}{enabled_status}",
                        )
                    )
            except Exception as exc:  # noqa: BLE001 - corrupt dictionaries remain untouched
                diagnostics.append(HistoryDiagnostic("HISTORY_DICTIONARY_INVALID", f"词典格式无效：{exc}", path.name))
        return ProviderResult(tuple(records), tuple(diagnostics))


class TerminologyHistoryProvider:
    name = "Terminology"

    def __init__(self, persistence_root: str | Path, catalog, projects) -> None:
        self._root = Path(persistence_root).resolve(strict=False)
        self._catalog = catalog
        self._projects = projects
        self._paths = TerminologyPaths(self._root)

    def collect(self, cancellation) -> ProviderResult:
        records: list[SourceRecord] = []
        diagnostics: list[HistoryDiagnostic] = []
        for catalog_entry in self._catalog.list_projects().projects:
            _raise_if_cancelled(cancellation)
            database = self._paths.database(catalog_entry.project_id)
            if not database.is_file():
                continue
            repository = None
            try:
                project_ref = ProjectRef(ProjectId(catalog_entry.project_id))
                project = self._projects.read_snapshot(project_ref)
                locales = _project_locales(project.envelope.data)
                repository = SqliteTerminologyRepository.open(str(self._root), catalog_entry.project_id, writable=False)
                names = {item.variant_id: item.name for item in variant_catalog(project)}
                for variant_id, variant_name in names.items():
                    _raise_if_cancelled(cancellation)
                    version = repository.effective_version(catalog_entry.project_id, variant_id)
                    if version is None:
                        continue
                    for decision in version.decisions:
                        if not decision.is_effective:
                            continue
                        scope = f"project:{catalog_entry.project_id}:{decision.scope.canonical_key}"
                        records.append(
                            SourceRecord(
                                HistoryEntryKind.TERM,
                                decision.original,
                                decision.translation,
                                HistorySourceRef(
                                    HistorySourceType.TERMINOLOGY,
                                    f"{version.ref.version_id}:{decision.term_id}",
                                    f"术语 {catalog_entry.name} / {variant_name}",
                                    project_id=catalog_entry.project_id,
                                    project_name=catalog_entry.name,
                                    variant_id=variant_id,
                                    variant_name=variant_name,
                                    plugin_id=decision.scope.plugin_id,
                                    details=(
                                        ("version", version.ref.version_id),
                                        ("scope", decision.scope.canonical_key),
                                    ),
                                ),
                                source_locale=locales[0],
                                target_locale=locales[1],
                                scope_key=scope,
                                status=decision.status.value,
                            )
                        )
            except Exception as exc:  # noqa: BLE001 - isolate one terminology database
                diagnostics.append(
                    HistoryDiagnostic("HISTORY_TERMINOLOGY_READ_FAILED", f"术语库读取失败：{exc}", catalog_entry.name)
                )
            finally:
                if repository is not None:
                    repository.close()
        return ProviderResult(tuple(records), tuple(diagnostics))


def build_production_history_search(
    *,
    persistence,
    task_runtime,
    translation_memory_root: str | Path,
) -> ProductionHistorySearch:
    index = SqliteHistorySearchIndex(Path(persistence.root) / "history-search" / "history.sqlite3")
    providers = (
        ProjectVariantHistoryProvider(persistence.project_catalog, persistence.projects, persistence.variants),
        DictionaryHistoryProvider(translation_memory_root),
        TerminologyHistoryProvider(persistence.root, persistence.project_catalog, persistence.projects),
    )
    refresh = HistorySearchRefreshService(index, providers)
    query = HistorySearchService(index)
    return ProductionHistorySearch(index, query, refresh, HistorySearchTaskEntrypoint(task_runtime, refresh))


def _project_locales(data) -> tuple[str, str]:
    source = str(data.get("source_locale") or data.get("source_language") or "")
    target = str(data.get("target_locale") or data.get("target_language") or "")
    return source, target


def _raise_if_cancelled(cancellation) -> None:
    if cancellation is not None:
        method = getattr(cancellation, "raise_if_cancelled", None)
        if callable(method):
            method()


__all__ = [
    "DictionaryHistoryProvider",
    "ProductionHistorySearch",
    "ProjectVariantHistoryProvider",
    "TerminologyHistoryProvider",
    "build_production_history_search",
]
