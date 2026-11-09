from __future__ import annotations

from types import SimpleNamespace

from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.persistence.task_store import TaskStore
from transbridge.smart_assistant.tools._entry_upload import upload_entries
from transbridge.smart_assistant.tools.types import ExecutionContext


def test_assistant_selected_upload_retains_full_collection_order_and_local_context(current_project_factory, tmp_path):
    entries = tuple(TranslationEntry(key, key, key, "", 0, "BOOK:FULL") for key in ("a", "b", "c"))
    collection = TranslationEntryCollection(entries)
    project = current_project_factory(collection)
    project.context.task_store = TaskStore(tmp_path / "task-store-v1")
    collection = project.context.collection
    ctx = ExecutionContext(app_context=project.context)
    sent = []

    def upsert_entry(project_id, entry, **kwargs):
        sent.append(entry)
        return SimpleNamespace(remote_id=17, key=entry.key, original=entry.original)

    result = upload_entries({"entry_ids": ["c"]}, ctx, collection, SimpleNamespace(upsert_entry=upsert_entry), 7, None)

    assert result.success
    assert [entry.context for entry in sent] == ["00000002|BOOK:FULL"]
    assert collection.get("c").context == "BOOK:FULL"
    assert result.data["durable"]
    assert not result.data["deferred_associations"]
    assert project.context.collection.get("c").external_refs[0].opaque_id == 17


def test_assistant_upload_preserves_sparse_plugin_order_and_free_text(current_project_factory, tmp_path):
    entries = (
        TranslationEntry("a", "a", "a", "", 0, "INFO:NAM1|", metadata=(("plugin.source_order", 428),)),
        TranslationEntry("b", "b", "b", "", 0, "custom text", metadata=(("plugin.source_order", 512),)),
    )
    collection = TranslationEntryCollection(entries)
    project = current_project_factory(collection)
    project.context.task_store = TaskStore(tmp_path / "task-store-v1")
    collection = project.context.collection
    ctx = ExecutionContext(app_context=project.context)
    sent = []

    def upsert_entry(project_id, entry, **kwargs):
        sent.append(entry)
        return SimpleNamespace(remote_id=len(sent), key=entry.key, original=entry.original)

    result = upload_entries({}, ctx, collection, SimpleNamespace(upsert_entry=upsert_entry), 7, None)

    assert result.success
    assert [entry.context for entry in sent] == ["00000428|INFO:NAM1|", "custom text"]
    assert [entry.context for entry in project.context.collection] == ["INFO:NAM1|", "custom text"]
    assert result.data["durable"]
    assert not result.data["deferred_associations"]
