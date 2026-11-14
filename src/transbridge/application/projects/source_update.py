# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Explicit, owner-bound source updates across every saved translation variant."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import secrets
from threading import RLock

from transbridge.application.contracts import DomainError, ErrorCategory, OperationResult
from transbridge.application.io.text_source_options import snapshot_read_options
from transbridge.config.text_encoding import TextEncodings
from transbridge.persistence.v2.ids import ProjectId, ProjectRef
from transbridge.persistence.v2.models import ProjectDto

from .provisioning import ProjectSourceRequest, translation_registry
from .source_content import authoritative_baseline_sources, folded_plugin_pairs, source_content_identity
from .source_encoding import TEXT_ENCODING_FORMATS, with_source_read_options
from .source_input_publication import PreparedSourcePublication, check_source_update_cancelled, publish_source_input
from .source_registry import SourceRegistrySnapshot, migrate_legacy_source_registry
from .source_update_migration import compare_source, migrate_variant
from .source_update_models import SourceUpdatePreview, SourceUpdateTarget
from .text_encoding_matching import match_encoding_entries


class ProjectSourceUpdateService:
    def __init__(self, lifecycle, projects, store, baselines, preparer):
        self._lifecycle = lifecycle
        self._projects = projects
        self._store = store
        self._baselines = baselines
        self._preparer = preparer
        self._pending: dict[str, PreparedSourcePublication] = {}
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
            project, paths = self._store.read_source_references(ref)
            registry = _registry(project)
            return OperationResult.completed(
                tuple(
                    SourceUpdateTarget(
                        item.source_id,
                        paths.get(source_content_identity(item.to_dict())) or item.location,
                        item.format_id,
                        item.display_name or Path(item.location).name,
                    )
                    for item in _update_targets(registry)
                ),
                run_id=context.run_id,
            )
        except Exception as exc:
            return OperationResult.from_exception(exc, run_id=context.run_id)

    def prepare(self, project_path, source_id, replacement_path, context, *, cancelled=None):
        try:
            check_source_update_cancelled(cancelled)
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
            if source not in _update_targets(registry):
                raise ValueError("所选来源不是独立的工程源文件，请更新其对应的原始来源。")
            path = str(Path(replacement_path).resolve(strict=True))
            old = self._store.previous(ref, source)
            options = dict(source.format_options)
            localized_language = dict(old.hydration.source_snapshot.metadata).get("localized_language")
            if localized_language is not None:
                options.setdefault("language", localized_language)
            request = ProjectSourceRequest(path, source.format_id, options=tuple(options.items()))
            encodings = TextEncodings.from_project_data(expected.project.envelope.data)
            request = with_source_read_options(request, encodings)
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
            warnings = ()
            keys = {
                (item.entry_key.local_key, item.entry_key.original)
                for variant in expected.variants
                for item in variant.entries
                if item.entry_key.namespace == namespace
            }
            matched = _match_same_source_with_new_read_options(source, old, prepared, keys)
            changes = compare_source(keys, old, prepared, matched_keys=matched)
            variants = tuple(
                migrate_variant(item, namespace, prepared, changes, matched_keys=matched) for item in expected.variants
            )
            project = _updated_project(expected.project, registry, source, old, prepared)
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
            candidate = PreparedSourcePublication(
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
                old.hydration.reference_location or "",
            )
            check_source_update_cancelled(cancelled)
            with self._lock:
                self._pending[token] = candidate
            return OperationResult.completed(preview, run_id=context.run_id)
        except Exception as exc:
            return OperationResult.from_exception(exc, run_id=context.run_id)

    def commit(self, token, context, *, cancelled=None):
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
            return publish_source_input(
                candidate,
                token,
                context,
                lifecycle=self._lifecycle,
                store=self._store,
                baselines=self._baselines,
                preparer=self._preparer,
                cancelled=cancelled,
            )
        except Exception as exc:
            return OperationResult.from_exception(exc, run_id=context.run_id)

    def discard(self, token, context) -> None:
        with self._lock:
            candidate = self._pending.get(token)
            if candidate is not None and candidate.owner == context.owner_id:
                del self._pending[token]

    def _resolve(self, path, context):
        ref = self._store.resolve(path)
        self._check_context(ref, context)
        return ref

    @staticmethod
    def _check_context(ref, context):
        if context.project_id is not None and context.project_id != ref.identity.value:
            raise DomainError(ErrorCategory.PERMISSION, "PROJECT_CONTEXT_MISMATCH", "更新目标与请求工程不一致。")


def _match_same_source_with_new_read_options(source, previous, prepared, keys):
    before, after = previous.hydration, prepared.hydration
    if (
        source.format_id not in TEXT_ENCODING_FORMATS
        or before is None
        or after is None
        or before.fingerprint != after.fingerprint
        or _read_facts(before) == _read_facts(after)
    ):
        return None
    matched = match_encoding_entries(previous, prepared)
    if keys - matched.keys():
        raise DomainError(
            ErrorCategory.CONFLICT,
            "TEXT_ENCODING_ENTRY_MAPPING_UNSAFE",
            "无法通过来源定位对应所有已保存词条，请核对所选来源及配套 Strings。",
        )
    return matched


def _read_facts(hydration):
    snapshot = hydration.source_snapshot
    return snapshot_read_options(snapshot), dict(snapshot.metadata).get("localized_language")


def _update_targets(registry):
    sources = authoritative_baseline_sources(
        tuple(item.to_dict() for item in registry.sources), tuple(item.to_dict() for item in registry.relations)
    )
    ids = {item["source_id"] for item in sources}
    return tuple(item for item in registry.sources if item.source_id in ids)


def _registry(project):
    data = project.envelope.data
    try:
        return SourceRegistrySnapshot.from_project_data(data)
    except (KeyError, TypeError, ValueError):
        return migrate_legacy_source_registry(project.envelope.identity, data.get("sources", ()))


def _updated_project(project, registry, source, previous, prepared):
    registry = updated_registry(project, registry, source, previous, prepared)
    data = deepcopy(project.envelope.data)
    data.update(translation_registry(registry).to_project_data())
    return ProjectDto(replace(project.envelope, revision=project.envelope.revision + 1, data=data))


def updated_registry(project, registry, source, previous, prepared):
    registration = migrate_legacy_source_registry(project.envelope.identity, (prepared.to_dict(),)).sources[0]
    old_name = previous.hydration.source_snapshot.source.display_name if previous.hydration is not None else None
    custom_name = source.display_name if source.display_name != old_name else None
    updated = replace(
        registration,
        source_id=source.source_id,
        display_name=custom_name or registration.display_name,
        enabled=source.enabled,
        plugin_scope=source.plugin_scope,
    )
    sources = tuple(updated if item.source_id == source.source_id else item for item in registry.sources)
    pairs = folded_plugin_pairs(
        tuple(item.to_dict() for item in registry.sources), tuple(item.to_dict() for item in registry.relations)
    )
    folded_ids = {pair.translation["source_id"] for pair in pairs if pair.primary["source_id"] == source.source_id}
    relations = tuple(
        replace(item, folded_into_baseline=True) if item.from_source_id in folded_ids else item
        for item in registry.relations
    )
    return SourceRegistrySnapshot(sources, relations, registry.diagnostics)
