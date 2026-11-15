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
from .source_input_publication import (
    PreparedSourceInput,
    PreparedSourcePublication,
    check_source_update_cancelled,
    parse_source_update_input,
    publish_source_input,
)
from .source_registry import SourceRegistrySnapshot, migrate_legacy_source_registry
from .source_update_migration import SourceMigration, compare_source, migrate_variant
from .source_update_models import (
    BatchSourceUpdatePreview,
    SourceUpdateDifference,
    SourceUpdatePreview,
    SourceUpdateTarget,
)
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
        """Keep the single-source preview contract for existing callers."""
        result = self.prepare_batch(project_path, ((source_id, replacement_path),), context, cancelled=cancelled)
        if not result.is_success or result.value is None:
            return result
        batch = result.value
        (item,) = batch.differences
        preview = SourceUpdatePreview(
            batch.token,
            item.source_name,
            item.replacement_path,
            batch.variant_count,
            item.added,
            item.removed,
            item.unchanged,
            item.changed,
            item.unverified,
            item.reordered,
        )
        return replace(result, value=preview)

    def prepare_batch(self, project_path, replacements, context, *, cancelled=None):
        try:
            check_source_update_cancelled(cancelled)
            ref = self._resolve(project_path, context)
            generation = self._lifecycle.generation
            active = self._lifecycle.active
            if active is not None and active.project_ref == ref and active.dirty:
                raise ValueError("请先保存当前工程的修改，再更新源文件。")
            expected = self._store.read(ref)
            registry = _registry(expected.project)
            selections = _selected_sources(registry, replacements)
            previous = self._store.previous_sources(ref, tuple(source for source, _ in selections))
            encodings = TextEncodings.from_project_data(expected.project.envelope.data)
            keys_by_namespace = {item.baseline.fingerprint.namespace: set() for item in previous}
            for variant in expected.variants:
                for entry in variant.entries:
                    key = entry.entry_key
                    if key.namespace in keys_by_namespace:
                        keys_by_namespace[key.namespace].add((key.local_key, key.original))
            inputs = []
            migrations = []
            differences = []
            for (source, path), old in zip(selections, previous, strict=True):
                check_source_update_cancelled(cancelled)
                request = _source_request(source, path, old, encodings)
                prepared = parse_source_update_input(self._preparer, request, context)
                namespace = old.baseline.fingerprint.namespace
                migration, difference = _compare_source(source, old, request, prepared, keys_by_namespace[namespace])
                registry = updated_registry(expected.project, registry, source, old, prepared)
                inputs.append(
                    PreparedSourceInput(
                        replace(request, expected_fingerprint=prepared.baseline.fingerprint.sha256),
                        prepared,
                        namespace,
                        old.hydration.reference_location or "",
                    )
                )
                migrations.append(migration)
                differences.append(difference)
            migrations = tuple(migrations)
            variants = tuple(migrate_variant(variant, migrations) for variant in expected.variants)
            token = secrets.token_hex(24)
            preview = BatchSourceUpdatePreview(token, len(variants), tuple(differences))
            candidate = PreparedSourcePublication(
                context.owner_id,
                generation,
                expected,
                _updated_project(expected.project, registry),
                variants,
                tuple(inputs),
                str(self._projects.path_for(ref)),
                differences[0].source_name if len(differences) == 1 else f"{len(differences)} 个来源",
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
                recover_replacement=True,
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


def _selected_sources(registry, replacements):
    replacements = tuple(replacements)
    if not replacements:
        raise ValueError("请至少选择一个要更新的来源。")
    if len({source_id for source_id, _ in replacements}) != len(replacements):
        raise ValueError("同一个工程来源只能选择一个新版文件。")
    targets = {item.source_id: item for item in _update_targets(registry)}
    selected = []
    for source_id, replacement in replacements:
        source = targets.get(source_id)
        if source is None:
            raise ValueError("工程中找不到所选独立来源，请重新选择。")
        try:
            selected.append((source, Path(replacement).resolve(strict=True)))
        except FileNotFoundError as exc:
            raise DomainError(
                ErrorCategory.PREREQUISITE,
                "RESOURCE_NOT_FOUND",
                f"找不到所选新版文件，请重新选择。\n{replacement}",
                cause=exc,
            ) from exc
    if len({path for _, path in selected}) != len(selected):
        raise ValueError("同一个新版文件只能配对一个工程来源。")
    return selected


def _source_request(source, path, old, encodings):
    options = dict(source.format_options)
    localized_language = dict(old.hydration.source_snapshot.metadata).get("localized_language")
    if localized_language is not None:
        options.setdefault("language", localized_language)
    request = ProjectSourceRequest(str(path), source.format_id, options=tuple(options.items()))
    return with_source_read_options(request, encodings)


def _compare_source(source, old, request, prepared, keys):
    matched = _match_same_source_with_new_read_options(source, old, prepared, keys)
    changes = compare_source(keys, old, prepared, matched_keys=matched)
    return SourceMigration(old.baseline.fingerprint, prepared, changes, matched), SourceUpdateDifference(
        source.display_name or Path(source.location).name,
        old.hydration.reference_location or source.location,
        request.location,
        len(changes.added),
        len(changes.removed),
        len(changes.unchanged),
        len(changes.changed),
        len(changes.unverified),
        len(changes.reordered),
    )


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


def _updated_project(project, registry):
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
