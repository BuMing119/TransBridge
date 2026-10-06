"""Explicit, owner-bound source updates across every saved translation variant."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
import os
from pathlib import Path
import secrets
from threading import RLock

from transbridge.application.contracts import DomainError, ErrorCategory, OperationResult
from transbridge.application.io.identity import SourceNamespace
from transbridge.persistence.v2.ids import ProjectId, ProjectRef
from transbridge.persistence.v2.models import ProjectDto
from transbridge.persistence.v2.schema import parse_json_bytes

from .provisioning import ProjectSourceRequest
from .source_content import authoritative_baseline_sources, source_content_identity
from .source_registry import SourceRegistrySnapshot, migrate_legacy_source_registry
from .source_update_migration import compare_source, migrate_variant
from .source_update_models import SourceUpdatePreview, SourceUpdateResult, SourceUpdateTarget


@dataclass(frozen=True)
class _Candidate:
    owner: str
    generation: int
    expected: object
    project: ProjectDto
    variants: tuple
    request: ProjectSourceRequest
    source: object
    old_namespace: SourceNamespace
    path: str
    preview: SourceUpdatePreview


class ProjectSourceUpdateService:
    def __init__(self, lifecycle, projects, store, baselines, preparer):
        self._lifecycle = lifecycle
        self._projects = projects
        self._store = store
        self._baselines = baselines
        self._preparer = preparer
        self._pending: dict[str, _Candidate] = {}
        self._lock = RLock()

    def active_project_path(self, context) -> str | None:
        active = self._lifecycle.active
        if active is None:
            return None
        self._check_context(active.project_ref, context)
        return self._projects.path_for(active.project_ref)

    def list_sources(self, project_path, context):
        try:
            ref = self._resolve(project_path, context)
            project = self._store.read(ref).project
            registry = _registry(project)
            primary = authoritative_baseline_sources(
                tuple(item.to_dict() for item in registry.sources), tuple(item.to_dict() for item in registry.relations)
            )
            ids = {item["source_id"] for item in primary}
            return OperationResult.completed(
                tuple(
                    SourceUpdateTarget(
                        item.source_id, item.location, item.format_id, item.display_name or Path(item.location).name
                    )
                    for item in registry.sources
                    if item.source_id in ids
                ),
                run_id=context.run_id,
            )
        except Exception as exc:
            return OperationResult.from_exception(exc, run_id=context.run_id)

    def prepare(self, project_path, source_id, replacement_path, context):
        try:
            ref = self._resolve(project_path, context)
            generation = self._lifecycle.generation
            active = self._lifecycle.active
            if active is not None and active.project_ref == ref and active.dirty:
                raise ValueError("请先保存当前工程的修改，再更新源文件。")
            expected = self._store.read(ref)
            registry = _registry(expected.project)
            source = next((item for item in registry.sources if item.source_id == source_id), None)
            if source is None:
                raise ValueError("工程中找不到所选源文件。")
            targets = self.list_sources(project_path, context)
            if not targets.is_success or source_id not in {item.source_id for item in targets.value or ()}:
                raise ValueError("所选来源不是独立的工程源文件，请更新其对应的原始来源。")
            path = str(Path(replacement_path).resolve(strict=True))
            if any(
                item.source_id != source_id and os.path.normcase(item.location) == os.path.normcase(path)
                for item in registry.sources
            ):
                raise ValueError("新版文件已被工程中的其他来源使用。")
            request = ProjectSourceRequest(path, source.format_id, options=source.format_options)
            prepared = self._preparer.prepare_source(request, context, role="primary", common_options=())
            identity = source_content_identity(source.to_dict())
            namespaces = {
                fingerprint.namespace
                for variant in expected.variants
                for fingerprint in variant.source_fingerprints
                if fingerprint.sha256 == source.fingerprint
                and (identity is None or fingerprint.namespace.value == identity)
            }
            if len(namespaces) != 1:
                raise ValueError("无法把所选来源唯一对应到翻译版本，请检查工程来源登记。")
            namespace = namespaces.pop()
            for variant in expected.variants:
                matches = [item for item in variant.source_fingerprints if item.namespace == namespace]
                if len(matches) != 1 or matches[0].sha256 != source.fingerprint:
                    raise ValueError("工程内翻译版本的来源不一致，无法统一更新。")
            old, warnings = self._previous(source, context)
            keys = {
                (item.entry_key.local_key, item.entry_key.original)
                for variant in expected.variants
                for item in variant.entries
                if item.entry_key.namespace == namespace
            }
            changes = compare_source(keys, old, prepared)
            variants = tuple(migrate_variant(item, namespace, prepared, changes) for item in expected.variants)
            project = _updated_project(expected.project, registry, source, prepared, namespace)
            token = secrets.token_hex(24)
            preview = SourceUpdatePreview(
                token,
                source.display_name or Path(source.location).name,
                path,
                len(variants),
                len(changes.added),
                len(changes.removed),
                len(changes.unchanged),
                len(changes.changed),
                len(changes.unverified),
                len(changes.reordered),
                warnings,
            )
            candidate = _Candidate(
                context.owner_id,
                generation,
                expected,
                project,
                variants,
                replace(request, expected_fingerprint=prepared.baseline.fingerprint.sha256),
                prepared,
                namespace,
                str(self._projects.path_for(ref)),
                preview,
            )
            with self._lock:
                self._pending[token] = candidate
            return OperationResult.completed(preview, run_id=context.run_id)
        except Exception as exc:
            return OperationResult.from_exception(exc, run_id=context.run_id)

    def commit(self, token, context):
        try:
            with self._lock:
                candidate = self._pending.get(token)
                if candidate is None:
                    raise ValueError("更新预览已失效，请重新预览。")
                if candidate.owner != context.owner_id:
                    raise DomainError(
                        ErrorCategory.PERMISSION, "SOURCE_UPDATE_OWNER_MISMATCH", "更新预览不属于当前操作。"
                    )
                ref = ProjectRef(ProjectId(candidate.project.envelope.identity))
                self._check_context(ref, context)
                del self._pending[token]
            # Reparse and fingerprint-check all members, including localized plugin sidecars.
            refreshed = self._preparer.prepare_source(candidate.request, context, role="primary", common_options=())
            if refreshed != candidate.source:
                raise ValueError("新版源文件在预览后发生变化，请重新预览。")
            backup = []

            def publish():
                backup.append(
                    self._store.commit(
                        candidate.expected,
                        candidate.project,
                        candidate.variants,
                        token,
                        backup_metadata={
                            "source": candidate.preview.source_name,
                            "replacement": candidate.preview.replacement_path,
                        },
                    )
                )

            active = self._lifecycle.active
            if active is not None and active.project_ref == ref:
                if active.dirty or active.variant is None:
                    raise ValueError("预览后工程有未保存修改，请保存并重新预览。")
                variant = next(item for item in candidate.variants if item.ref == active.formal_variant_ref)
                baselines = self._baselines.provide(active.project, active.formal_variant_ref, context)
                new_baselines = tuple(
                    item for item in baselines if item.fingerprint.namespace != candidate.old_namespace
                )

                def publish_active():
                    publish()
                    self._baselines.replace_many(
                        ref, tuple(item.ref for item in candidate.variants), (*new_baselines, refreshed.baseline)
                    )

                result = self._lifecycle.commit_active_content(
                    candidate.project,
                    variant,
                    context,
                    expected_project_revision=candidate.expected.project.envelope.revision,
                    expected_variant_revision=variant.revision - 1,
                    expected_generation=candidate.generation,
                    before_publish=publish_active,
                    persisted=True,
                )
                if not result.is_success:
                    return result
            else:
                self._lifecycle.commit_detached_content(ref, candidate.generation, publish)
                for variant in candidate.variants:
                    self._baselines.remove(ref, variant.ref)
            return OperationResult.completed(SourceUpdateResult(candidate.path, backup[0]), run_id=context.run_id)
        except Exception as exc:
            return OperationResult.from_exception(exc, run_id=context.run_id)

    def discard(self, token, context) -> None:
        with self._lock:
            candidate = self._pending.get(token)
            if candidate is not None and candidate.owner == context.owner_id:
                del self._pending[token]

    def _resolve(self, path, context):
        selected = Path(path).resolve(strict=True)
        document = parse_json_bytes(selected.read_bytes())
        ref = ProjectRef(ProjectId(str(document["id"])))
        if selected != Path(self._projects.path_for(ref)).resolve():
            raise ValueError("只能更新当前数据目录中登记的工程。")
        self._check_context(ref, context)
        return ref

    @staticmethod
    def _check_context(ref, context):
        if context.project_id is not None and context.project_id != ref.identity.value:
            raise DomainError(ErrorCategory.PERMISSION, "PROJECT_CONTEXT_MISMATCH", "更新目标与请求工程不一致。")

    def _previous(self, source, context):
        if source.fingerprint is not None:
            try:
                old = self._preparer.prepare_source(
                    ProjectSourceRequest(source.location, source.format_id, source.fingerprint, source.format_options),
                    context,
                    role="primary",
                    common_options=(),
                )
                metadata = {} if old.hydration is None else dict(old.hydration.source_snapshot.metadata)
                if metadata.get("localized_sources") or metadata.get("localized_lookup_detected"):
                    return None, (
                        "旧插件的配套 Strings 文件版本没有独立保存，无法核实旧原文；已有译文将保留并标为待复核。",
                    )
                return old, ()
            except OSError:
                pass
            except DomainError as exc:
                if exc.category not in {ErrorCategory.INPUT, ErrorCategory.CONFLICT, ErrorCategory.PREREQUISITE}:
                    raise
        return None, ("旧源文件已变化或不可读取，无法核实原文和旧顺序；匹配的已有译文将保留并标为待复核。",)


def _registry(project):
    data = project.envelope.data
    try:
        return SourceRegistrySnapshot.from_project_data(data)
    except (KeyError, TypeError, ValueError):
        return migrate_legacy_source_registry(project.envelope.identity, data.get("sources", ()))


def _updated_project(project, registry, source, prepared, namespace):
    registration = migrate_legacy_source_registry(project.envelope.identity, (prepared.to_dict(),)).sources[0]
    updated = replace(
        registration,
        source_id=source.source_id,
        display_name=source.display_name,
        enabled=source.enabled,
        plugin_scope=source.plugin_scope,
    )
    sources = []
    for item in registry.sources:
        if item.source_id == source.source_id:
            sources.append(updated)
        elif source_content_identity(item.to_dict()) == namespace.value:
            # Folded translated-plugin imports retain their registration and role.
            legacy = dict(item.legacy)
            legacy["source_id"] = prepared.baseline.fingerprint.namespace.value
            legacy.pop("namespace", None)
            sources.append(replace(item, legacy=tuple(legacy.items())))
        else:
            sources.append(item)
    data = deepcopy(project.envelope.data)
    data.update(SourceRegistrySnapshot(tuple(sources), registry.relations, registry.diagnostics).to_project_data())
    return ProjectDto(replace(project.envelope, revision=project.envelope.revision + 1, data=data))
