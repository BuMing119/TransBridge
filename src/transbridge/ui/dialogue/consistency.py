"""Offer scoped, optimistic and atomic synchronization after an entry is applied."""

from __future__ import annotations

from dataclasses import dataclass
import logging

from transbridge.converter.translation_entry import STAGE_CHECKED, STAGE_HIDDEN, STAGE_LOCKED

from .editing import EntryDraft, commit_drafts, content_scope


@dataclass(frozen=True)
class SyncCandidate:
    draft: EntryDraft
    key: str
    protected_reason: str = ""

    @property
    def selected_by_default(self) -> bool:
        return not self.protected_reason and self.draft.before.stage < STAGE_CHECKED


def sync_candidates(context, entry_key, text: str, drafts) -> tuple[SyncCandidate, ...]:
    collection = context.collection
    source = None if collection is None else collection.get(entry_key)
    if source is None or not source.original or not text:
        return ()
    scope = content_scope(context)
    candidates = []
    for entry in collection:
        if entry.identity == entry_key or entry.original != source.original or entry.translation == text:
            continue
        reason = ""
        if (scope, entry.identity) in drafts:
            reason = "有未应用草稿，已保护"
        elif entry.stage in (STAGE_LOCKED, STAGE_HIDDEN):
            reason = "已锁定或隐藏，已保护"
        draft = EntryDraft.capture(context, entry)
        draft.text = text
        candidates.append(SyncCandidate(draft, entry.key, reason))
    return tuple(candidates)


def synchronize_translation(
    context, entry_key, text, drafts, parent, *, projection=None, projection_sync=None
) -> str | None:
    """Return an error to keep the editor open; cancellation leaves the applied entry intact."""
    from .sync_dialog import SyncTranslationDialog

    candidates = sync_candidates(context, entry_key, text, drafts)
    if not candidates:
        return None
    source = EntryDraft.capture(context, context.collection.get(entry_key))
    dialog = SyncTranslationDialog(candidates, source.before.original, text, parent)
    if not dialog.exec():
        return None
    selected = dialog.selected_candidates()
    if not selected:
        return None
    if any((item.draft.scope, item.draft.before.entry_key) in drafts for item in selected):
        return "当前词条已应用；待同步词条出现未应用草稿，未执行批量同步。请核对后重试。"
    try:
        error = commit_drafts(
            context,
            (source, *(item.draft for item in selected)),
            projection=projection,
            projection_sync=projection_sync,
        )
    except Exception as exc:
        logging.getLogger(__name__).exception("Matching entry synchronization failed")
        error = str(exc)
    return None if error is None else f"当前词条已应用，但批量同步失败：{error}"
