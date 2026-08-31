"""Captured drafts and revision-checked commits through existing mutation ports."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from transbridge.application.contracts import RequestContext
from transbridge.application.io.identity import Provenance
from transbridge.application.io.mutation import ChangeSet, EntryPatch, EntrySnapshot, MutationStatus
from transbridge.converter.translation_entry import STAGE_TRANSLATED, STAGE_UNTRANSLATED, TranslationEntry
from transbridge.persistence.v2.ids import ProjectId, VariantId, VariantRef
from transbridge.ui.source_hydration import apply_variant_projection


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

    def commit(self, context, *, projection=None) -> str | None:
        """Return an actionable failure, retaining this draft on every failed commit."""
        if content_scope(context) != self.scope:
            return "工程、版本或翻译内容已切换。请返回原内容再应用此草稿。"
        collection = context.collection
        current = None if collection is None else collection.get(self.before.entry_key)
        if current is None or current.snapshot() != self.before:
            return "此词条已被其他操作修改或移除。草稿已保留；请复制草稿并重新载入词条后核对。"
        if not self.changed:
            return None
        stage = STAGE_TRANSLATED if self.text and current.stage == STAGE_UNTRANSLATED else current.stage
        if context.uses_authoritative_projection:
            version = context.active_version_identity
            if (
                context.project_commands is None
                or context.runtime_context is None
                or version is None
                or projection is None
            ):
                return "工程写入服务不可用，草稿已保留。请重新打开工程后再试。"
            snapshot = projection.snapshot()
            states = () if snapshot is None else snapshot.to_dict()["values"].get("entries", ())
            authoritative = apply_variant_projection(collection, states).get(current.identity)
            if authoritative is None or authoritative.snapshot() != self.before:
                return "工程中的词条已变化，草稿未覆盖新内容。请重新载入词条后核对。"
            result = context.project_commands.replace_entry_states(
                {current.identity: (self.text, stage)},
                context.runtime_context,
                expected_project_revision=context.project_revision,
                expected_variant_revision=context.variant_revision,
                expected_variant_ref=VariantRef(VariantId(version[1]), ProjectId(version[0])),
            )
            if not result.is_success:
                return result.diagnostics[0].message if result.diagnostics else "工程提交失败，草稿已保留。"
            if content_scope(context) == self.scope:
                snapshot = projection.snapshot()
                states = snapshot.to_dict()["values"].get("entries", ())
                for slot in context.slots.values():
                    slot.collection = apply_variant_projection(slot.collection, states)
                context.collection_changed.emit(context.collection)
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
                    (EntryPatch.create(current.identity, translation=self.text, stage=stage),),
                    ((current.identity, current.revision),),
                    Provenance(run_id, request.owner_id, "ui.dialogue-editor"),
                ),
                request,
            )
            if result.status is not MutationStatus.APPLIED:
                return result.diagnostics[0].message
            context.mark_dirty()
            context.collection_changed.emit(collection)
        return None
