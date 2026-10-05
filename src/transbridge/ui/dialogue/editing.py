"""Captured drafts and revision-checked commits through existing mutation ports."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from transbridge.application.contracts import RequestContext
from transbridge.application.io.identity import Provenance
from transbridge.application.io.mutation import ChangeSet, EntryPatch, EntrySnapshot, MutationStatus
from transbridge.converter.translation_entry import STAGE_TRANSLATED, STAGE_UNTRANSLATED, TranslationEntry
from transbridge.persistence.v2.ids import ProjectId, VariantId, VariantRef
from transbridge.ui.entry_projection import EntryProjectionUpdate


def content_scope(context) -> tuple:
    return context.active_version_identity, context.active_key


@dataclass
class EntryDraft:
    scope: tuple
    before: EntrySnapshot
    text: str

    @classmethod
    def capture(cls, context, entry: TranslationEntry) -> EntryDraft:
        return cls(content_scope(context), entry.snapshot(), entry.translation)

    @property
    def changed(self) -> bool:
        return self.text != self.before.translation

    def commit(self, context, *, projection=None, projection_sync=None) -> str | None:
        """Return an actionable failure, retaining this draft on every failed commit."""
        return commit_drafts(context, (self,), projection=projection, projection_sync=projection_sync)


def commit_drafts(context, drafts, *, projection=None, projection_sync=None) -> str | None:
    """Commit captured entries together, validating every snapshot before any mutation."""
    drafts = tuple(drafts)
    if not drafts:
        return None
    scope = drafts[0].scope
    collection = context.collection
    for draft in drafts:
        if content_scope(context) != draft.scope:
            return "工程、版本或翻译内容已切换。请返回原内容再应用此草稿。"
        current = None if collection is None else collection.get(draft.before.entry_key)
        if current is None or current.snapshot() != draft.before:
            return "此词条已被其他操作修改或移除。草稿已保留；请复制草稿并重新载入词条后核对。"
    changed = tuple(draft for draft in drafts if draft.changed)
    if not changed:
        return None
    states_to_write = {
        draft.before.entry_key: (
            draft.text,
            STAGE_TRANSLATED if draft.text and draft.before.stage == STAGE_UNTRANSLATED else draft.before.stage,
        )
        for draft in changed
    }
    if context.uses_authoritative_projection:
        version = context.active_version_identity
        if context.project_commands is None or context.runtime_context is None or version is None or projection is None:
            return "工程写入服务不可用，草稿已保留。请重新打开工程后再试。"
        snapshot = projection.snapshot()
        if snapshot is None:
            return "工程权威快照不可用，草稿已保留。请重新加载工程后重试。"
        authoritative = EntryProjectionUpdate.from_snapshot(snapshot, keys=(draft.before.entry_key for draft in drafts))
        if any(
            authoritative.entry(collection.get(draft.before.entry_key)).snapshot() != draft.before for draft in drafts
        ):
            return "工程中的词条已变化，草稿未覆盖新内容。请重新载入词条后核对。"
        result = context.project_commands.replace_entry_states(
            states_to_write,
            context.runtime_context,
            expected_project_revision=context.project_revision,
            expected_variant_revision=context.variant_revision,
            expected_variant_ref=VariantRef(VariantId(version[1]), ProjectId(version[0])),
        )
        if not result.is_success:
            return result.diagnostics[0].message if result.diagnostics else "工程提交失败，草稿已保留。"
        if content_scope(context) == scope:
            snapshot = projection.snapshot()
            if snapshot is None:
                return "译文已提交，但工程权威快照不可用。请重新加载工程后核对译文。"
            update = EntryProjectionUpdate.from_snapshot(snapshot)
            for slot in context.slots.values():
                slot.collection = update.collection(slot.collection)
            if projection_sync is None:
                context.collection_changed.emit(context.collection)
            else:
                projection_sync.publish_applied(context, snapshot)
    else:
        run_id = f"dialogue-edit-{uuid4().hex}"
        request = RequestContext(
            "ui.dialogue-editor",
            run_id=run_id,
            permissions=frozenset({"entry.translation.write", "entry.stage.write"}),
        )
        result = collection.apply(
            ChangeSet(
                run_id,
                tuple(
                    EntryPatch.create(key, translation=text, stage=stage)
                    for key, (text, stage) in states_to_write.items()
                ),
                tuple((draft.before.entry_key, draft.before.revision) for draft in changed),
                Provenance(run_id, request.owner_id, "ui.dialogue-editor"),
            ),
            request,
        )
        if result.status is not MutationStatus.APPLIED:
            return result.diagnostics[0].message
        context.mark_dirty()
        context.collection_changed.emit(collection)
    return None
