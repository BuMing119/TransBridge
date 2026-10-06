"""One detached preparation and guarded publication path for translation imports."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
import logging
from pathlib import Path

from transbridge.application.io import legacy_migration, migration_import
from transbridge.application.io.migration_import import MigrationImportError
from transbridge.application.io.migration_snapshot import detached_entries
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.persistence.v2.ids import ProjectId, VariantId, VariantRef
from transbridge.ui.workers import ApiWorker

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Target:
    slot: object
    collection: TranslationEntryCollection
    revision: object
    entries: tuple
    paths: tuple
    strings_lookup: object

    @classmethod
    def capture(cls, slot):
        collection = slot.collection
        return cls(
            slot,
            collection,
            collection.collection_revision,
            detached_entries(collection),
            _paths(slot),
            getattr(slot, "strings_lookup", None),
        )

    def is_current(self, context):
        return (
            any(slot is self.slot for slot in context.slots.values())
            and self.slot.collection is self.collection
            and self.collection.collection_revision == self.revision
            and tuple(self.collection) == self.entries
            and _paths(self.slot) == self.paths
            and getattr(self.slot, "strings_lookup", None) is self.strings_lookup
        )


def _paths(slot):
    return tuple(
        getattr(slot, field, None) for field in ("esp_path", "eet_path", "xt_path", "strings_path", "strings_lang")
    )


def run_migration(host, slot, config) -> None:
    context = host.context
    cfg = deepcopy(config)
    runtime_context = context.runtime_context
    authoritative = context.uses_authoritative_projection
    authority = _authority(context)
    active_slot = context.active_slot
    apply_all = bool(cfg.strings_apply_all and cfg.strings_dir)
    slots = tuple(context.slots.values()) if apply_all else (slot,)
    targets = tuple(_Target.capture(value) for value in slots)
    sources = tuple(
        (path, hint)
        for path, hint in (
            (getattr(cfg, "json_path", None), getattr(cfg, "json_format_id", None)),
            (getattr(cfg, "sst_path", None), getattr(cfg, "sst_format_id", None)),
            (cfg.tp_path, "plugin.sse"),
        )
        if path
    )
    host.workbench.show_step2_progress(0, "验证迁移源中…")

    def prepare():
        prepared = []
        states = {}
        labels = []
        skipped = 0
        for target in targets:
            candidate = TranslationEntryCollection(target.entries)
            esp_path, eet_path, xt_path, strings_path, _language = target.paths
            eet_source = cfg.eet_path if target.slot is slot and eet_path is None else None
            xt_source = cfg.xt_path if target.slot is slot and xt_path is None else None
            strings_source = cfg.strings_dir if strings_path is None else None
            if eet_source or xt_source or strings_source:
                legacy = legacy_migration.prepare_legacy_migration(
                    candidate,
                    eet_path=eet_source,
                    xt_path=xt_source,
                    strings_dir=strings_source,
                    plugin_stem=Path(esp_path).stem if esp_path else "",
                    strings_lang=cfg.strings_lang,
                    context=runtime_context,
                )
            else:
                legacy = legacy_migration.LegacyMigrationDraft(candidate)
            candidate = legacy.collection
            if target.slot is slot and sources:
                candidate, formats, unmatched = _prepare_structured(sources, candidate, runtime_context)
                labels.extend(formats)
                skipped += unmatched
            previous = {entry.identity: (entry.translation, entry.stage) for entry in target.entries}
            changes = {
                entry.identity: (entry.translation, entry.stage)
                for entry in candidate
                if (entry.translation, entry.stage) != previous[entry.identity]
            }
            for key, state in changes.items():
                if key in states and states[key] != state:
                    raise MigrationImportError(
                        "MIGRATION_ENTRY_KEY_CONFLICT", f"多个来源的迁移结果冲突：{key.local_key}"
                    )
                states[key] = state
            prepared.append((target, candidate, changes, eet_source, xt_source, strings_source, legacy))
        return states, prepared, labels, skipped

    def publish(result):
        states, prepared, labels, skipped = result
        if (
            host.context is not context
            or context.active_slot is not active_slot
            or context.uses_authoritative_projection != authoritative
            or _authority(context) != authority
            or not all(target.is_current(context) for target in targets)
            or (apply_all and tuple(context.slots.values()) != slots)
        ):
            failed("MIGRATION_TARGET_CHANGED: 工程版本或翻译内容已变化，导入草稿未提交。")
            return
        if authoritative and states:
            if authority[0] is None:
                failed("ACTIVE_VARIANT_REQUIRED: 导入草稿没有绑定活动工程版本。")
                return
            (project_id, variant_id), project_revision, variant_revision = authority
            committed = context.project_commands.replace_entry_states(
                states,
                runtime_context,
                expected_project_revision=project_revision,
                expected_variant_revision=variant_revision,
                expected_variant_ref=VariantRef(VariantId(variant_id), ProjectId(project_id)),
            )
            if not committed.is_success:
                diagnostic = committed.diagnostics[0]
                failed(f"{diagnostic.code}: {diagnostic.message}")
                return
        updated = 0
        for target, candidate, changes, eet_source, xt_source, strings_source, legacy in prepared:
            if legacy.strings_lookup is not None:
                target.slot.strings_lookup = legacy.strings_lookup
            if changes:
                updated += 1
                target.slot.collection = candidate
            if legacy.applied_count:
                if eet_source:
                    target.slot.eet_path = eet_source
                if xt_source:
                    target.slot.xt_path = xt_source
                if strings_source:
                    target.slot.strings_path = strings_source
                    target.slot.strings_lang = cfg.strings_lang
            if changes and getattr(cfg, "sst_path", None) and target.slot is slot:
                target.slot.sst_path = cfg.sst_path
        host.workbench.hide_step2_progress()
        formats = f"（{' + '.join(labels)}）" if labels else ""
        scope = f"共 {updated} 个集合，" if apply_all and updated > 1 else ""
        suffix = f"；{skipped} 条无法唯一匹配已跳过" if skipped else ""
        host.show_message(f"迁移完成{formats}，{scope}新增 {len(states)} 条译文{suffix}")
        context.collection_changed.emit(slot.collection)

    def failed(message):
        host.workbench.hide_step2_progress()
        host.show_message(f"迁移失败：{message}")

    def completed(result):
        try:
            publish(result)
        except Exception as exc:
            logger.exception("Migration draft publication failed")
            failed(str(exc))

    worker = ApiWorker(prepare)
    worker.result.connect(completed)
    worker.error.connect(failed)
    host.workers.append(worker)
    worker.start()


def _authority(context):
    return (
        getattr(context, "active_version_identity", None),
        getattr(context, "project_revision", None),
        getattr(context, "variant_revision", None),
    )


def _prepare_structured(sources, target, context):
    proposals = {}
    formats = []
    skipped = 0
    for path, hint in sources:
        draft = migration_import.prepare_migration_import(path, target, format_hint=hint, context=context)
        formats.append(draft.format_id.value)
        skipped += draft.skipped_unmatched + draft.skipped_conflicts
        for key, state in draft.states:
            if key in proposals and proposals[key] != state:
                raise MigrationImportError("MIGRATION_ENTRY_KEY_CONFLICT", f"多个迁移源提供了不同译文：{key.local_key}")
            proposals[key] = state
    return (
        TranslationEntryCollection(
            replace(
                entry,
                translation=proposals[entry.identity][0],
                stage=proposals[entry.identity][1],
                revision=entry.revision.next(),
            )
            if entry.identity in proposals and not entry.translation and entry.stage == 0
            else entry
            for entry in target
        ),
        formats,
        skipped,
    )
