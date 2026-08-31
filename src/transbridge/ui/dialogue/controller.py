"""Own the entry editor window, optional task context, and draft lifetime."""

from __future__ import annotations

import logging

from PyQt6.QtCore import QEvent, QObject
from PyQt6.QtWidgets import QMessageBox

from transbridge.application.dialogue.index import DialogueIndex, source_unavailable_reason
from transbridge.application.dialogue.loading import DialogueIndexLoader
from transbridge.ui.source_hydration import apply_variant_projection
from transbridge.ui.windowing import show_and_activate
from transbridge.ui.workers import ApiWorker

from .dialog import EntryEditorDialog
from .editing import EntryDraft, content_scope


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
        self._generation = 0
        self._closed = False
        self._index = DialogueIndex((), {})
        self._loader = DialogueIndexLoader()
        self._node_identity = None
        self._scope = None
        self._quest = self._topic = self._row = 0
        self._drafts: dict[tuple, EntryDraft] = {}
        self._current: EntryDraft | None = None
        self._selected_key = None
        self._entry_keys = self._navigation_keys = ()
        self._context_reason = ""
        self.dialog = EntryEditorDialog(parent, can_close=self.can_close)
        self.view = self.dialog.view
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
        self._generation += 1
        self._scope = None
        self._index = DialogueIndex((), {})
        self.preview.set_editable_entry_keys(())
        self._clear_selection("版本已切换，等待加载翻译内容。")

    def _clear_selection(self, message: str) -> None:
        self._current = self._selected_key = None
        self._node_identity = None
        self._navigation_keys = ()
        self.view.set_context_available(False, message)
        self.view.show_entries((), -1)
        self.view.show_entry(None, "")
        self.view.body.setEnabled(False)
        self.view.message.setText(message)

    def refresh(self, _collection=None) -> None:
        if self._closed:
            return
        self._generation += 1
        generation = self._generation
        scope = content_scope(self.context)
        if scope != self._scope:
            self._clear_selection("内容已切换，请双击要编辑的词条。")
        self._scope = scope
        self._index = DialogueIndex((), {})
        collection = self.context.collection
        entries = () if collection is None else tuple(collection)
        self._entry_keys = tuple(entry.identity for entry in entries)
        self.preview.set_editable_entry_keys(self._entry_keys)
        self._context_reason = unavailable_reason(self.context) or "正在建立任务与话题索引…"
        slot = self.context.active_slot
        self.view.source_label.setText("" if slot is None else f"当前内容：{slot.label}")
        self.dialog.setWindowTitle("词条编辑" if slot is None else f"词条编辑 — {slot.label}")
        if self._selected_key in self._entry_keys:
            self._display_entry(self._selected_key)
        elif entries and self._node_identity is not None:
            self.view.set_context_available(False, self._context_reason)
        else:
            self._clear_selection("请双击要编辑的词条。" if entries else "当前内容没有可编辑的词条。")
        if not entries or unavailable_reason(self.context):
            return
        worker = ApiWorker(self._loader.build, entries, plugin=slot.plugin, snapshot=slot.source_snapshot)
        worker.result.connect(lambda result: self._loaded(generation, scope, result))
        worker.error.connect(lambda message: self._failed(generation, message))
        worker.finished.connect(lambda: self._release_worker(worker))
        self._workers.append(worker)
        worker.start()

    def _release_worker(self, worker) -> None:
        self._workers.remove(worker)
        worker.deleteLater()

    def _loaded(self, generation, scope, index) -> None:
        if self._closed or generation != self._generation or scope != content_scope(self.context):
            return
        self._index = index
        self._context_reason = "" if index.quests else "当前插件没有可浏览的任务或对话词条。"
        if self._selected_key is not None:
            self._display_entry(self._selected_key)
        elif (location := self._selected_location()) is not None:
            self._show_context(location)
        elif self._node_identity is not None:
            self._clear_selection("记录已被移除，请重新选择词条。")

    def _failed(self, generation, message) -> None:
        if not self._closed and generation == self._generation:
            self._context_reason = f"任务索引建立失败：{message}。可继续编辑译文，重新选择内容后重试任务树。"
            if self._selected_key is not None:
                self._display_entry(self._selected_key)
            else:
                self.view.set_context_available(False, self._context_reason)

    def open_entry(self, key) -> None:
        if self._closed or not self.preview.isEnabled() or self._scope != content_scope(self.context):
            return
        collection = self.context.collection
        if collection is None or collection.get(key) is None:
            return
        self._node_identity = None
        self._selected_key = key
        self._sync_projection()
        self._display_entry(key)
        show_and_activate(self.dialog)
        self.view.translation.setFocus()

    def _display_entry(self, key) -> None:
        collection = self.context.collection
        entry = None if collection is None else collection.get(key)
        if entry is None:
            self._clear_selection("词条已被移除，请重新选择。")
            return
        self.view.body.setEnabled(True)
        self.view.message.setText("修改后应用译文；未应用草稿会在窗口打开期间保留。")
        location = self._selected_location(key) or self._index.locations.get(key)
        if location is not None and unavailable_reason(self.context) is None:
            self._show_context(location)
        else:
            self._navigation_keys = self._entry_keys
            reason = self._context_reason or "当前词条没有任务关联，可在右侧编辑译文。"
            self.view.set_context_available(False, reason)
            self.view.show_entries((entry,), 0)
            self.select_entry(0)

    def _selected_location(self, key=None):
        for quest_row, quest in enumerate(self._index.quests):
            for topic_row, topic in enumerate(quest.topics):
                if topic.identity == self._node_identity and (key is None or key in topic.entries):
                    return quest_row, topic_row, 0 if key is None else topic.entries.index(key)
        return None

    def _show_context(self, location) -> None:
        quest, topic, row = location
        self.view.body.setEnabled(True)
        self.view.set_context_available(True)
        self.view.show_quests(self._index, quest)
        self.select_quest(quest, topic, row)

    def _sync_projection(self) -> bool:
        if self._projection is None or self.context.collection is None:
            return False
        snapshot = self._projection.snapshot()
        if snapshot is None:
            return False
        states = snapshot.to_dict()["values"].get("entries", ())
        collection = self.context.collection
        projected = apply_variant_projection(collection, states)
        if tuple(projected) == tuple(collection):
            return False
        self.context.collection = projected
        return True

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
            self.view.message.setText(f"{selected.label} · {len(entries)} 条关联词条；修改后应用译文。")
            self.select_entry(row)
        else:
            self._current = self._selected_key = None
            self.view.show_entry(None, "")
            self._show_draft_state()
            self.view.message.setText(f"{selected.label} 没有关联的可翻译词条，可继续选择其他记录。")

    def select_entry(self, row: int) -> None:
        entries = self.view.table_model.entries
        if not 0 <= row < len(entries):
            return
        self._row = row
        entry = entries[row]
        self._selected_key = entry.identity
        self._current = self._drafts.get((self._scope, entry.identity)) or EntryDraft.capture(self.context, entry)
        self.view.show_entry(entry, self._current.text)
        self._show_draft_state()
        position = self._navigation_keys.index(entry.identity)
        self.view.previous_button.setEnabled(position > 0)
        self.view.next_button.setEnabled(position < len(self._navigation_keys) - 1)

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
            key = self._navigation_keys[position]
            self._sync_projection()
            self._display_entry(key)
            self.view.translation.setFocus()

    def apply(self, advance: bool = False) -> None:
        draft = self._current
        if draft is None or not self.preview.isEnabled():
            return
        position = self._navigation_keys.index(self._selected_key)
        next_key = self._navigation_keys[min(position + int(advance), len(self._navigation_keys) - 1)]
        try:
            error = draft.commit(self.context, projection=self._projection)
        except Exception as exc:  # GUI boundary: retain the draft and report the original failure.
            logging.getLogger(__name__).exception("Entry draft commit failed")
            error = f"应用译文失败：{exc}。草稿已保留。"
        if error:
            self.view.message.setText(error)
            return
        self._drafts.pop((draft.scope, draft.before.entry_key), None)
        if content_scope(self.context) == draft.scope:
            self._display_entry(next_key)

    def discard(self) -> None:
        if self._current is None:
            return
        key = self._current.before.entry_key
        self._drafts.pop((self._current.scope, key), None)
        self._sync_projection()
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
        self._drafts.clear()
        return True

    def _dismissed(self) -> None:
        self._clear_selection("请双击要编辑的词条。")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._generation += 1
        self.dialog.hide()
        self.context.collection_changed.disconnect(self.refresh)
        self.context.variant_changed.disconnect(self._version_changed)
        self.preview.entry_edit_requested.disconnect(self.open_entry)
        self.preview.removeEventFilter(self)
