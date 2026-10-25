"""Read-only authoritative source snapshots for file publication."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from transbridge.application.categorized_upload import UploadSource
from transbridge.application.contracts import DomainError, ErrorCategory, OperationResult, RequestContext
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.persistence.v2.baselines import BaselineRegistry

from .file_source_catalog import build_file_sources_catalog, registered_file_sources
from .lifecycle import ProjectLifecycleService
from .provisioning import ProjectSourcePreparationPort, ProjectSourceRequest
from .remote_binding import normalize_paratranz_endpoint
from .source_content import source_content_identity
from .source_hydration_cache import SourceHydrationCache


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
            baselines = self._baselines.provide(active.project, active.formal_variant_ref, context)
            sources = registered_file_sources(active.project)
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
                tuple(
                    (source.source_id, source.display_name or Path(source.location).name) for source, _raw in sources
                ),
                dirty=active.dirty,
            )
            self._assert_current(snapshot, context)
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
        namespace = source_content_identity(raw_source)
        matches = tuple(
            baseline
            for baseline in baselines
            if baseline.fingerprint.sha256 == source.fingerprint
            and (namespace is None or baseline.fingerprint.namespace.value == namespace)
        )
        if len(matches) != 1 or matches[0].fingerprint not in variant.source_fingerprints:
            _conflict("CATEGORIZED_SOURCE_BASELINE_AMBIGUOUS", f"来源缺少唯一且有效的版本基线：{source.location}")
        baseline = matches[0]
        if baseline.fingerprint.namespace in used_namespaces:
            _conflict("CATEGORIZED_SOURCE_NAMESPACE_AMBIGUOUS", f"多个来源对应同一份内容：{source.location}")
        used_namespaces.add(baseline.fingerprint.namespace)
        options = dict(source.format_options)
        options["source_namespace"] = baseline.fingerprint.namespace.value
        prepared = self._hydrations.prepare(
            self._source_preparer,
            ProjectSourceRequest(source.location, source.format_id, source.fingerprint, tuple(options.items())),
            context,
            scope_key=scope_key,
        )
        if prepared is None or prepared.fingerprint != baseline.fingerprint:
            _conflict("CATEGORIZED_SOURCE_CHANGED", f"来源内容已变化，请先更新工程来源：{source.location}")
        entries = []
        source_states = states.get(baseline.fingerprint.namespace, {})
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
        expected = set(source_states)
        if expected != {entry.identity for entry in entries}:
            _conflict("CATEGORIZED_SOURCE_ENTRIES_MISSING", f"来源解析结果无法覆盖版本中的全部词条：{source.location}")
        return UploadSource(source.source_id, source.display_name or Path(source.location).name, tuple(entries))

    def current(
        self, snapshot: CategorizedUploadSnapshot, context: RequestContext
    ) -> OperationResult[CategorizedUploadSnapshot]:
        """Check a captured upload session without reparsing source files."""
        try:
            self._assert_current(snapshot, context)
            return OperationResult.completed(snapshot, run_id=context.run_id)
        except Exception as exc:  # noqa: BLE001
            return OperationResult.from_exception(exc, run_id=context.run_id)

    def _active(self, context):
        active = self._lifecycle.active
        if active is None or active.variant is None or active.formal_variant_ref is None:
            raise DomainError(ErrorCategory.PREREQUISITE, "ACTIVE_VARIANT_REQUIRED", "请先打开一个工程版本。")
        if context.project_id is not None and context.project_id != active.project_ref.identity.value:
            _conflict("PROJECT_CONTEXT_MISMATCH", "当前工程与分类上传请求不一致。")
        if context.variant_id is not None and context.variant_id != active.formal_variant_ref.identity.value:
            _conflict("VARIANT_CONTEXT_MISMATCH", "当前版本与分类上传请求不一致。")
        return active

    def _assert_current(self, snapshot, context):
        active = self._active(context)
        if (
            active.project_ref.identity.value != snapshot.project_id
            or active.formal_variant_ref.identity.value != snapshot.variant_id
            or active.project.envelope.revision != snapshot.project_revision
            or active.variant.revision != snapshot.variant_revision
            or self._lifecycle.generation != snapshot.generation
        ):
            _conflict("CATEGORIZED_UPLOAD_STALE", "工程、版本或译文已变化，需重新读取当前来源；可在此重试。")
        return active


def _validate_remote_project_id(value):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("remote_project_id must be a positive integer")


def _conflict(code, message):
    raise DomainError(ErrorCategory.CONFLICT, code, message)


__all__ = ["CategorizedUploadSnapshot", "ProjectCategorizedUploadService"]
