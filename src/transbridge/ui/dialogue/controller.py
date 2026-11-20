# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Own the entry editor window, optional task context, and draft lifetime."""

from __future__ import annotations

import logging

from PyQt6.QtCore import QEvent, QObject
from PyQt6.QtWidgets import QMessageBox

from transbridge.application.dialogue.index import (
    CONTEXT_RECORD_TYPES,
    DialogueIndex,
    build_dialogue_index,
    record_type,
    source_unavailable_reason,
)
from transbridge.application.dialogue.loading import DialogueIndexLoader
from transbridge.application.dialogue.record_fields import record_identity
from transbridge.ui.project_rows import ProjectRowsCollection
from transbridge.ui.windowing import show_and_activate

from .consistency import synchronize_translation
from .dialog import EntryEditorDialog
from .editing import EntryDraft, content_scope
from .index_delivery import IndexDelivery
from .index_state import IndexStructure
from .record_browser import RecordBrowser


def unavailable_reason(context) -> str | None:
    """Explain why the optional task tree is unavailable, not the text editor."""
    slot = context.active_slot
    if slot is None or context.collection is None:
        return "请先加载翻译内容。"
    format_id = slot.format_id
    for source in context.project_sources:
        if source.get("location") == context.active_key or source.get("source_id") == context.active_key:
            format_id = source.get("format_id") or format_id
            break
    return source_unavailable_reason(format_id=format_id, esp_path=slot.esp_path, eet_path=slot.eet_path)


