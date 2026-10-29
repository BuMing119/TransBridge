"""Read-only authoritative source snapshots for file publication."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import logging
from pathlib import Path

from transbridge.application.categorized_upload import UploadSource
from transbridge.application.contracts import DomainError, ErrorCategory, OperationResult, RequestContext
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.persistence.v2.baselines import BaselineRegistry

from .entry_source_text import available_source_baselines, known_original_entries, registered_source_namespace
from .file_source_catalog import build_file_sources_catalog, registered_file_sources
from .installed_source_request import InstalledSourceRequest
from .lifecycle import ProjectLifecycleService
from .provisioning import ProjectSourcePreparationPort
from .remote_binding import normalize_paratranz_endpoint
from .source_hydration_cache import SourceHydrationCache

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CategorizedUploadSnapshot:
    project_id: str
    variant_id: str
    project_revision: int
    variant_revision: int
    generation: int
    endpoint: str
    remote_project_id: int
    sources: tuple[UploadSource, ...]
    failures: tuple[tuple[str, str, str], ...] = ()
    source_names: tuple[tuple[str, str], ...] = ()
    dirty: bool = False


class ProjectCategorizedUploadService:
    """Hydrate complete registered sources and guard their captured local revisions."""

    def __init__(
        self,
        lifecycle: ProjectLifecycleService,
        baselines: BaselineRegistry,
        source_preparer: ProjectSourcePreparationPort,
    ) -> None:
        self._lifecycle = lifecycle
        self._baselines = baselines
        self._source_preparer = source_preparer
        self._hydrations = SourceHydrationCache()

    def catalog(self, context: RequestContext):
        """Read the current source directory without parsing any source files."""
        try:
            generation = self._lifecycle.generation
            active = self._active(context)
            variant = active.variant.snapshot()
            result = build_file_sources_catalog(active.project, variant)
            if generation != self._lifecycle.generation:
                _conflict("CATEGORIZED_UPLOAD_STALE", "工程内容刚刚发生变化，请重新读取插件列表。")
            return OperationResult.completed(result, run_id=context.run_id)
        except Exception as exc:
            return OperationResult.from_exception(exc, run_id=context.run_id)

    def snapshot(
        self,
        context: RequestContext,
        *,
        endpoint: str,
        remote_project_id: int,
        source_ids: tuple[str, ...] | None = None,
        tolerate_failures: bool = False,
        include_external_refs: bool = False,
    ) -> OperationResult[CategorizedUploadSnapshot]:
        try:
            endpoint = normalize_paratranz_endpoint(endpoint)
            _validate_remote_project_id(remote_project_id)
            generation = self._lifecycle.generation
            active = self._active(context)
            variant = active.variant.snapshot()
            baselines = available_source_baselines(self._baselines, active.project, variant, context)
            sources = registered_file_sources(active.project)
            catalog = build_file_sources_catalog(active.project, variant)
            states = defaultdict(dict)
            for entry in variant.entries:
                if not entry.tombstone:
                    states[entry.entry_key.namespace][entry.entry_key] = entry
            upload_sources = []
            failures = []
            used_namespaces = set()
            for source, raw_source in sources:
                if source_ids is not None and source.source_id not in source_ids:
                    continue
                try:
                    upload_sources.append(
                        self._hydrate(
                            source,
                            raw_source,
                            baselines,
                            variant,
                            states,
                            context,
                            used_namespaces,
                            scope_key=(active.project_ref.identity.value, variant.ref.identity.value),
                            include_external_refs=include_external_refs,
                        )
                    )
                except Exception as exc:
                    if not tolerate_failures:
                        raise
                    failures.append((source.source_id, source.display_name or Path(source.location).name, str(exc)))
            registered_ids = {source.source_id for source, _raw in sources}
            for item in catalog:
                if item.source_id in registered_ids or source_ids is not None and item.source_id not in source_ids:
                    continue
                try:
                    entries = known_original_entries(
                        states.get(item.namespace, {}).values(), include_external_refs=include_external_refs
                    )
                    if entries is None:
                        _conflict(
                            "CATEGORIZED_SOURCE_ENTRIES_MISSING",
                            f"该来源仍有未保存原文的词条，请补充输入：{item.label}",
                        )
                    upload_sources.append(UploadSource(item.source_id, item.label, entries))
                except Exception as exc:
                    if not tolerate_failures:
                        raise
                    failures.append((item.source_id, item.label, str(exc)))
            snapshot = CategorizedUploadSnapshot(
                active.project_ref.identity.value,
                variant.ref.identity.value,
                active.project.envelope.revision,
                variant.revision,
                generation,
                endpoint,
                remote_project_id,
                tuple(upload_sources),
                tuple(failures),
                tuple((item.source_id, item.label) for item in catalog),
                dirty=active.dirty,
            )
            self._assert_current(snapshot, context, require_revision=True)
            return OperationResult.completed(snapshot, run_id=context.run_id)
        except Exception as exc:  # noqa: BLE001 - retain source and repository domain diagnostics
            return OperationResult.from_exception(exc, run_id=context.run_id)

    def _hydrate(
        self,
        source,
        raw_source,
        baselines,
        variant,
        states,
        context,
        used_namespaces,
        *,
        scope_key,
        include_external_refs,
    ):
        namespace = registered_source_namespace(source, raw_source, variant)
        if namespace is None:
            _conflict("CATEGORIZED_SOURCE_BASELINE_AMBIGUOUS", f"来源缺少唯一且有效的版本基线：{source.location}")
        if namespace in used_namespaces:
            _conflict("CATEGORIZED_SOURCE_NAMESPACE_AMBIGUOUS", f"多个来源对应同一份内容：{source.location}")
        used_namespaces.add(namespace)
        source_states = states.get(namespace, {})
        label = source.display_name or Path(source.location).name or source.source_id
        try:
            matches = tuple(
                baseline
                for baseline in baselines
                if baseline.fingerprint.namespace == namespace
                and baseline.fingerprint.sha256 == source.fingerprint
                and baseline.fingerprint in variant.source_fingerprints
            )
            if len(matches) != 1 or not source.source_artifact_id:
                _conflict("CATEGORIZED_SOURCE_BASELINE_AMBIGUOUS", f"来源的保存内容尚不可用，请补充输入：{label}")
            baseline = matches[0]
            options = dict(source.format_options)
            options["source_namespace"] = namespace.value
            prepared = self._hydrations.read_installed(
                self._source_preparer,
                InstalledSourceRequest(source.location, source.format_id, source.fingerprint, tuple(options.items())),
                context,
                scope_key=scope_key,
                source=raw_source,
            )
            if prepared is None or prepared.fingerprint != baseline.fingerprint:
                _conflict("CATEGORIZED_SOURCE_CHANGED", f"来源内容已变化，请先更新工程来源：{source.location}")
        except (DomainError, OSError):
            entries = known_original_entries(source_states.values(), include_external_refs=include_external_refs)
            if entries is None:
                raise
            _logger.debug("Using saved identity originals for source %s", source.source_id, exc_info=True)
            return UploadSource(source.source_id, label, entries)
        entries = []
        for parsed in prepared.entries:
            state = source_states.get(parsed.entry_key)
            if state is not None:
                entries.append(
                    TranslationEntry(
                        id=parsed.legacy_id,
                        key=parsed.entry_key.local_key,
                        original=parsed.original,
                        translation=state.translation,
                        stage=state.stage.value,
                        context=parsed.context,
                        entry_key=parsed.entry_key,
                        revision=state.revision,
                        provenance=state.provenance,
                        metadata=parsed.metadata,
                        string_id=parsed.string_id,
                        external_refs=state.external_refs if include_external_refs else (),
                    )
                )
        present = {entry.identity for entry in entries}
        remaining = known_original_entries(
            (state for key, state in source_states.items() if key not in present),
            include_external_refs=include_external_refs,
        )
        if remaining is None:
            _conflict("CATEGORIZED_SOURCE_ENTRIES_MISSING", f"来源解析结果无法覆盖版本中的全部词条：{source.location}")
        return UploadSource(source.source_id, label, (*entries, *remaining))

    def current(
        self, snapshot: CategorizedUploadSnapshot, context: RequestContext
    ) -> OperationResult[CategorizedUploadSnapshot]:
        """Check a captured upload session without reparsing source files."""
        try:
            self._assert_current(snapshot, context)
            return OperationResult.completed(snapshot, run_id=context.run_id)
        except Exception as exc:  # noqa: BLE001
            return OperationResult.from_exception(exc, run_id=context.run_id)

    def result_source_texts(self, project, variant, keys, context):
        """Read verified input text for just the sources containing result targets."""
        from .result_sources import read_result_source_texts

        return read_result_source_texts(
            project,
            variant,
            keys,
            context,
            baselines=self._baselines,
            preparer=self._source_preparer,
            hydrations=self._hydrations,
        )

    def _active(self, context):
        active = self._lifecycle.active
        if active is None or active.variant is None or active.formal_variant_ref is None:
            raise DomainError(ErrorCategory.PREREQUISITE, "ACTIVE_VARIANT_REQUIRED", "请先打开一个工程版本。")
        if context.project_id is not None and context.project_id != active.project_ref.identity.value:
            _conflict("PROJECT_CONTEXT_MISMATCH", "当前工程与分类上传请求不一致。")
        if context.variant_id is not None and context.variant_id != active.formal_variant_ref.identity.value:
            _conflict("VARIANT_CONTEXT_MISMATCH", "当前版本与分类上传请求不一致。")
        return active

    def _assert_current(self, snapshot, context, *, require_revision=False):
        active = self._active(context)
        if (
            active.project_ref.identity.value != snapshot.project_id
            or active.formal_variant_ref.identity.value != snapshot.variant_id
            or require_revision
            and (
                active.project.envelope.revision != snapshot.project_revision
                or active.variant.revision != snapshot.variant_revision
                or self._lifecycle.generation != snapshot.generation
            )
        ):
            _conflict("CATEGORIZED_UPLOAD_STALE", "工程、版本或译文已变化，需重新读取当前来源；可在此重试。")
        return active


def _validate_remote_project_id(value):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("remote_project_id must be a positive integer")


def _conflict(code, message):
    raise DomainError(ErrorCategory.CONFLICT, code, message)


__all__ = ["CategorizedUploadSnapshot", "ProjectCategorizedUploadService"]