class DialogueEditorController(QObject):
    def __init__(self, context, parent, preview, workers: list, *, projection=None) -> None:
        super().__init__(parent)
        self.context, self.preview = context, preview
        self._workers = workers
        self._projection = projection
        self._index_structure = IndexStructure()
        self._generation = 0
        self._closed = False
        self._index = DialogueIndex((), {})
        self._loader = DialogueIndexLoader()
        self._delivery = None
        self._node_identity = None
        self._scope = None
        self._quest = self._topic = self._row = 0
        self._drafts: dict[tuple, EntryDraft] = {}
        self._current: EntryDraft | None = None
        self._selected_key = None
        self._entry_keys = self._navigation_keys = ()
        self._session_keys = ()
        self._context_reason = ""
        self.dialog = EntryEditorDialog(parent, can_close=self.can_close)
        self.view = self.dialog.view
        self._records = RecordBrowser(self.view, workers)
        self._records.entry_requested.connect(self._display_entry)
        self._records.changed.connect(self._redisplay)
        self.view.quest_selected.connect(self.select_quest)
        self.view.topic_selected.connect(self.select_topic)
        self.view.entry_selected.connect(self.select_entry)
        self.view.draft_changed.connect(self.edit_text)
        self.view.apply_requested.connect(self.apply)
        self.view.discard_requested.connect(self.discard)
        self.view.move_requested.connect(self.move)
        self.dialog.dismissed.connect(self._dismissed)
        preview.entry_edit_requested.connect(self.open_entry)
        preview.installEventFilter(self)
        context.collection_changed.connect(self.refresh)
        context.variant_changed.connect(self._version_changed)
        self.refresh(context.collection)

    def eventFilter(self, watched, event):  # noqa: N802 - Qt override
        if watched is self.preview and event.type() == QEvent.Type.EnabledChange:
            self.view.setEnabled(self.preview.isEnabled())
        return super().eventFilter(watched, event)

    def _version_changed(self, _variant) -> None:
        # Do not expose the old source under a newly activated Variant.
        self._cancel_index()
        self._generation += 1
        self._scope = None
        self._index_structure = IndexStructure()
        self._index = DialogueIndex((), {})
        self.preview.set_editable_entry_keys(())
        self._clear_selection("版本已切换，等待加载翻译内容。")

    def _clear_selection(self, message: str) -> None:
        self._records.reset()
        self._current = self._selected_key = None
        self._node_identity = None
        self._navigation_keys = ()
        self._session_keys = ()
        self.view.set_context_available(False)
        self.view.show_entries((), -1)
        self.view.show_entry(None, "")
        self.view.body.setEnabled(False)
        self.view.show_message(message)

    def refresh(self, _collection=None) -> None:
        if self._closed:
            return
        scope = content_scope(self.context)
        if scope != self._scope:
            self._clear_selection("内容已切换，请双击要编辑的词条。")
        self._scope = scope
        collection = self.context.collection
        slot = self.context.active_slot
        reason = unavailable_reason(self.context)
        if self._index_structure.source_changed(scope, slot, reason):
            self._cancel_index()
            self._generation += 1
            self._index = DialogueIndex((), {})
            self._entry_keys = ()
            self._context_reason = reason or "正在建立任务与话题索引…"
        self.preview.set_editable_entry_keys(() if collection is None else collection)
        self.view.source_label.setText("" if slot is None else f"当前内容：{slot.label}")
        self.dialog.setWindowTitle("词条编辑" if slot is None else f"词条编辑 — {slot.label}")
        if not collection:
            self._clear_selection("当前内容没有可编辑的词条。")
        # Popup activation only needs identity membership. Defer relationship
        # snapshots, navigation keys and model population until the editor is used.
        if not self.dialog.isVisible():
            return
        self._prepare_context()

    def _prepare_context(self) -> None:
        collection = self.context.collection
        paged = isinstance(collection, ProjectRowsCollection)
        reason = unavailable_reason(self.context)
        selected = None if collection is None or self._selected_key is None else collection.get(self._selected_key)
        task_entry = selected is not None and record_type(selected) in CONTEXT_RECORD_TYPES and not reason
        if selected is not None and not reason and not task_entry and record_identity(selected) is not None:
            # Record fields can be read independently of the quest/scene directory.
            self._session_keys = self._records.refresh_opening_keys(collection, self._session_keys, self.preview)
            self._display_entry(selected.identity)
            return
        rebuild = collection is not None and self._index_structure.changed(collection)
        if rebuild:
            self._cancel_index()
            self._generation += 1
            self._index = DialogueIndex((), {})
            self._context_reason = reason or "正在建立任务与话题索引…"
            self._entry_keys = self._index_structure.keys
            if paged:
                removed = self._index_structure.removed_keys
                if removed is None:
                    self._session_keys = () if task_entry else self.preview.editor_navigation_keys()
                elif removed:
                    self._session_keys = tuple(key for key in self._session_keys if key not in removed)
            else:
                self._session_keys = tuple(key for key in self._session_keys if key in collection)
        if (
            paged
            and task_entry
            and (rebuild or self._delivery is not None)
            and selected.identity not in self._index.locations
        ):
            # Only cached pages and the clicked row participate in this first frame.
            # The revision-bound full-source iterator remains owned by the worker.
            available = {entry.identity: entry for entry in collection.visible_rows()}
            available[selected.identity] = selected
            self._index = build_dialogue_index(available.values())
            self._context_reason = "正在补全话题与场景记录…"
        if selected is not None:
            self._display_entry(self._selected_key)
        elif collection and self._node_identity is not None:
            location = self._selected_location()
            if not rebuild and location is not None:
                self._show_context(location)
            else:
                self.view.set_context_available(False, self._context_reason)
        else:
            self._clear_selection("请双击要编辑的词条。" if collection else "当前内容没有可编辑的词条。")
        if not rebuild or not collection or reason:
            return
        generation, scope = self._generation, self._scope
        slot = self.context.active_slot
        # Paged iteration captures the current revision before handing the read to the worker.
        entries = iter(collection) if paged else tuple(collection)
        self._delivery = IndexDelivery(
            self._loader,
            entries,
            self,
            self._workers,
            generation,
            scope,
            plugin=slot.plugin,
            snapshot=slot.source_snapshot,
            reading_entries=getattr(slot, "reading_source_entries", None),
            load_snapshot=collection.freeze_native_template() if paged else None,
        )

    def _cancel_index(self):
        if self._delivery is not None:
            self._delivery.cancel()
            self._delivery = None

    def _loaded(self, generation, scope, index, *, complete=True) -> None:
        if self._closed or generation != self._generation or scope != content_scope(self.context):
            return
        if not complete and self._node_identity is not None:
            # A metadata preview cannot replace a selected scene from the full catalog.
            return
        self._index = index
        self._context_reason = "" if complete else "正在补全话题与场景记录…"
        if self._selected_key is not None:
            self._display_entry(self._selected_key)
        elif (location := self._selected_location()) is not None:
            self._show_context(location)
        elif self._node_identity is not None:
            self._clear_selection("记录已被移除，请重新选择词条。")

    def _failed(self, generation, message) -> None:
        if not self._closed and generation == self._generation:
            self._index_structure = IndexStructure()
            self._index_structure.source_changed(
                self._scope, self.context.active_slot, unavailable_reason(self.context)
            )
            self._context_reason = f"任务索引建立失败：{message}。可继续编辑译文，重新选择内容后重试任务树。"
            if self._selected_key is not None:
                self._display_entry(self._selected_key)
            else:
                self.view.set_context_available(False, self._context_reason)

    def open_entry(self, key) -> None:
        if self._closed or not self.preview.isEnabled() or self._scope != content_scope(self.context):
            return
        collection = self.context.collection
        entry = None if collection is None else collection.get(key)
        if entry is None:
            return
        self._records.reset()
        self._records.bind_collection(collection)
        if record_type(entry) == "QUST":
            self._records.preferred = "record"
        self._node_identity = None
        self._selected_key = key
        task_entry = (
            isinstance(collection, ProjectRowsCollection)
            and unavailable_reason(self.context) is None
            and record_type(entry) in CONTEXT_RECORD_TYPES
        )
        self._session_keys = () if task_entry else self.preview.editor_navigation_keys()
        self._prepare_context()
        show_and_activate(self.dialog)
        self.view.translation.setFocus()

    def _redisplay(self) -> None:
        if not self._closed and self.dialog.isVisible() and self._selected_key is not None:
            self._display_entry(self._selected_key)

    def _display_entry(self, key) -> None:
        collection = self.context.collection
        entry = None if collection is None else collection.get(key)
        if entry is None:
            self._clear_selection("词条已被移除，请重新选择。")
            return
        self.view.body.setEnabled(True)
        self.view.show_message("")
        location = self._selected_location(key) or self._index.locations.get(key)
        plugin = unavailable_reason(self.context) is None
        record = record_identity(entry) if plugin else None
        self.view.set_record_available(record is not None)
        if record is not None and (self._records.preferred == "record" or location is None):
            task_record = record.signature in CONTEXT_RECORD_TYPES
            self.view.set_context_available(
                location is not None,
                self._context_reason if task_record else "",
                show_reason=task_record and location is None,
            )
            keys = self._records.present(collection, entry, record, self._scope)
            self._navigation_keys = keys if len(keys) > 1 or key not in self._session_keys else self._session_keys
            self._edit_entry(entry, record_scope=len(keys) > 1)
            if self._records.pending:
                self.view.previous_button.setEnabled(False)
                self.view.next_button.setEnabled(False)
                self.view.apply_next_button.setEnabled(False)
        elif location is not None and plugin:
            self._show_context(location)
        else:
            self._navigation_keys = self._session_keys or self._entry_keys
            if key not in self._navigation_keys:
                self._navigation_keys = (key,)
            self.view.set_context_available(False, self._context_reason)
            self.view.show_entries((entry,), 0)
            self.select_entry(0)

    def _selected_location(self, key=None):
        if self._node_identity is None:
            return None
        for quest_row, quest in enumerate(self._index.quests):
            for topic_row, topic in enumerate(quest.topics):
                if topic.identity == self._node_identity and (key is None or key in topic.entries):
                    return quest_row, topic_row, 0 if key is None else topic.entries.index(key)
        return None

    def _show_context(self, location) -> None:
        quest, topic, row = location
        self.view.body.setEnabled(True)
        self.view.set_context_available(True, self._context_reason)
        self.view.set_browse_mode("dialogue")
        self.view.show_quests(self._index, quest)
        self.select_quest(quest, topic, row)

    def select_quest(self, quest: int, topic: int = 0, row: int = 0) -> None:
        if not 0 <= quest < len(self._index.quests) or unavailable_reason(self.context):
            return
        self._quest = quest
        selected = self._index.quests[quest]
        self.view.show_quest(selected, topic)
        self.select_topic(topic, row)

    def select_topic(self, topic: int, row: int = 0) -> None:
        if not 0 <= self._quest < len(self._index.quests) or unavailable_reason(self.context):
            return
        quest = self._index.quests[self._quest]
        if not 0 <= topic < len(quest.topics):
            return
        self._topic = topic
        selected = quest.topics[topic]
        self._node_identity = selected.identity
        self._navigation_keys = (
            selected.entries
            if selected.kind == "SCEN"
            else tuple(dict.fromkeys(key for item in quest.topics if item.kind != "SCEN" for key in item.entries))
        )
        collection = self.context.collection
        entries = tuple(entry for key in selected.entries if (entry := collection.get(key)) is not None)
        self.view.show_entries(entries, row)
        if entries:
            self.view.show_message(f"{selected.label} · {len(entries)} 条关联词条")
            self.select_entry(row)
        else:
            self._current = self._selected_key = None
            self.view.set_record_available(False)
            self.view.show_entry(None, "")
            self._show_draft_state()
            self.view.show_message(f"{selected.label} 没有关联的可翻译词条。")

    def select_entry(self, row: int) -> None:
        entries = self.view.table_model.entries
        if not 0 <= row < len(entries):
            return
        self._row = row
        entry = entries[row]
        self.view.set_record_available(unavailable_reason(self.context) is None and record_identity(entry) is not None)
        self._edit_entry(entry)

    def _edit_entry(self, entry, *, record_scope=False) -> None:
        self._selected_key = entry.identity
        self._current = self._drafts.get((self._scope, entry.identity)) or EntryDraft.capture(self.context, entry)
        self.view.show_entry(entry, self._current.text)
        self._show_draft_state()
        position = self._navigation_keys.index(entry.identity)
        self.view.previous_button.setEnabled(position > 0)
        self.view.next_button.setEnabled(position < len(self._navigation_keys) - 1)
        self.view.apply_next_button.setEnabled(True)
        self.view.show_navigation(
            position,
            len(self._navigation_keys),
            self.view.browsing_mode == "dialogue" and self.view.context_panel.isEnabled(),
            scope="当前记录" if record_scope else None,
        )

    def edit_text(self, text: str) -> None:
        if self._current is None:
            return
        self._current.text = text
        key = (self._current.scope, self._current.before.entry_key)
        if self._current.changed:
            self._drafts[key] = self._current
        else:
            self._drafts.pop(key, None)
        self._show_draft_state()

    def _show_draft_state(self) -> None:
        self.view.show_draft_state(bool(self._current and self._current.changed), len(self._drafts))

    def move(self, delta: int) -> None:
        if self._current is None:
            return
        position = self._navigation_keys.index(self._selected_key) + delta
        if 0 <= position < len(self._navigation_keys):
            self._display_entry(self._navigation_keys[position])
            self.view.translation.setFocus()

    def apply(self, advance: bool = False) -> None:
        draft = self._current
        if draft is None or not self.preview.isEnabled():
            return
        position = self._navigation_keys.index(self._selected_key)
        next_key = (
            self._navigation_keys[position + 1] if advance and position + 1 < len(self._navigation_keys) else None
        )
        changed = draft.changed
        try:
            error = draft.commit(self.context, projection=self._projection)
        except Exception as exc:  # GUI boundary: retain the draft and report the original failure.
            logging.getLogger(__name__).exception("Entry draft commit failed")
            error = f"应用译文失败：{exc}。草稿已保留。"
        if error:
            self.view.show_message(error)
            return
        self._drafts.pop((draft.scope, draft.before.entry_key), None)
        if content_scope(self.context) != draft.scope:
            return
        self._display_entry(draft.before.entry_key)
        if changed:
            try:
                error = synchronize_translation(
                    self.context,
                    draft.before.entry_key,
                    draft.text,
                    self._drafts,
                    self.dialog,
                    projection=self._projection,
                )
            except Exception as exc:
                logging.getLogger(__name__).exception("Translation synchronization failed")
                error = f"当前译文已应用，但同步失败：{exc}。请核对后重试。"
            if error:
                self.view.show_message(error)
                self.view.translation.setFocus()
                return
        if content_scope(self.context) != draft.scope:
            return
        if next_key is not None:
            self._display_entry(next_key)
            self.view.translation.setFocus()
        elif self.dialog.close():
            self.preview.return_from_editor(draft.before.entry_key)
        else:
            self.view.translation.setFocus()

    def discard(self) -> None:
        if self._current is None:
            return
        key = self._current.before.entry_key
        self._drafts.pop((self._current.scope, key), None)
        self._display_entry(key)

    def can_close(self) -> bool:
        if not self._drafts:
            return True
        decision = QMessageBox.warning(
            self.dialog,
            "仍有未应用的译文草稿",
            f"有 {len(self._drafts)} 条草稿尚未应用到工程。关闭将丢弃这些草稿。\n"
            "选择取消可返回编辑，已应用的译文仍由工程保存。",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if decision != QMessageBox.StandardButton.Discard:
            return False
        return True

    def _dismissed(self) -> None:
        self._drafts.clear()
        self._clear_selection("请双击要编辑的词条。")

    def close(self) -> None:
        if self._closed:
            return
        self._cancel_index()
        self._closed = True
        self._records.reset()
        self._generation += 1
        self.dialog.hide()
        self.context.collection_changed.disconnect(self.refresh)
        self.context.variant_changed.disconnect(self._version_changed)
        self.preview.entry_edit_requested.disconnect(self.open_entry)
        self.preview.removeEventFilter(self)
